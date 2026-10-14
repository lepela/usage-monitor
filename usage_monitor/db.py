"""SQLite(WAL) 스키마와 쓰기 함수. 모든 쓰기는 수신기 프로세스 안에서만 일어난다."""
import sqlite3
import time

from . import rollup

SCHEMA = """
CREATE TABLE IF NOT EXISTS api_requests (
  request_id      TEXT PRIMARY KEY,
  ts              INTEGER NOT NULL,      -- epoch ms, UTC
  host            TEXT NOT NULL,
  client          TEXT,                  -- service.name: claude-code | claude-code-desktop
  session_id      TEXT,
  model           TEXT,
  effort          TEXT,                  -- NULL 가능
  speed           TEXT,
  query_source    TEXT,
  input_tokens    INTEGER,
  output_tokens   INTEGER,
  cache_read      INTEGER,
  cache_creation  INTEGER,
  cost_micros_est INTEGER,               -- 추정치, cost_usd_micros
  duration_ms     INTEGER,
  raw_json        TEXT,                  -- user.*·organization.id·process.owner 제거 후
  prompt_id       TEXT,                  -- 사용자 프롬프트 하나에 딸린 요청을 묶는 값(prompt.id)
  ttft_ms         INTEGER                -- 첫 토큰까지 걸린 시간
);
CREATE INDEX IF NOT EXISTS idx_req_ts ON api_requests(ts);

CREATE TABLE IF NOT EXISTS user_prompts (  -- OTel user_prompt 이벤트. 프롬프트 본문은 저장하지 않는다
  prompt_id     TEXT PRIMARY KEY,
  ts            INTEGER NOT NULL,        -- epoch ms, 프롬프트를 보낸 시각
  host          TEXT NOT NULL,
  client        TEXT,
  session_id    TEXT,
  prompt_length INTEGER,                 -- 글자 수
  command_name  TEXT                     -- 슬래시 명령이면 그 이름
);
CREATE INDEX IF NOT EXISTS idx_prompt_ts ON user_prompts(ts);

CREATE TABLE IF NOT EXISTS limit_observations (
  ts          INTEGER NOT NULL,          -- epoch s, 관측 시각
  host        TEXT NOT NULL,
  session_id  TEXT NOT NULL,
  source      TEXT NOT NULL,             -- 'statusline' | 'get_usage'
  window      TEXT NOT NULL,             -- '5h' | '7d' | '7d:<모델>'
  used_pct    INTEGER NOT NULL,
  resets_at   INTEGER NOT NULL,          -- epoch s, 분 단위로 반올림
  PRIMARY KEY (window, ts, host, session_id, source)
);
CREATE INDEX IF NOT EXISTS idx_obs_window ON limit_observations(window, resets_at, ts);

CREATE TABLE IF NOT EXISTS session_state (
  session_id  TEXT PRIMARY KEY,
  host        TEXT,
  client      TEXT,
  last_ts     INTEGER,                   -- epoch s
  model       TEXT,
  effort      TEXT,
  ctx_pct     REAL,
  cache_hit   REAL,
  cache_warm  INTEGER
);

CREATE TABLE IF NOT EXISTS source_health (
  host        TEXT NOT NULL,
  source      TEXT NOT NULL,             -- 'otel' | 'statusline' | 'get_usage'
  last_seen   INTEGER NOT NULL,          -- epoch s
  PRIMARY KEY (host, source)
);

CREATE TABLE IF NOT EXISTS limit_hits (    -- api_error HTTP 429
  ts          INTEGER NOT NULL,          -- epoch ms
  host        TEXT NOT NULL,
  session_id  TEXT,
  error       TEXT
);

CREATE TABLE IF NOT EXISTS spool_offsets (
  path        TEXT PRIMARY KEY,
  file_id     TEXT NOT NULL,             -- 파일 교체 감지용 (st_ino:st_ctime_ns)
  offset      INTEGER NOT NULL
);
"""


def _migrate(conn):
    """이전 버전 DB에 새 열을 더한다. PRAGMA user_version으로 한 번만 한다."""
    if conn.execute("PRAGMA user_version").fetchone()[0] >= 1:
        return
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(api_requests)")}
    for col, typ in (("prompt_id", "TEXT"), ("ttft_ms", "INTEGER")):
        if col not in cols:
            conn.execute(f"ALTER TABLE api_requests ADD COLUMN {col} {typ}")
    conn.execute("""UPDATE api_requests SET
                      prompt_id = json_extract(raw_json, '$.attributes."prompt.id"'),
                      ttft_ms = CAST(json_extract(raw_json, '$.attributes.ttft_ms') AS INTEGER)
                    WHERE prompt_id IS NULL AND raw_json IS NOT NULL""")
    conn.execute("PRAGMA user_version = 1")


