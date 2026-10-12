"""스풀 JSONL tail. statusline.jsonl(CLI)과 usage_tool.jsonl(Desktop get_usage hook)을 읽는다.

- 바이트 오프셋을 DB(spool_offsets)에 저장해 재시작 후 이어 읽는다
- 파일이 바뀌었거나(st_ino 변경) 잘렸으면(크기 < 오프셋) 처음부터 읽는다
- 개행으로 끝난 줄만 처리한다. 끝의 미완성 줄은 다음 poll까지 남겨 둔다
"""
import json
import logging
from datetime import datetime

from . import db

log = logging.getLogger(__name__)

WINDOW_LABELS = {"5-hour limit": "5h", "Weekly · all models": "7d"}


def round_minute(epoch_s):
    """두 소스의 리셋 시각을 맞춘다. statusline은 정각 정수, get_usage는 ±0.3초 ISO로 온다."""
    return int(round(epoch_s / 60)) * 60


def window_key(label):
    if label in WINDOW_LABELS:
        return WINDOW_LABELS[label]
    if label and label.startswith("Weekly · "):
        return "7d:" + label.removeprefix("Weekly · ").strip().lower()
    return None


def _iso_epoch(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def ingest_statusline(conn, rec, default_host):
    ts, sid = int(rec["ts"]), rec.get("session_id")
    host = rec.get("host") or default_host
    for key, win in (("five_hour", "5h"), ("seven_day", "7d")):
        w = rec.get(key)
        if w and w.get("pct") is not None and w.get("resets_at"):
            db.insert_observation(conn, ts, host, sid, "statusline", win, w["pct"], round_minute(w["resets_at"]))
    warm = rec.get("cache_warm")
    db.upsert_session(
        conn, sid, ts, host=host, client="claude-code", model=rec.get("model"), effort=rec.get("effort"),
        ctx_pct=rec.get("ctx_pct"), cache_hit=rec.get("cache_hit"),
        cache_warm=None if warm is None else int(bool(warm)),
    )
    db.touch_health(conn, host, "statusline", ts)


def ingest_get_usage(conn, rec, default_host):
    ts, sid = int(rec["ts"]), rec.get("session_id")
    host = rec.get("host") or default_host
    db.touch_health(conn, host, "get_usage", ts)
    db.upsert_session(conn, sid, ts, host=host, client="claude-code-desktop", ctx_pct=rec.get("ctx_pct"))
    plan = rec.get("plan") or {}
    if plan.get("status") != "ok":
        return
    for w in plan.get("windows") or []:
        win = window_key(w.get("label"))
        if win and w.get("percentUsed") is not None and w.get("resetsAt"):
            db.insert_observation(conn, ts, host, sid, "get_usage", win, w["percentUsed"],
                                  round_minute(_iso_epoch(w["resetsAt"])))


class SpoolTailer:
    def __init__(self, conn, path, handler, default_host):
        self.conn, self.path, self.handler, self.default_host = conn, path, handler, default_host

    def poll(self):
        """새 완성 줄을 처리하고 처리한 줄 수를 돌려준다."""
        try:
            st = self.path.stat()
        except OSError:
            return 0
        file_id = str(st.st_ino)
        saved_id, offset = db.get_offset(self.conn, self.path)
        if saved_id != file_id or st.st_size < offset:
            offset = 0
        if st.st_size == offset:
            if saved_id != file_id:
                db.set_offset(self.conn, self.path, file_id, offset)
            return 0
        with open(self.path, "rb") as f:
            f.seek(offset)
            chunk = f.read(st.st_size - offset)
        end = chunk.rfind(b"\n")
        if end < 0:
            return 0
        n = 0
        self.conn.execute("BEGIN")
        try:
            for line in chunk[:end].split(b"\n"):
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line.decode("utf-8"))
                    self.handler(self.conn, rec, self.default_host)
                    n += 1
                except (ValueError, KeyError, TypeError) as e:
                    log.warning("스풀 줄 건너뜀 %s: %s", self.path.name, e)
            db.set_offset(self.conn, self.path, file_id, offset + end + 1)
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        return n


def tailers(conn, spool_dir, default_host):
    return [
        SpoolTailer(conn, spool_dir / "statusline.jsonl", ingest_statusline, default_host),
        SpoolTailer(conn, spool_dir / "usage_tool.jsonl", ingest_get_usage, default_host),
    ]
