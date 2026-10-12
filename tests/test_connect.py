"""settings.json 연결·해제. 남의 설정을 건드리지 않는지가 요점이다."""
import json
import tempfile
import unittest
from pathlib import Path

from usage_monitor import connect as c

PY = "C:/Python312/python.exe"


class ConnectTest(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        home = tmp / "usage-monitor"
        self.paths = {"settings": tmp / "settings.json", "hooks_dir": home / "hooks",
                      "backup_dir": home / "backup", "manifest": home / "state" / "connect.json"}

    def write(self, data):
        self.paths["settings"].write_text(json.dumps(data), encoding="utf-8")

    def read(self):
        return json.loads(self.paths["settings"].read_text(encoding="utf-8"))

    def test_fresh_connect_then_disconnect_restores_original(self):
        original = {"theme": "dark", "permissions": {"allow": ["Bash(npm run *)"]}}
        self.write(original)
        report = c.connect(self.paths, python=PY)
        s = self.read()
        self.assertEqual(s["theme"], "dark")
        self.assertEqual(s["env"]["OTEL_EXPORTER_OTLP_LOGS_ENDPOINT"], "http://127.0.0.1:4318/v1/logs")
        self.assertIn("usage-monitor/hooks/statusline.py", s["statusLine"]["command"])
        self.assertEqual(sorted(s["hooks"]), ["PostToolUse", "Stop", "UserPromptSubmit"])
        self.assertEqual(s["hooks"]["PostToolUse"][0]["matcher"], c.TOOL_MATCHER)
        self.assertTrue((self.paths["hooks_dir"] / "usage_hook_common.py").exists())
        self.assertTrue(Path(report["backup"]).exists())
        self.assertEqual(report["skipped"], [])
        self.assertEqual(c.status(self.paths), {"otel": "ours", "statusline": "ours", "scripts": True,
                                                "hooks": ["PostToolUse", "Stop", "UserPromptSubmit"]})

        c.disconnect(self.paths)
        self.assertEqual(self.read(), original)
        self.assertFalse(self.paths["hooks_dir"].exists())

    def test_existing_statusline_and_hooks_are_kept(self):
        theirs_hook = {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo mine"}]}
        self.write({"statusLine": {"type": "command", "command": "~/.claude/my-statusline.sh"},
                    "hooks": {"PostToolUse": [theirs_hook], "SessionStart": [{"hooks": [{"type": "command", "command": "x"}]}]}})
        report = c.connect(self.paths, python=PY)
        s = self.read()
        self.assertEqual(s["statusLine"]["command"], "~/.claude/my-statusline.sh")
        self.assertTrue(any("statusLine" in m for m in report["skipped"]))
        self.assertEqual(s["hooks"]["PostToolUse"][0], theirs_hook)           # 남의 hook이 먼저, 그대로
        self.assertEqual(len(s["hooks"]["PostToolUse"]), 2)
        self.assertIn("SessionStart", s["hooks"])
        self.assertEqual(c.status(self.paths)["statusline"], "other")

        c.disconnect(self.paths)
        s = self.read()
        self.assertEqual(s["statusLine"]["command"], "~/.claude/my-statusline.sh")
        self.assertEqual(s["hooks"], {"PostToolUse": [theirs_hook],
                                      "SessionStart": [{"hooks": [{"type": "command", "command": "x"}]}]})

    def test_foreign_otel_is_not_overwritten(self):
        self.write({"env": {"OTEL_LOGS_EXPORTER": "otlp", "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT": "https://collector.corp/v1/logs",
                            "MY_VAR": "1"}})
        report = c.connect(self.paths, python=PY)
        s = self.read()
        self.assertEqual(s["env"]["OTEL_EXPORTER_OTLP_LOGS_ENDPOINT"], "https://collector.corp/v1/logs")
        self.assertNotIn("CLAUDE_CODE_ENABLE_TELEMETRY", s["env"])
        self.assertTrue(any("OTel" in m for m in report["skipped"]))
        c.disconnect(self.paths)
        self.assertEqual(self.read()["env"], {"OTEL_LOGS_EXPORTER": "otlp", "MY_VAR": "1",
                                              "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT": "https://collector.corp/v1/logs"})

    def test_reconnect_is_idempotent_and_replaces_old_paths(self):
        # 다른 위치(예: 소스 폴더)의 스크립트를 가리키는 옛 항목이 있는 상태에서 연결
        old = "python -S D:/dev/usage-monitor/hooks/usage_stop_guard.py"
        self.write({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": old, "timeout": 10}]}]},
                    "statusLine": {"type": "command", "command": "python -S -I D:/dev/usage-monitor/hooks/statusline.py"}})
        c.connect(self.paths, python=PY)
        c.connect(self.paths, python=PY)
        s = self.read()
        self.assertEqual(len(s["hooks"]["Stop"]), 1)
        self.assertNotIn("D:/dev", s["hooks"]["Stop"][0]["hooks"][0]["command"])
        self.assertNotIn("D:/dev", s["statusLine"]["command"])

    def test_env_changed_after_connect_is_left_alone(self):
        self.write({})
        c.connect(self.paths, python=PY)
        s = self.read()
        s["env"]["OTEL_RESOURCE_ATTRIBUTES"] = "host.name=CUSTOM,team=x"
        self.write(s)
        report = c.disconnect(self.paths)
        self.assertEqual(self.read(), {"env": {"OTEL_RESOURCE_ATTRIBUTES": "host.name=CUSTOM,team=x"}})
        self.assertTrue(any("OTEL_RESOURCE_ATTRIBUTES" in m for m in report["skipped"]))

    def test_python_path_with_spaces_is_quoted_and_uses_slashes(self):
        self.write({})
        c.connect(self.paths, python=r"C:\Program Files\Python312\python.exe")
        cmd = self.read()["statusLine"]["command"]
        self.assertTrue(cmd.startswith('"C:/Program Files/Python312/python.exe" -S -I "'))
        self.assertNotIn("\\", cmd)

    def test_exe_mode_registers_hook_exe_and_disconnect_removes_it(self):
        self.write({"theme": "dark"})
        exe = r"C:\Users\someone\AppData\Local\Programs\UsageMonitor\um-hook.exe"
        c.connect(self.paths, python=PY, hook_exe=exe)
        s = self.read()
        self.assertEqual(s["statusLine"]["command"],
                         '"C:/Users/someone/AppData/Local/Programs/UsageMonitor/um-hook.exe" statusline')
        self.assertTrue(s["hooks"]["Stop"][0]["hooks"][0]["command"].endswith('um-hook.exe" stop'))
        self.assertTrue(s["hooks"]["PostToolUse"][0]["hooks"][0]["command"].endswith('um-hook.exe" post'))
        self.assertFalse(self.paths["hooks_dir"].exists())                  # 스크립트는 복사하지 않는다
        self.assertEqual(c.status(self.paths)["statusline"], "ours")
        c.disconnect(self.paths)
        self.assertEqual(self.read(), {"theme": "dark"})

    def test_missing_settings_file(self):
        c.connect(self.paths, python=PY)
        self.assertEqual(c.status(self.paths)["otel"], "ours")
        c.disconnect(self.paths)
        self.assertEqual(self.read(), {})


if __name__ == "__main__":
    unittest.main()