def connect(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_req_prompt ON api_requests(prompt_id)")
    rollup.ensure(conn)
    return conn


def upsert_request(conn, r):
    """같은 request_id 재수신은 덮어쓴다(중복 없음)."""
    conn.execute(
        """INSERT INTO api_requests (request_id, ts, host, client, session_id, model, effort, speed,
             query_source, input_tokens, output_tokens, cache_read, cache_creation, cost_micros_est, duration_ms,
             raw_json, prompt_id, ttft_ms)
           VALUES (:request_id,:ts,:host,:client,:session_id,:model,:effort,:speed,:query_source,
            :input_tokens,:output_tokens,:cache_read,:cache_creation,:cost_micros_est,:duration_ms,:raw_json,
            :prompt_id,:ttft_ms)
           ON CONFLICT(request_id) DO UPDATE SET
             ts=excluded.ts, host=excluded.host, client=excluded.client, session_id=excluded.session_id,
             model=excluded.model, effort=excluded.effort, speed=excluded.speed,
             query_source=excluded.query_source, input_tokens=excluded.input_tokens,
             output_tokens=excluded.output_tokens, cache_read=excluded.cache_read,
             cache_creation=excluded.cache_creation, cost_micros_est=excluded.cost_micros_est,
             duration_ms=excluded.duration_ms, raw_json=excluded.raw_json,
             prompt_id=excluded.prompt_id, ttft_ms=excluded.ttft_ms""",
        {"prompt_id": None, "ttft_ms": None, **r},
    )


def upsert_user_prompt(conn, r):
    conn.execute(
        """INSERT INTO user_prompts VALUES (:prompt_id,:ts,:host,:client,:session_id,:prompt_length,:command_name)
           ON CONFLICT(prompt_id) DO UPDATE SET ts=MIN(ts, excluded.ts)""",
        r,
    )


def insert_observation(conn, ts, host, session_id, source, window, used_pct, resets_at):
    conn.execute(
        "INSERT OR IGNORE INTO limit_observations VALUES (?,?,?,?,?,?,?)",
        (ts, host, session_id or "", source, window, int(used_pct), resets_at),
    )


def upsert_session(conn, session_id, last_ts, **fields):
    """주어진 값만 갱신한다. None인 필드는 기존 값을 유지한다."""
    if not session_id:
        return
    cols = ["host", "client", "model", "effort", "ctx_pct", "cache_hit", "cache_warm"]
    vals = {c: fields.get(c) for c in cols}
    conn.execute(
        f"""INSERT INTO session_state (session_id, last_ts, {', '.join(cols)})
            VALUES (:session_id, :last_ts, {', '.join(':' + c for c in cols)})
            ON CONFLICT(session_id) DO UPDATE SET
              last_ts=MAX(COALESCE(session_state.last_ts, 0), excluded.last_ts),
              {', '.join(f'{c}=COALESCE(excluded.{c}, session_state.{c})' for c in cols)}""",
        {"session_id": session_id, "last_ts": last_ts, **vals},
    )


def touch_health(conn, host, source, ts=None):
    conn.execute(
        """INSERT INTO source_health VALUES (?,?,?)
           ON CONFLICT(host, source) DO UPDATE SET last_seen=MAX(last_seen, excluded.last_seen)""",
        (host, source, int(ts or time.time())),
    )


def insert_limit_hit(conn, ts_ms, host, session_id, error):
    conn.execute("INSERT INTO limit_hits VALUES (?,?,?,?)", (ts_ms, host, session_id, error))


def get_offset(conn, path):
    row = conn.execute("SELECT file_id, offset FROM spool_offsets WHERE path=?", (str(path),)).fetchone()
    return (row["file_id"], row["offset"]) if row else (None, 0)


def set_offset(conn, path, file_id, offset):
    conn.execute(
        """INSERT INTO spool_offsets VALUES (?,?,?)
           ON CONFLICT(path) DO UPDATE SET file_id=excluded.file_id, offset=excluded.offset""",
        (str(path), file_id, offset),
    )


def purge_old(conn, retention_days, now=None):
    """보관 기간이 지난 요청·관측·한도 도달 기록을 지운다. 하루 한 번 호출된다.
    집계 표(usage_hourly·usage_daily)는 지우지 않는다."""
    cutoff_s = int((now or time.time()) - retention_days * 86400)
    conn.execute("DELETE FROM api_requests WHERE ts < ?", (cutoff_s * 1000,))
    conn.execute("DELETE FROM limit_observations WHERE ts < ?", (cutoff_s,))
    conn.execute("DELETE FROM limit_hits WHERE ts < ?", (cutoff_s * 1000,))
    conn.execute("DELETE FROM user_prompts WHERE ts < ?", (cutoff_s * 1000,))
