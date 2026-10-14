"""일·시간 단위 집계. 추세 분석과 피벗용 사실 표를 만든다.

요청 원본(api_requests)은 보관 기간(기본 90일) 뒤 지워지지만 집계 표는 지우지 않는다.

usage_hourly   사실 표. 결: 현지 시각 1시간 × host × client × model × effort × query_source.
               합산 가능한 측정값만 둔다. 어느 차원으로 묶어 더해도 맞다
usage_prompts  프롬프트(턴) 하나에 한 줄. 보낸·시작·완료 시각을 epoch ms와 현지 시각 글자(YYYY-MM-DD HH:MM:SS)로 함께 둔다
usage_daily    하루 요약. 세션 수·활동 분처럼 더할 수 없는 값, 그날의 한도, 수집 가동 시간
receiver_runs  수신기가 떠 있던 구간. 요청이 없는 날이 "안 쓴 날"인지 "수집이 안 된 날"인지 가른다

수신기만 쓴다. 매번 최근 며칠을 지우고 다시 계산하므로, 늦게 도착한 요청도 반영되고 여러 번 돌려도 결과가 같다.
날짜와 시는 이 PC의 현지 시각 기준이다.
"""
import time
from datetime import date, datetime, timedelta

SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_hourly (
  day             TEXT NOT NULL,         -- 현지 날짜 YYYY-MM-DD
  hour            INTEGER NOT NULL,      -- 현지 시 0~23
  host            TEXT NOT NULL,
  client          TEXT NOT NULL,         -- 값이 없으면 ''
  model           TEXT NOT NULL,
  effort          TEXT NOT NULL,
  query_source    TEXT NOT NULL,
  requests        INTEGER NOT NULL,
  input_tokens    INTEGER NOT NULL,
  output_tokens   INTEGER NOT NULL,
  cache_read      INTEGER NOT NULL,
  cache_creation  INTEGER NOT NULL,
  cost_micros_est INTEGER NOT NULL,
  duration_ms     INTEGER NOT NULL,
  PRIMARY KEY (day, hour, host, client, model, effort, query_source)
);

CREATE TABLE IF NOT EXISTS usage_daily (
  day               TEXT PRIMARY KEY,
  requests          INTEGER NOT NULL,
  sessions          INTEGER NOT NULL,    -- 요청을 보낸 서로 다른 세션 수
  active_minutes    INTEGER NOT NULL,    -- 요청이 하나라도 있었던 분의 수
  first_ts          INTEGER,             -- epoch s, 그날 첫 요청
  last_ts           INTEGER,             -- epoch s, 그날 마지막 요청
  input_tokens      INTEGER NOT NULL,
  output_tokens     INTEGER NOT NULL,
  cache_read        INTEGER NOT NULL,
  cache_creation    INTEGER NOT NULL,
  cost_micros_est   INTEGER NOT NULL,
  peak_5h_pct       INTEGER,             -- 그날 관측된 5시간 한도 최고값
  last_7d_pct       INTEGER,             -- 그날 마지막 관측의 7일 한도
  limit_obs         INTEGER NOT NULL,    -- 그날 한도 관측 횟수(5h)
  limit_hits        INTEGER NOT NULL,    -- 한도 도달(429) 횟수
  collected_minutes INTEGER,             -- 수신기가 떠 있던 분. NULL이면 가동 기록이 없던 시기
  rolled_at         INTEGER NOT NULL     -- epoch s, 이 줄을 계산한 시각
);

