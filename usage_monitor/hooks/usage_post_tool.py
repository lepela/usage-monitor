"""PostToolUse hook (matcher: mcp__ccd_session_mgmt__get_usage).

get_usage 결과의 plan과 컨텍스트 %를 스풀에 한 줄로 남긴다. context의 나머지(카테고리별 토큰)는 버린다.
세션 종류와 무관하게 기록한다. 모델이 언제 불렀든 관측은 관측이다.
"""
import json
import time

import usage_hook_common as c


def parse_payload(resp):
    """tool_response 형태가 정해져 있지 않아 dict·문자열·content 블록 목록을 모두 받는다.
    Desktop 2.16120.0 실측은 [{"type": "text", "text": "<JSON>"}]."""
    if isinstance(resp, dict):
        if "plan" in resp:
            return resp
        if "content" in resp:
            return parse_payload(resp["content"])
    if isinstance(resp, list):
        for block in resp:
            found = parse_payload(block.get("text") if isinstance(block, dict) else block)
            if found is not None:
                return found
    if isinstance(resp, str):
        try:
            return parse_payload(json.loads(resp))
        except ValueError:
            return None
    return None


def main():
    if c.DISABLE.exists():
        return
    inp = c.read_input()
    c.debug_dump("post_tool_use", inp)
    payload = parse_payload(inp.get("tool_response"))
    record = {
        "ts": int(time.time()),
        "host": c.host_name(),
        "session_id": inp.get("session_id"),
        "source": "get_usage",
        "plan": payload.get("plan") if payload else None,
    }
    ctx = (payload or {}).get("context") or {}
    # session_id 인자를 주지 않은 호출(self)일 때만 이 세션의 컨텍스트다
    if ctx.get("status") == "ok" and ctx.get("session") == "self":
        record["ctx_pct"] = ctx.get("percentUsed")
    if payload is None:
        record["parse_error"] = True
    c.append_line(c.USAGE_SPOOL, record)


if __name__ == "__main__":
    main()
