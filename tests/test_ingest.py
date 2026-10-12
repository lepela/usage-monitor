"""OTLP 파싱과 스풀 tail."""
import json
import tempfile
import unittest
from pathlib import Path

from usage_monitor import db, otlp, spool
from tests import fixtures as fx


class OtlpTest(unittest.TestCase):
    def test_api_request_fields_and_pii(self):
        p = fx.payload([fx.api_request("req_1", 1791348885399)])
        [(kind, row)] = list(otlp.parse(p, "DEFAULT"))
        self.assertEqual(kind, "api_request")
        self.assertEqual(row["ts"], 1791348885399)
        self.assertEqual(row["host"], "PC1")
        self.assertEqual(row["client"], "claude-code")
        self.assertEqual((row["input_tokens"], row["output_tokens"], row["cache_read"], row["cache_creation"]),
                         (2, 300, 40000, 1000))
        self.assertEqual(row["cost_micros_est"], 200000)
        raw = json.loads(row["raw_json"])
        flat = json.dumps(raw)
        self.assertNotIn("someone@example.com", flat)
        self.assertNotIn("organization.id", flat)
        self.assertNotIn("user.", flat)

    def test_desktop_without_host_uses_default_and_drops_owner(self):
        p = fx.payload([fx.api_request("req_2", 1, host=None, effort=None, query_source="sdk")],
                       service="claude-code-desktop", host=None)
        [(_, row)] = list(otlp.parse(p, "DEFAULT"))
        self.assertEqual(row["host"], "DEFAULT")
        self.assertEqual(row["client"], "claude-code-desktop")
        self.assertIsNone(row["effort"])
        self.assertNotIn("winuser", row["raw_json"])

    def test_other_events_ignored_and_429_kept(self):
        p = fx.payload([
            fx.other_event("user_prompt", 1, prompt="<REDACTED>"),
            fx.other_event("api_error", 2, status_code=429, error="rate limited"),
            fx.other_event("api_error", 3, error="OAuth refresh token is no longer valid"),
        ])
        rows = list(otlp.parse(p, "D"))
        self.assertEqual([k for k, _ in rows], ["api_error", "api_error"])
        self.assertEqual(rows[0][1]["status_code"], 429)
        self.assertIsNone(rows[1][1]["status_code"])


class SpoolTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.conn = db.connect(self.tmp / "t.db")
        self.path = self.tmp / "statusline.jsonl"

    def tailer(self, path=None, handler=spool.ingest_statusline):
        return spool.SpoolTailer(self.conn, path or self.path, handler, "D")

    def write(self, data, mode="ab", path=None):
        with open(path or self.path, mode) as f:
            f.write(data)

    def count(self):
        return self.conn.execute("SELECT COUNT(*) FROM limit_observations").fetchone()[0]

    def test_partial_line_waits_for_newline(self):
        line = json.dumps(fx.statusline_line(100)).encode()
        self.write(line[:20])
        self.assertEqual(self.tailer().poll(), 0)
        self.write(line[20:] + b"\n")
        self.assertEqual(self.tailer().poll(), 1)
        self.assertEqual(self.count(), 2)  # 5h, 7d

    def test_resume_after_restart_and_truncation(self):
        self.write((json.dumps(fx.statusline_line(100)) + "\n").encode())
        self.tailer().poll()
        self.write((json.dumps(fx.statusline_line(200)) + "\n").encode())
        self.assertEqual(self.tailer().poll(), 1)  # 새 tailer(재시작)가 이어서 한 줄만 읽는다
        self.write((json.dumps(fx.statusline_line(300)) + "\n").encode(), mode="wb")  # 잘림
        self.assertEqual(self.tailer().poll(), 1)
        self.assertEqual(self.count(), 6)

    def test_bad_line_skipped(self):
        self.write(b"not json\n" + (json.dumps(fx.statusline_line(100)) + "\n").encode())
        with self.assertLogs("usage_monitor.spool", level="WARNING"):
            self.assertEqual(self.tailer().poll(), 1)

    def test_get_usage_windows_and_reset_alignment(self):
        gpath = self.tmp / "usage_tool.jsonl"
        self.write((json.dumps(fx.get_usage_line(150)) + "\n").encode(), path=gpath)
        self.write((json.dumps(fx.statusline_line(100)) + "\n").encode())
        self.tailer(gpath, spool.ingest_get_usage).poll()
        self.tailer().poll()
        rows = self.conn.execute(
            "SELECT window, source, used_pct, resets_at FROM limit_observations ORDER BY window, source").fetchall()
        got = {(r["window"], r["source"]): (r["used_pct"], r["resets_at"]) for r in rows}
        self.assertEqual(got[("5h", "get_usage")][1], got[("5h", "statusline")][1])  # 09:40:00.116Z == 1791366000
        self.assertEqual(got[("7d", "get_usage")][1], got[("7d", "statusline")][1])
        self.assertIn(("7d:fable", "get_usage"), got)
        sess = self.conn.execute("SELECT client, ctx_pct FROM session_state WHERE session_id='dt1'").fetchone()
        self.assertEqual((sess["client"], sess["ctx_pct"]), ("claude-code-desktop", 26))

    def test_get_usage_not_ok_records_health_only(self):
        gpath = self.tmp / "usage_tool.jsonl"
        rec = {"ts": 1, "host": "PC1", "session_id": "x", "source": "get_usage", "plan": None, "parse_error": True}
        self.write((json.dumps(rec) + "\n").encode(), path=gpath)
        self.tailer(gpath, spool.ingest_get_usage).poll()
        self.assertEqual(self.count(), 0)
        self.assertEqual(self.conn.execute("SELECT source FROM source_health").fetchone()[0], "get_usage")


if __name__ == "__main__":
    unittest.main()
