"""트레이 프로그램. 수신기·위젯 창·트레이 아이콘을 한 프로세스에서 돌린다.

    pythonw -m usage_monitor run

스레드 구성
  주 스레드    위젯 창(pywebview). 창을 숨겨도 이 루프는 돈다
  수신기       uvicorn. 죽으면 5초 뒤 다시 띄운다(작업 스케줄러 감시를 대신한다)
  트레이       pystray. 아이콘에 5시간 한도 %를 그리고 30초마다 갱신한다
  종료 감시    `usage-monitor quit`이 올리는 이름 있는 이벤트를 기다린다
"""
import ctypes
import json
import logging
import os
import sys
import threading
import time
import urllib.request
import webbrowser

import pystray
import uvicorn
import webview
from PIL import Image, ImageDraw, ImageFont

from . import autostart, config, connect, kiosk, receiver, widget_host

log = logging.getLogger("usage-monitor.app")
APP_MUTEX = "usage-monitor-app"
QUIT_EVENT = "usage-monitor-quit"
ICON_REFRESH_SEC = 30
COLORS = {"good": (12, 163, 12), "warning": (250, 178, 25), "critical": (208, 59, 59), "none": (95, 94, 90)}


def fetch_snapshot(base):
    try:
        with urllib.request.urlopen(base + "/api/snapshot", timeout=3) as r:
            return json.load(r)
    except (OSError, ValueError):
        return None


def make_icon(pct):
    """5시간 한도 %를 숫자로 그린 64×64 아이콘. 값을 모르면 회색 줄표."""
    level = "none" if pct is None else "critical" if pct >= 90 else "warning" if pct >= 70 else "good"
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, 63, 63), radius=12, fill=(26, 26, 25, 255))
    d.rounded_rectangle((6, 50, 57, 58), radius=4, fill=COLORS[level])       # 색만이 아니라 숫자도 함께 준다
    text = "–" if pct is None else str(min(pct, 99) if pct < 100 else 100)
    try:
        font = ImageFont.truetype("segoeuib.ttf", 40 if len(text) < 3 else 30)
    except OSError:
        font = ImageFont.load_default()
    box = d.textbbox((0, 0), text, font=font)
    d.text(((64 - box[2] + box[0]) / 2 - box[0], (48 - box[3] + box[1]) / 2 - box[1]), text,
           font=font, fill=(232, 231, 225, 255))
    return img


def tooltip(snap):
    if not snap:
        return "Usage Monitor · 수신기 응답 없음"
    parts = []
    for key, label in (("5h", "5시간"), ("7d", "7일")):
        lim = snap["limits"].get(key)
        if lim and lim["verdict"] != "reset":
            parts.append(f"{label} {lim['pct']}%")
    return "Usage Monitor · " + (" · ".join(parts) if parts else "한도 관측 없음")


class Receiver:
    """이미 응답하는 수신기가 있으면 그것을 쓰고, 없으면 이 프로세스에서 띄운다."""

    def __init__(self, cfg, base):
        self.cfg, self.base = cfg, base
        self.server, self.stopping = None, False

    def start(self):
        if fetch_snapshot(self.base):
            log.info("이미 떠 있는 수신기를 씁니다 %s", self.base)
            return
        threading.Thread(target=self._serve, name="receiver", daemon=True).start()

    def _serve(self):
        while not self.stopping:
            try:
                self.server = uvicorn.Server(uvicorn.Config(
                    receiver.create_app(self.cfg), host=self.cfg.bind, port=self.cfg.port, log_level="warning"))
                self.server.run()
            except BaseException as e:          # 포트 충돌이면 uvicorn이 SystemExit을 낸다
                log.error("수신기 종료: %r", e)
            if not self.stopping:
                time.sleep(5)

    def stop(self):
        self.stopping = True
        if self.server:
            self.server.should_exit = True


