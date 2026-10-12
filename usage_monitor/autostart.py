"""로그인 시 자동 실행. 현재 사용자의 Run 레지스트리 값 하나로 한다(관리자 권한 불필요)."""
import sys
import winreg
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
NAME = "UsageMonitor"


def command():
    """콘솔 없는 pythonw로 트레이 프로그램을 띄우는 명령."""
    if getattr(sys, "frozen", False):                      # 실행 파일로 설치됐을 때
        return f'"{Path(sys.executable).with_name("UsageMonitor.exe")}" run'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    return f'"{pythonw}" -m usage_monitor run'


def is_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return bool(winreg.QueryValueEx(key, NAME)[0])
    except OSError:
        return False


def enable():
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, NAME, 0, winreg.REG_SZ, command())


def disable():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, NAME)
    except OSError:
        pass
