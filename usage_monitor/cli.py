"""명령 줄 진입점.

    python -m usage_monitor <명령>        (설치 후에는 usage-monitor <명령>도 된다)

  setup        Claude Code에 연결하고, 로그인 시 자동 실행을 켜고, 프로그램을 띄운다
  uninstall    프로그램을 끝내고, 자동 실행을 끄고, Claude Code 연결을 해제한다
  status       연결·자동 실행·수신기 상태
  run          트레이 프로그램 (수신기 + 위젯 창 + 트레이 아이콘). 명령을 생략하면 이것
  quit         떠 있는 트레이 프로그램을 끝낸다
  connect      Claude Code 설정에 등록만
  disconnect   Claude Code 설정에서 제거만
  receiver     수신기만 콘솔에서 실행 (개발·시험용)
  widget       위젯 창만 실행 (개발·시험용)
  kiosk        바 모니터에 키오스크 열기
"""
import subprocess
import sys
import time
from pathlib import Path


def _print_report(title, report):
    print(title)
    for line in report["done"]:
        print("  완료  ", line)
    for line in report["skipped"]:
        print("  건너뜀", line)
    if report.get("backup"):
        print("  백업  ", report["backup"])


def _start_detached():
    """콘솔 없는 pythonw로 트레이 프로그램을 띄우고 바로 돌아온다."""
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    subprocess.Popen([str(pythonw), "-m", "usage_monitor", "run"],
                     creationflags=0x00000008 | 0x00000200, close_fds=True)   # DETACHED_PROCESS | NEW_PROCESS_GROUP


def cmd_setup():
    from . import autostart, config, connect, tray
    cfg = config.load()
    _print_report("Claude Code 연결", connect.connect(port=cfg.port))
    autostart.enable()
    print("로그인 시 자동 실행: 켬")
    if tray.is_running():
        print("프로그램: 이미 떠 있음")
    else:
        _start_detached()
        print("프로그램: 시작함 (트레이 아이콘을 확인합니다)")
    print("새로 여는 Claude Code 세션부터 사용량이 들어옵니다. 이미 열려 있던 세션은 OTel을 보내지 않습니다.")


def cmd_uninstall():
    from . import autostart, config, connect, tray
    print("프로그램:", "종료를 알림" if tray.request_quit() else "떠 있지 않음")
    autostart.disable()
    print("로그인 시 자동 실행: 끔")
    _print_report("Claude Code 연결 해제", connect.disconnect())
    print(f"기록(DB·스풀·백업)은 {config.default_home()} 에 남겨 두었습니다. 필요 없으면 폴더를 지웁니다.")
    print("패키지 삭제: pip uninstall usage-monitor")


def cmd_status():
    from . import autostart, config, connect, tray
    cfg = config.load()
    st = connect.status()
    label = {"ours": "연결됨", "other": "다른 설정이 있음", "none": "없음"}
    print("Claude Code 연결")
    print("  OTel       ", label[st["otel"]])
    print("  statusLine ", label[st["statusline"]])
    print("  hooks      ", ", ".join(st["hooks"]) or "없음")
    print("  스크립트   ", "설치됨" if st["scripts"] else "없음")
    print("로그인 시 자동 실행:", "켬" if autostart.is_enabled() else "끔")
    print("트레이 프로그램:", "실행 중" if tray.is_running() else "꺼짐")
    snap = tray.fetch_snapshot(f"http://127.0.0.1:{cfg.port}")
    print("수신기:", "응답함" if snap else "응답 없음", f"(127.0.0.1:{cfg.port})")
    for key, lim in ((snap or {}).get("limits") or {}).items():
        when = time.strftime("%m-%d %H:%M", time.localtime(lim["last_obs_ts"]))
        print(f"  {key:9} {lim['pct']:3}%  관측 {when}")


def cmd_run():
    from . import tray
    tray.main()


def cmd_quit():
    from . import tray
    print("종료를 알림" if tray.request_quit() else "떠 있지 않음")


def cmd_connect():
    from . import config, connect
    _print_report("Claude Code 연결", connect.connect(port=config.load().port))


def cmd_disconnect():
    from . import connect
    _print_report("Claude Code 연결 해제", connect.disconnect())


def cmd_receiver():
    from . import receiver
    receiver.main()


def cmd_widget():
    from . import widget_host
    widget_host.main()


def cmd_kiosk():
    from . import config, kiosk
    print(kiosk.open_kiosk(f"http://127.0.0.1:{config.load().port}/")[1])


COMMANDS = {name[4:]: fn for name, fn in list(globals().items()) if name.startswith("cmd_")}


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    name = args[0] if args else "run"
    if name in ("-h", "--help", "help") or name not in COMMANDS:
        print(__doc__.strip())
        sys.exit(0 if name in ("-h", "--help", "help") else 2)
    COMMANDS[name]()


if __name__ == "__main__":
    main()
