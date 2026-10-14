"""UserPromptSubmit hook.

Desktop 세션에서 관측할 때가 됐으면(간격은 마지막 사용률과 그 뒤의 추정 비용으로 정한다) 이번 턴에 get_usage를 함께
부르라는 문구를 컨텍스트에 넣는다. 유도 사이에는 모든 세션을 합쳐 쿨다운을 둔다.
"""
import time

try:                                   # 실행 파일(um-hook.exe) 안에서는 패키지 모듈로 불린다
    from . import usage_hook_common as c
except ImportError:                    # Python 스크립트로 직접 실행될 때
    import usage_hook_common as c


def main():
    inp = c.read_input()
    c.debug_dump("user_prompt_submit", {k: inp.get(k) for k in ("session_id", "hook_event_name")})
    if not c.enabled_for_desktop():
        return
    now = time.time()
    is_due, age, cooldown = c.due(now)
    if not is_due or c.marker_age("nudge_at", now) < cooldown:
        return
    c.touch_marker("nudge_at", inp.get("session_id"))
    c.emit({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": c.NUDGE_TEXT.format(age=c.stale_minutes(age)),
        }
    })


if __name__ == "__main__":
    main()
