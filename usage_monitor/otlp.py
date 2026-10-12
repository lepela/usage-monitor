"""OTLP/JSON 로그 페이로드 파서.

구조: resourceLogs → scopeLogs → logRecords → attributes. 속성 값은 AnyValue로 감싸져 온다.
실측: event.name은 접두사 없는 'api_request', intValue는 문자열로 올 수 있다,
Desktop은 host.name이 없다, 모든 레코드에 계정 식별자가 붙는다.
"""
import json
from datetime import datetime

PII_PREFIXES = ("user.",)
PII_KEYS = {"organization.id", "process.owner"}


def unwrap(v):
    if not isinstance(v, dict) or not v:
        return None
    kind, x = next(iter(v.items()))
    if kind == "intValue":
        return int(x)
    if kind == "doubleValue":
        return float(x)
    if kind == "arrayValue":
        return [unwrap(i) for i in (x or {}).get("values", [])]
    if kind == "kvlistValue":
        return attrs_to_dict((x or {}).get("values", []))
    return x


def attrs_to_dict(attrs):
    return {a["key"]: unwrap(a.get("value")) for a in attrs or [] if "key" in a}


def strip_pii(d):
    return {k: v for k, v in d.items() if not k.startswith(PII_PREFIXES) and k not in PII_KEYS}


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _ts_ms(record, attrs):
    ns = _int(record.get("timeUnixNano")) or _int(record.get("observedTimeUnixNano"))
    if ns:
        return ns // 1_000_000
    ts = attrs.get("event.timestamp")
    if ts:
        return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1000)
    return None


def _event_name(record, attrs):
    name = attrs.get("event.name") or unwrap(record.get("body")) or ""
    return name.removeprefix("claude_code.")


def parse(payload, default_host):
    """페이로드에서 ('api_request', row) / ('api_error', row) 튜플을 차례로 낸다."""
    for rl in payload.get("resourceLogs") or []:
        resource = attrs_to_dict((rl.get("resource") or {}).get("attributes"))
        for sl in rl.get("scopeLogs") or []:
            for record in sl.get("logRecords") or []:
                attrs = attrs_to_dict(record.get("attributes"))
                name = _event_name(record, attrs)
                if name not in ("api_request", "api_error"):
                    continue
                host = attrs.get("host.name") or resource.get("host.name") or default_host
                base = {
                    "ts": _ts_ms(record, attrs),
                    "host": host,
                    "client": resource.get("service.name"),
                    "session_id": attrs.get("session.id"),
                }
                if name == "api_error":
                    yield name, dict(base, status_code=_int(attrs.get("status_code")),
                                     error=attrs.get("error"))
                    continue
                cost_micros = _int(attrs.get("cost_usd_micros"))
                if cost_micros is None and attrs.get("cost_usd") is not None:
                    cost_micros = round(float(attrs["cost_usd"]) * 1_000_000)
                raw = {"resource": strip_pii(resource), "attributes": strip_pii(attrs)}
                yield name, dict(
                    base,
                    request_id=attrs.get("request_id") or attrs.get("client_request_id"),
                    model=attrs.get("model"),
                    effort=attrs.get("effort"),
                    speed=attrs.get("speed"),
                    query_source=attrs.get("query_source"),
                    input_tokens=_int(attrs.get("input_tokens")),
                    output_tokens=_int(attrs.get("output_tokens")),
                    cache_read=_int(attrs.get("cache_read_tokens")),
                    cache_creation=_int(attrs.get("cache_creation_tokens")),
                    cost_micros_est=cost_micros,
                    duration_ms=_int(attrs.get("duration_ms")),
                    raw_json=json.dumps(raw, ensure_ascii=False, separators=(",", ":")),
                )
