"""일·시간 집계와 stats·export 명령."""
import csv
import tempfile
import unittest
from datetime import date
from pathlib import Path

from usage_monitor import db, rollup, stats

D1, D2 = date(2026, 9, 1), date(2026, 9, 2)


class RollupTest(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "t.db"
        self.conn = db.connect(self.path)
        self.d1, _ = rollup.day_bounds(D1)
        self.d2, self.end = rollup.day_bounds(D2)
        self.n = 0

    def req(self, ts, session="s1", model="claude-opus-5-5", source="sdk", cost=1_000_000, out=100, client="claude-code",
            prompt=None, ttft=None):
        self.n += 1
        db.upsert_request(self.conn, dict(
            request_id=f"r{self.n}", ts=ts * 1000, host="PC1", client=client, session_id=session, model=model,
            effort=None, speed=None, query_source=source, input_tokens=10, output_tokens=out, cache_read=1000,
            cache_creation=50, cost_micros_est=cost, duration_ms=2000, raw_json="{}", prompt_id=prompt, ttft_ms=ttft))

    def build(self):
        h = 3600
        self.req(self.d1 + 9 * h, session="a")
        self.req(self.d1 + 9 * h + 10, session="a", model="claude-haiku-5-5", source="prompt_suggestion", cost=10_000)
        self.req(self.d1 + 14 * h, session="b")                       # 다른 시·다른 세션
        self.req(self.d2 + 1 * h, session="c", client="claude-code-desktop")
        for ts, pct in ((self.d1 + 9 * h, 10), (self.d1 + 14 * h, 25), (self.d1 + 15 * h, 20)):
            db.insert_observation(self.conn, ts, "PC1", "a", "get_usage", "5h", pct, self.d1 + 20 * h)
            db.insert_observation(self.conn, ts, "PC1", "a", "get_usage", "7d", pct // 5, self.d1 + 9 * 86400)

    def test_hourly_fact_table_is_additive(self):
        self.build()
        rollup.rollup(self.conn, now=self.end - 60)
        rows = self.conn.execute("SELECT * FROM usage_hourly ORDER BY day, hour, model").fetchall()
        self.assertEqual([(r["day"], r["hour"], r["model"], r["query_source"], r["requests"]) for r in rows], [
            ("2026-09-01", 9, "claude-haiku-5-5", "prompt_suggestion", 1),
            ("2026-09-01", 9, "claude-opus-5-5", "sdk", 1),
            ("2026-09-01", 14, "claude-opus-5-5", "sdk", 1),
            ("2026-09-02", 1, "claude-opus-5-5", "sdk", 1)])
        self.assertEqual(rows[0]["effort"], "")                        # NULL은 ''로 넣어 기본 키에 들어가게 한다
        total = self.conn.execute("SELECT SUM(cost_micros_est) FROM usage_hourly").fetchone()[0]
        self.assertEqual(total, 3_010_000)

    def test_daily_summary(self):
        self.build()
        rollup.rollup(self.conn, now=self.end - 60)
        d = dict(self.conn.execute("SELECT * FROM usage_daily WHERE day='2026-09-01'").fetchone())
        self.assertEqual((d["requests"], d["sessions"], d["active_minutes"]), (3, 2, 2))
        self.assertEqual((d["peak_5h_pct"], d["last_7d_pct"], d["limit_obs"]), (25, 4, 3))
        self.assertEqual(d["cost_micros_est"], 2_010_000)
        self.assertIsNone(d["collected_minutes"])                      # 가동 기록이 없던 시기

    def test_rerun_is_idempotent_and_picks_up_late_requests(self):
        self.build()
        rollup.rollup(self.conn, now=self.end - 60)
        rollup.rollup(self.conn, now=self.end - 60)
        self.assertEqual(self.conn.execute("SELECT SUM(requests) FROM usage_hourly").fetchone()[0], 4)
        self.req(self.d2 + 2 * 3600, session="c")                      # 늦게 도착한 요청
        rollup.rollup(self.conn, now=self.end - 30)
        self.assertEqual(self.conn.execute("SELECT requests FROM usage_daily WHERE day='2026-09-02'").fetchone()[0], 2)

    def test_purge_keeps_rollups(self):
        self.build()
        rollup.rollup(self.conn, now=self.end - 60)
        later = self.end + 100 * 86400
        db.purge_old(self.conn, 90, now=later)
        rollup.rollup(self.conn, now=later)                            # 최근 이틀만 다시 계산한다
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM api_requests").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT SUM(requests) FROM usage_daily").fetchone()[0], 4)

    def test_collected_minutes_from_receiver_runs(self):
        run = rollup.start_run(self.conn, now=self.d2 - 1800)          # 전날 23:30에 떠서
        rollup.touch_run(self.conn, run, now=self.d2 + 3600)           # 다음날 01:00까지
        rollup.rollup(self.conn, now=self.end - 60)
        got = dict(self.conn.execute("SELECT day, collected_minutes FROM usage_daily").fetchall())
        self.assertEqual(got, {"2026-09-01": 30, "2026-09-02": 60})    # 요청이 없어도 수집한 날은 남는다

    def test_stats_render_and_export(self):
        self.build()
        rollup.rollup(self.conn, now=self.end - 60)
        text = stats.render(self.path, "day", 0)
        self.assertIn("2026-09-01", text)
        self.assertIn("$2.01", text)
        by_source = stats.render(self.path, "source", 0)
        self.assertIn("prompt_suggestion", by_source)
        out = self.path.with_name("h.csv")
        self.assertEqual(stats.export(self.path, out, "hourly", 0), 4)
        raw = out.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))               # 엑셀용 BOM
        rows = list(csv.DictReader(raw.decode("utf-8-sig").splitlines()))
        self.assertEqual(rows[0]["cost_usd_est"], "0.01")

    def test_prompt_rows_with_times(self):
        t = self.d2 - 30                                               # 전날 23:59:30에 보내서 자정을 넘겨 끝난 턴
        db.upsert_user_prompt(self.conn, dict(prompt_id="p1", ts=t * 1000, host="PC1", client="claude-code-desktop",
                                              session_id="a", prompt_length=42, command_name=None))
        self.req(t + 5, session="a", prompt="p1", ttft=700, client="claude-code-desktop")
        self.req(t + 40, session="a", prompt="p1", model="claude-haiku-5-5", cost=10_000, client="claude-code-desktop")
        self.req(t + 100, session="b", prompt="p2")                    # user_prompt 이벤트가 없는 턴
        rollup.rollup(self.conn, now=self.end - 60)
        p1 = dict(self.conn.execute("SELECT * FROM usage_prompts WHERE prompt_id='p1'").fetchone())
        self.assertEqual(p1["day"], "2026-09-01")                      # 보낸 날 기준
        self.assertEqual((p1["submitted_ts"], p1["started_ts"], p1["finished_ts"]),
                         (t * 1000, (t + 5) * 1000 - 2000, (t + 40) * 1000))
        self.assertEqual((p1["submitted_at"], p1["finished_at"]), ("2026-09-01 23:59:30", "2026-09-02 00:00:10"))
        self.assertEqual((p1["elapsed_ms"], p1["api_ms"], p1["first_ttft_ms"], p1["requests"]), (40_000, 4000, 700, 2))
        self.assertEqual(set(p1["models"].split(",")), {"claude-opus-5-5", "claude-haiku-5-5"})
        self.assertEqual(p1["prompt_length"], 42)
        p2 = dict(self.conn.execute("SELECT * FROM usage_prompts WHERE prompt_id='p2'").fetchone())
        self.assertIsNone(p2["submitted_at"])
        self.assertEqual(p2["elapsed_ms"], 2000)                       # 보낸 시각이 없으면 첫 요청 시작부터
        rollup.rollup(self.conn, now=self.end - 30)                    # 다시 돌려도 한 줄씩
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM usage_prompts").fetchone()[0], 2)
        text = stats.render(self.path, "prompt", 0)
        self.assertIn("2026-09-02 00:00:10", text)
        out = self.path.with_name("p.csv")
        self.assertEqual(stats.export(self.path, out, "prompts", 0), 2)

    def test_migration_fills_prompt_id_from_raw(self):
        import json
        import sqlite3
        old = self.path.with_name("old.db")
        c = sqlite3.connect(old)
        c.execute("CREATE TABLE api_requests (request_id TEXT PRIMARY KEY, ts INTEGER, host TEXT, client TEXT, "
                  "session_id TEXT, model TEXT, effort TEXT, speed TEXT, query_source TEXT, input_tokens INTEGER, "
                  "output_tokens INTEGER, cache_read INTEGER, cache_creation INTEGER, cost_micros_est INTEGER, "
                  "duration_ms INTEGER, raw_json TEXT)")
        raw = json.dumps({"attributes": {"prompt.id": "px", "ttft_ms": "512"}})
        c.execute("INSERT INTO api_requests (request_id, ts, host, raw_json) VALUES ('r', 1, 'PC1', ?)", (raw,))
        c.commit()
        c.close()
        conn = db.connect(old)
        self.assertEqual(tuple(conn.execute("SELECT prompt_id, ttft_ms FROM api_requests").fetchone()), ("px", 512))
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 1)

    def test_stats_without_rollup_tables(self):
        import sqlite3
        old = self.path.with_name("old.db")
        sqlite3.connect(old).execute("CREATE TABLE api_requests (x)").connection.close()
        with self.assertRaises(stats.StatsError):
            stats.render(old)


if __name__ == "__main__":
    unittest.main()
