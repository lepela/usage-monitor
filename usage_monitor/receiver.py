"""FastAPI 수신기. 실행: python -m usage_monitor receiver

- POST /v1/logs      OTLP/JSON 로그 수신 → api_requests UPSERT, 429 → limit_hits
- GET  /api/snapshot 화면 최초 로드용 전체 상태
- GET  /stream       SSE. 변경 시 스냅샷, 10초마다 heartbeat, 30초마다 스냅샷(시간이 흐르며 바뀌는 값용)
- GET  /             정적 대시보드(web/)
백그라운드: 스풀 tail(0.5초), 보관 기간 정리(하루 1회)
"""
import asyncio
import gzip
import json
import logging
import sys
import time
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import config as config_mod
from . import db, metrics, otlp, spool
from .sse import Hub

log = logging.getLogger("usage-monitor")
WEB_DIR = config_mod.WEB_DIR

HEARTBEAT_SEC = 10
SNAPSHOT_REFRESH_SEC = 30
CHANGE_DEBOUNCE_SEC = 0.3
PURGE_EVERY_SEC = 86400


class FreshStaticFiles(StaticFiles):
    """화면 파일을 고치면 키오스크·배경화면·위젯 창이 다음 로드에 바로 새 파일을 받게 한다.
    헤더가 없으면 브라우저가 Last-Modified로 추정해 한동안 캐시본을 쓴다."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


class State:
    def __init__(self, cfg):
        self.cfg = cfg
        self.conn = db.connect(cfg.db_path)
        self.hub = Hub()
        self.dirty = asyncio.Event()
        self.tailers = spool.tailers(self.conn, cfg.spool_dir, cfg.default_host)

    def snapshot(self):
        return metrics.build_snapshot(self.conn)

    def mark_changed(self):
        self.dirty.set()


async def spool_loop(st):
    while True:
        try:
            if sum(t.poll() for t in st.tailers):
                st.mark_changed()
        except Exception:
            log.exception("스풀 tail 실패")
        await asyncio.sleep(st.cfg.spool_poll_sec)


async def publish_loop(st):
    """변경은 0.3초 모아서, 변경이 없어도 30초마다 스냅샷을 보낸다."""
    last_snapshot = 0.0
    last_beat = time.time()
    while True:
        try:
            await asyncio.wait_for(st.dirty.wait(), timeout=1.0)
            await asyncio.sleep(CHANGE_DEBOUNCE_SEC)
        except asyncio.TimeoutError:
            pass
        now = time.time()
        if st.dirty.is_set() or now - last_snapshot >= SNAPSHOT_REFRESH_SEC:
            st.dirty.clear()
            if st.hub.clients:
                st.hub.publish("snapshot", st.snapshot())
            last_snapshot = now
        if now - last_beat >= HEARTBEAT_SEC:
            st.hub.publish("heartbeat", {"now": int(now)})
            last_beat = now


async def purge_loop(st):
    while True:
        try:
            db.purge_old(st.conn, st.cfg.retention_days)
        except Exception:
            log.exception("보관 기간 정리 실패")
        await asyncio.sleep(PURGE_EVERY_SEC)


def create_app(cfg=None):
    cfg = cfg or config_mod.load()

    @asynccontextmanager
    async def lifespan(app):
        st = State(cfg)
        app.state.st = st
        tasks = [asyncio.create_task(f(st)) for f in (spool_loop, publish_loop, purge_loop)]
        log.info("수신기 시작 %s:%s db=%s spool=%s", cfg.bind, cfg.port, cfg.db_path, cfg.spool_dir)
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            st.conn.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.post("/v1/logs")
    async def receive_logs(request: Request):
        if cfg.auth_token and request.headers.get("authorization") != f"Bearer {cfg.auth_token}":
            raise HTTPException(401)
        if "json" not in request.headers.get("content-type", ""):
            raise HTTPException(415, "http/json만 받는다 (OTEL_EXPORTER_OTLP_LOGS_PROTOCOL=http/json)")
        body = await request.body()
        if request.headers.get("content-encoding") == "gzip":
            body = gzip.decompress(body)
        try:
            payload = json.loads(body)
        except ValueError:
            raise HTTPException(400, "JSON 파싱 실패")
        st = request.app.state.st
        n = 0
        st.conn.execute("BEGIN")
        try:
            hosts = set()
            for kind, row in otlp.parse(payload, cfg.default_host):
                hosts.add(row["host"])
                if kind == "api_request":
                    if not row["request_id"] or row["ts"] is None:
                        continue
                    db.upsert_request(st.conn, row)
                    db.upsert_session(st.conn, row["session_id"], row["ts"] // 1000, host=row["host"],
                                      client=row["client"], model=row["model"], effort=row["effort"])
                    n += 1
                elif row.get("status_code") == 429:
                    db.insert_limit_hit(st.conn, row["ts"], row["host"], row["session_id"], row["error"])
                    n += 1
            # 어떤 이벤트든 받았으면 수집 경로는 살아 있다
            for h in hosts or {cfg.default_host}:
                db.touch_health(st.conn, h, "otel")
            st.conn.execute("COMMIT")
        except Exception:
            st.conn.execute("ROLLBACK")
            raise
        if n:
            st.mark_changed()
        return JSONResponse({"partialSuccess": {}})

    @app.get("/api/snapshot")
    async def snapshot(request: Request):
        return request.app.state.st.snapshot()

    @app.get("/stream")
    async def stream(request: Request):
        st = request.app.state.st
        first = f"event: snapshot\ndata: {json.dumps(st.snapshot(), ensure_ascii=False, separators=(',', ':'))}\n\n"
        return StreamingResponse(
            st.hub.stream(first), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/")
    async def index():
        return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    if WEB_DIR.exists():
        app.mount("/web", FreshStaticFiles(directory=WEB_DIR), name="web")
    return app


def setup_logging(cfg):
    """로그를 데이터 폴더의 receiver.log에 남긴다(1MB×3 회전). 콘솔이 있으면 화면에도 낸다."""
    log_path = cfg.home / "receiver.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handlers = [RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")]
    if sys.stdout is None or sys.stderr is None:
        # pythonw(자동 실행)에는 콘솔이 없다. uvicorn이 stderr에 쓰다 죽지 않게 파일로 돌린다
        sys.stdout = sys.stderr = open(log_path.with_suffix(".stdio.log"), "a", encoding="utf-8", buffering=1)
    else:
        handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=handlers)


def main():
    cfg = config_mod.load()
    setup_logging(cfg)
    uvicorn.run(create_app(cfg), host=cfg.bind, port=cfg.port, log_level="warning")


if __name__ == "__main__":
    main()
