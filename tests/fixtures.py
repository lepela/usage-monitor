"""테스트용 페이로드. 형태는 M0 실측(probe/)을 따르고, 계정 식별자는 가짜 값이다."""


def av(v):
    if isinstance(v, bool):
        return {"boolValue": v}
    if isinstance(v, int):
        return {"intValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    return {"stringValue": v}


def attrs(d):
    return [{"key": k, "value": av(v)} for k, v in d.items()]


IDENTITY = {
    "user.id": "hash", "user.email": "someone@example.com", "user.account_uuid": "uuid",
    "user.account_id": "user_x", "organization.id": "org",
}


def api_request(request_id, ts_ms, session="s1", host="PC1", tokens=(2, 300, 40000, 1000),
                cost_micros=200000, effort="high", query_source="repl_main_thread"):
    a = dict(IDENTITY)
    if host:
        a["host.name"] = host
    a.update({
        "session.id": session, "terminal.type": "mingw64", "event.name": "api_request",
        "event.timestamp": "2026-10-07T04:54:45.399Z", "event.sequence": 21,
        "model": "claude-opus-5", "input_tokens": tokens[0], "output_tokens": tokens[1],
        "cache_read_tokens": tokens[2], "cache_creation_tokens": tokens[3],
        "cost_usd": cost_micros / 1e6, "cost_usd_micros": cost_micros, "duration_ms": 6495,
        "request_id": request_id, "speed": "normal", "query_source": query_source,
    })
    if effort:
        a["effort"] = effort
    return {"timeUnixNano": str(ts_ms * 1_000_000), "observedTimeUnixNano": str(ts_ms * 1_000_000),
            "body": {"stringValue": "claude_code.api_request"}, "attributes": attrs(a)}


def other_event(name, ts_ms, session="s1", **extra):
    a = dict(IDENTITY, **{"session.id": session, "event.name": name}, **extra)
    return {"timeUnixNano": str(ts_ms * 1_000_000), "body": {"stringValue": f"claude_code.{name}"},
            "attributes": attrs(a)}


def payload(records, service="claude-code", host="PC1"):
    res = {"service.name": service, "service.version": "2.1.260", "os.type": "windows"}
    if host:
        res["host.name"] = host
    if service == "claude-code-desktop":
        res["process.owner"] = "winuser"
    return {"resourceLogs": [{"resource": {"attributes": attrs(res)},
                              "scopeLogs": [{"scope": {"name": "com.anthropic.claude_code.events"},
                                             "logRecords": records}]}]}


def statusline_line(ts, pct5=10, pct7=20, r5=1791366000, r7=1791694800, session="cli1"):
    return {"ts": ts, "host": "PC1", "session_id": session, "model": "Opus 5", "model_id": "claude-opus-5[1m]",
            "effort": "high", "five_hour": {"pct": pct5, "resets_at": r5},
            "seven_day": {"pct": pct7, "resets_at": r7}, "ctx_pct": 7, "cache_hit": 0.65,
            "cache_warm": True, "cost_usd": 0.45}


def get_usage_line(ts, pct5=4, pct7=16, session="dt1", r5="2026-10-07T09:40:00.116Z",
                   r7="2026-10-11T05:00:00.116Z"):
    return {"ts": ts, "host": "PC1", "session_id": session, "source": "get_usage", "ctx_pct": 26,
            "plan": {"status": "ok", "plan": "Max", "windows": [
                {"label": "5-hour limit", "percentUsed": pct5, "resetsAt": r5},
                {"label": "Weekly · all models", "percentUsed": pct7, "resetsAt": r7},
                {"label": "Weekly · Fable", "percentUsed": 0, "resetsAt": "2026-10-11T05:00:00.000Z"},
            ], "extraUsage": {"enabled": False, "currency": "USD"}}}