class App:
    def __init__(self):
        self.cfg = config.load()
        receiver.setup_logging(self.cfg)
        log.info("트레이 프로그램 시작 pid=%s %s", os.getpid(), sys.executable)
        self.base = f"http://127.0.0.1:{self.cfg.port}"
        self.settings_path = self.cfg.home / "state" / "app.json"
        self.settings = {"kiosk_on_start": False, **self._load_settings()}
        self.receiver = Receiver(self.cfg, self.base)
        self.widget_state = widget_host.load_state()
        self.window = self.api = self.icon = None
        self.quitting = False

    def _load_settings(self):
        try:
            return json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_settings(self):
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(json.dumps(self.settings), encoding="utf-8")

    # ── 트레이 메뉴 동작 ──────────────────────────────
    def toggle_widget(self, *_):
        widget_host.set_visible(self.api, self.widget_state, not self.widget_state.get("visible", True))

    def set_widget_opacity(self, percent):
        def action(*_):
            widget_host.set_opacity(self.api, self.widget_state, percent)
            self.window.evaluate_js(f"window.syncOpacity && window.syncOpacity({percent})")   # 위젯 버튼의 표시를 맞춘다
        return action

    def open_dashboard(self, *_):
        webbrowser.open(self.base + "/")

    def open_kiosk(self, *_):
        ok, msg = kiosk.open_kiosk(self.base + "/")
        if not ok:
            self.notify(msg)

    def toggle_kiosk_on_start(self, *_):
        self.settings["kiosk_on_start"] = not self.settings["kiosk_on_start"]
        self._save_settings()

    def toggle_autostart(self, *_):
        autostart.disable() if autostart.is_enabled() else autostart.enable()

    def toggle_connection(self, *_):
        if self.connected():
            report = connect.disconnect()
            self.notify("Claude Code 연결을 해제했습니다")
        else:
            report = connect.connect(port=self.cfg.port)
            skipped = report["skipped"]
            self.notify("Claude Code에 연결했습니다" + (" · " + " / ".join(skipped) if skipped else ""))
        log.info("연결 변경: %s", report)

    @staticmethod
    def connected():
        st = connect.status()
        return bool(st["hooks"]) and st["scripts"]

    def notify(self, message):
        try:
            self.icon.notify(message, "Usage Monitor")
        except Exception:
            log.info("알림: %s", message)

    def quit(self, *_):
        if self.quitting:
            return
        self.quitting = True
        self.receiver.stop()
        if self.icon:
            self.icon.stop()
        widget_host.destroy(self.api)

    # ── 구성 요소 ────────────────────────────────────
    def build_icon(self):
        item, sep = pystray.MenuItem, pystray.Menu.SEPARATOR
        menu = pystray.Menu(
            # 아이콘을 누르면 기본 항목이 실행된다. 위젯을 보이고 숨긴다
            item("위젯 보이기", self.toggle_widget, default=True,
                 checked=lambda _: self.widget_state.get("visible", True)),
            item("위젯 투명도", pystray.Menu(*[
                item(f"{p}%", self.set_widget_opacity(p), radio=True,
                     checked=lambda _, p=p: self.widget_state.get("opacity", 100) == p)
                for p in widget_host.OPACITY_LEVELS])),
            item("대시보드 열기 (브라우저)", self.open_dashboard),
            item("키오스크 열기 (바 모니터)", self.open_kiosk),
            sep,
            item("시작할 때 키오스크 열기", self.toggle_kiosk_on_start,
                 checked=lambda _: self.settings["kiosk_on_start"]),
            item("로그인 시 자동 실행", self.toggle_autostart, checked=lambda _: autostart.is_enabled()),
            item("Claude Code에 연결", self.toggle_connection, checked=lambda _: self.connected()),
            sep,
            item("종료", self.quit),
        )
        return pystray.Icon("usage-monitor", make_icon(None), "Usage Monitor", menu)

    def refresh_loop(self):
        kiosk_pending = self.settings["kiosk_on_start"]
        while not self.quitting:
            snap = fetch_snapshot(self.base)
            lim = (snap or {}).get("limits", {}).get("5h")
            pct = lim["pct"] if lim and lim["verdict"] != "reset" else None
            try:
                self.icon.icon = make_icon(pct)
                self.icon.title = tooltip(snap)
            except Exception:
                log.exception("트레이 아이콘 갱신 실패")
            if snap and kiosk_pending:           # 수신기가 응답한 뒤에 띄운다. 먼저 띄우면 오류 페이지에 머문다
                kiosk_pending = False
                self.open_kiosk()
            for _ in range(ICON_REFRESH_SEC if snap else 3):
                if self.quitting:
                    return
                time.sleep(1)

    def quit_watch(self):
        kernel32 = ctypes.windll.kernel32
        event = kernel32.CreateEventW(None, True, False, QUIT_EVENT)
        kernel32.WaitForSingleObject(event, 0xFFFFFFFF)
        self.quit()

    def run(self):
        self.receiver.start()
        widget_host.BASE = self.base
        self.window, self.api = widget_host.create(
            self.widget_state, on_close=lambda: widget_host.set_visible(self.api, self.widget_state, False))
        self.icon = self.build_icon()
        self.icon.run_detached()
        threading.Thread(target=self.refresh_loop, name="tray-refresh", daemon=True).start()
        threading.Thread(target=self.quit_watch, name="quit-watch", daemon=True).start()
        webview.start(widget_host.run, (self.window, self.api, self.widget_state))
        self.quit()                              # 창 루프가 다른 이유로 끝났을 때의 정리


def request_quit():
    """떠 있는 트레이 프로그램에 종료를 알린다. 떠 있지 않으면 False."""
    kernel32 = ctypes.windll.kernel32
    event = kernel32.OpenEventW(0x0002, False, QUIT_EVENT)         # EVENT_MODIFY_STATE
    if not event:
        return False
    kernel32.SetEvent(event)
    kernel32.CloseHandle(event)
    return True


def is_running():
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenMutexW(0x00100000, False, APP_MUTEX)     # SYNCHRONIZE
    if handle:
        kernel32.CloseHandle(handle)
    return bool(handle)


def main():
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, APP_MUTEX)
    if ctypes.windll.kernel32.GetLastError() == 183:               # 이미 떠 있다
        return
    App().run()
    del mutex


if __name__ == "__main__":
    main()