CREATE TABLE IF NOT EXISTS usage_prompts (
  prompt_id       TEXT PRIMARY KEY,
  day             TEXT NOT NULL,         -- 현지 날짜(보낸 시각, 없으면 시작 시각 기준)
  host            TEXT NOT NULL,
  client          TEXT NOT NULL,
  session_id      TEXT,
  submitted_ts    INTEGER,               -- epoch ms, 프롬프트를 보낸 시각(user_prompt 이벤트). 없으면 NULL
  started_ts      INTEGER NOT NULL,      -- epoch ms, 첫 API 요청 시작(ts - duration_ms)
  finished_ts     INTEGER NOT NULL,      -- epoch ms, 마지막 API 응답 완료
  submitted_at    TEXT,                  -- 현지 시각 YYYY-MM-DD HH:MM:SS
  started_at      TEXT NOT NULL,
  finished_at     TEXT NOT NULL,
  elapsed_ms      INTEGER NOT NULL,      -- 완료 - (보낸 시각, 없으면 시작 시각)
  api_ms          INTEGER NOT NULL,      -- API 요청 소요 시간의 합
  first_ttft_ms   INTEGER,               -- 첫 요청의 첫 토큰까지 시간
  requests        INTEGER NOT NULL,
  models          TEXT NOT NULL,         -- 쓴 모델, 쉼표로 구분
  input_tokens    INTEGER NOT NULL,
  output_tokens   INTEGER NOT NULL,
  cache_read      INTEGER NOT NULL,
  cache_creation  INTEGER NOT NULL,
  cost_micros_est INTEGER NOT NULL,
  prompt_length   INTEGER,               -- 글자 수. 본문은 저장하지 않는다
  command_name    TEXT,
  rolled_at       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_prompts_day ON usage_prompts(day);

CREATE TABLE IF NOT EXISTS receiver_runs (
  start_ts   INTEGER PRIMARY KEY,        -- epoch s
  last_seen  INTEGER NOT NULL            -- epoch s, 1분마다 갱신
);
"""

RECOMPUTE_DAYS = 2                      # 오늘과 그 전날을 매번 다시 계산한다


def ensure(conn):
    conn.executescript(SCHEMA)


def day_bounds(day):
    """현지 날짜의 시작·끝 epoch 초."""
    start = time.mktime(day.timetuple())
    end = time.mktime((day + timedelta(days=1)).timetuple())
    return int(start), int(end)


def start_run(conn, now=None):
    now = int(now or time.time())
    conn.execute("INSERT OR REPLACE INTO receiver_runs VALUES (?, ?)", (now, now))
    return now


def touch_run(conn, run_id, now=None):
    conn.execute("UPDATE receiver_runs SET last_seen=? WHERE start_ts=?", (int(now or time.time()), run_id))


def _first_day(conn):
    """집계할 첫날. 아직 집계가 없으면 남아 있는 원본의 첫날부터 한다."""
    if conn.execute("SELECT 1 FROM usage_daily LIMIT 1").fetchone():
        return None
    row = conn.execute(
        """SELECT MIN(t) AS t FROM (
             SELECT MIN(ts) / 1000 AS t FROM api_requests
             UNION ALL SELECT MIN(ts) FROM limit_observations
             UNION ALL SELECT MIN(start_ts) FROM receiver_runs)"""
    ).fetchone()
    return date.fromtimestamp(row["t"]) if row["t"] is not None else None


def _collected_minutes(conn, d0, d1):
    runs = conn.execute("SELECT start_ts, last_seen FROM receiver_runs WHERE start_ts < ? AND last_seen > ?",
                        (d1, d0)).fetchall()
    if not runs and not conn.execute("SELECT 1 FROM receiver_runs WHERE start_ts < ?", (d1,)).fetchone():
        return None                     # 가동 기록을 남기기 전의 날
    return sum(max(0, min(r["last_seen"], d1) - max(r["start_ts"], d0)) for r in runs) // 60


def _daily_row(conn, day, now):
    d0, d1 = day_bounds(day)
    req = conn.execute(
        """SELECT COUNT(*) AS requests, COUNT(DISTINCT session_id) AS sessions,
                  COUNT(DISTINCT ts / 60000) AS active_minutes, MIN(ts) / 1000 AS first_ts, MAX(ts) / 1000 AS last_ts,
                  COALESCE(SUM(input_tokens), 0) AS input_tokens, COALESCE(SUM(output_tokens), 0) AS output_tokens,
                  COALESCE(SUM(cache_read), 0) AS cache_read, COALESCE(SUM(cache_creation), 0) AS cache_creation,
                  COALESCE(SUM(cost_micros_est), 0) AS cost_micros_est
           FROM api_requests WHERE ts >= ? AND ts < ?""",
        (d0 * 1000, d1 * 1000),
    ).fetchone()
    obs = conn.execute(
        "SELECT MAX(used_pct) AS peak, COUNT(*) AS n FROM limit_observations WHERE window='5h' AND ts >= ? AND ts < ?",
        (d0, d1),
    ).fetchone()
    last7 = conn.execute(
        """SELECT MAX(used_pct) AS pct FROM limit_observations
           WHERE window='7d' AND ts = (SELECT MAX(ts) FROM limit_observations WHERE window='7d' AND ts >= ? AND ts < ?)""",
        (d0, d1),
    ).fetchone()
    hits = conn.execute("SELECT COUNT(*) AS n FROM limit_hits WHERE ts >= ? AND ts < ?",
                        (d0 * 1000, d1 * 1000)).fetchone()
    collected = _collected_minutes(conn, d0, d1)
    if not req["requests"] and not obs["n"] and not hits["n"] and not collected:
        return None
    return {**dict(req), "day": day.isoformat(), "peak_5h_pct": obs["peak"], "last_7d_pct": last7["pct"],
            "limit_obs": obs["n"], "limit_hits": hits["n"], "collected_minutes": collected, "rolled_at": int(now)}


LOCAL = "strftime('%Y-%m-%d %H:%M:%S', {} / 1000, 'unixepoch', 'localtime')"


def _rollup_prompts(conn, since_s, now):
    """since_s 뒤에 요청이 하나라도 있는 프롬프트를 원본 전체로 다시 계산한다(자정을 걸친 턴도 한 줄로)."""
    ids = "SELECT DISTINCT prompt_id FROM api_requests WHERE ts >= ? AND prompt_id IS NOT NULL"
    conn.execute(f"DELETE FROM usage_prompts WHERE prompt_id IN ({ids})", (since_s * 1000,))
    conn.execute(
        f"""INSERT INTO usage_prompts
            SELECT r.prompt_id,
                   date(COALESCE(p.ts, r.started) / 1000, 'unixepoch', 'localtime'),
                   r.host, r.client, r.session_id,
                   p.ts, r.started, r.finished,
                   CASE WHEN p.ts IS NULL THEN NULL ELSE {LOCAL.format('p.ts')} END,
                   {LOCAL.format('r.started')}, {LOCAL.format('r.finished')},
                   r.finished - COALESCE(MIN(p.ts, r.started), r.started),
                   r.api_ms, r.first_ttft, r.requests, r.models,
                   r.input_tokens, r.output_tokens, r.cache_read, r.cache_creation, r.cost,
                   p.prompt_length, p.command_name, ?
            FROM (SELECT prompt_id, MIN(host) AS host, COALESCE(MIN(client), '') AS client,
                         MIN(session_id) AS session_id,
                         MIN(ts - COALESCE(duration_ms, 0)) AS started, MAX(ts) AS finished,
                         COALESCE(SUM(duration_ms), 0) AS api_ms,
                         (SELECT ttft_ms FROM api_requests a2 WHERE a2.prompt_id = a.prompt_id ORDER BY ts LIMIT 1)
                           AS first_ttft,
                         COUNT(*) AS requests, GROUP_CONCAT(DISTINCT model) AS models,
                         COALESCE(SUM(input_tokens), 0) AS input_tokens, COALESCE(SUM(output_tokens), 0) AS output_tokens,
                         COALESCE(SUM(cache_read), 0) AS cache_read, COALESCE(SUM(cache_creation), 0) AS cache_creation,
                         COALESCE(SUM(cost_micros_est), 0) AS cost
                  FROM api_requests a WHERE prompt_id IN ({ids}) GROUP BY prompt_id) r
            LEFT JOIN user_prompts p ON p.prompt_id = r.prompt_id""",
        (int(now), since_s * 1000),
    )


def rollup(conn, now=None, recompute_days=RECOMPUTE_DAYS):
    """최근 며칠(처음이면 원본이 있는 첫날부터)을 다시 집계한다. 다시 계산한 날짜 수를 돌려준다."""
    now = now or time.time()
    today = date.fromtimestamp(now)
    start = _first_day(conn) or today - timedelta(days=recompute_days - 1)
    d0, _ = day_bounds(start)
    rows = []
    day = start
    while day <= today:
        row = _daily_row(conn, day, now)
        if row:
            rows.append(row)
        day += timedelta(days=1)
    conn.execute("BEGIN")
    try:
        conn.execute("DELETE FROM usage_hourly WHERE day >= ?", (start.isoformat(),))
        conn.execute(
            """INSERT INTO usage_hourly
               SELECT date(ts / 1000, 'unixepoch', 'localtime'),
                      CAST(strftime('%H', ts / 1000, 'unixepoch', 'localtime') AS INTEGER),
                      host, COALESCE(client, ''), COALESCE(model, ''), COALESCE(effort, ''), COALESCE(query_source, ''),
                      COUNT(*), COALESCE(SUM(input_tokens), 0), COALESCE(SUM(output_tokens), 0),
                      COALESCE(SUM(cache_read), 0), COALESCE(SUM(cache_creation), 0),
                      COALESCE(SUM(cost_micros_est), 0), COALESCE(SUM(duration_ms), 0)
               FROM api_requests WHERE ts >= ?
               GROUP BY 1, 2, 3, 4, 5, 6, 7""",
            (d0 * 1000,),
        )
        # 프롬프트 표가 비어 있으면(이 표를 처음 만든 때) 남은 원본 전체로 채운다
        empty = not conn.execute("SELECT 1 FROM usage_prompts LIMIT 1").fetchone()
        _rollup_prompts(conn, 0 if empty else d0, now)
        conn.execute("DELETE FROM usage_daily WHERE day >= ?", (start.isoformat(),))
        for r in rows:
            conn.execute(
                f"INSERT INTO usage_daily ({', '.join(r)}) VALUES ({', '.join(':' + k for k in r)})", r)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return (today - start).days + 1
