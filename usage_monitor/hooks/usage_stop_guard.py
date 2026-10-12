"""Stop hook.

유도를 받고도 get_usage가 불리지 않아 여전히 관측할 때면, 턴 종료를 한 번 막고 호출을 시킨다.
- stop_hook_active가 참이면(이미 한 번 막아서 이어진 턴) 다시 막지 않는다
- 강제 호출 사이에는 모든 세션을 합쳐 쿨다운을 둔다. 기록 경로가 고장 나도 비용 상한이 있다
"""
import time

import usage_hook_common as c


def main():
    inp = c.read_input()
    c.debug_dump("stop", {k: inp.get(k) for k in ("session_id", "hook_event_name", "stop_hook_active")})
    if not c.enabled_for_desktop() or inp.get("stop_hook_active"):
        return
    now = time.time()
    is_due, age, cooldown = c.due(now)
    if not is_due or c.marker_age("block_at", now) < cooldown:
        return
    c.touch_marker("block_at", inp.get("session_id"))
    c.emit({"decision": "block", "reason": c.BLOCK_TEXT.format(age=c.stale_minutes(age))})


if __name__ == "__main__":
    main()
