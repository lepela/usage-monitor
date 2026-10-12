"""Claude Code statusLine 명령. 실행: python -S -I <이 파일>

stdin JSON에서 한도·세션 상태를 뽑아 스풀(~/.claude/usage-monitor/spool/statusline.jsonl)에
한 줄 append하고, 상태줄에 짧은 한 줄을 출력한다. 네트워크 호출은 하지 않는다.
시간 예산: Git Bash 경유 200ms 이내. 그래서 json·os·time 외에는 import하지 않는다.
"""
import json
import os
import sys
import time

HOME = os.environ.get("USAGE_MONITOR_HOME") or os.path.join(os.path.expanduser("~"), ".claude", "usage-monitor")
SPOOL = os.path.join(HOME, "spool", "statusline.jsonl")


def window(rl, name):
    w = rl.get(name)
    if not isinstance(w, dict) or w.get("used_percentage") is None:
        return None
    return {"pct": w["used_percentage"], "resets_at": w.get("resets_at")}


def main():
    try:
        d = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    except ValueError:
        sys.stdout.buffer.write("usage-monitor: stdin 오류".encode("utf-8"))
        return
    rl = d.get("rate_limits") or {}
    model = d.get("model") or {}
    cache = d.get("prompt_cache") or {}
    ctx = d.get("context_window") or {}
    rec = {
        "ts": int(time.time()),
        "host": os.environ.get("COMPUTERNAME") or "",
        "session_id": d.get("session_id"),
        "model": model.get("display_name"),
        "model_id": model.get("id"),
        "effort": (d.get("effort") or {}).get("level"),
        "five_hour": window(rl, "five_hour"),
        "seven_day": window(rl, "seven_day"),
        "ctx_pct": ctx.get("used_percentage"),
        "cache_hit": cache.get("hit_ratio"),
        "cache_warm": cache.get("warm"),
        "cost_usd": (d.get("cost") or {}).get("total_cost_usd"),
    }
    line = (json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    try:
        os.makedirs(os.path.dirname(SPOOL), exist_ok=True)
        fd = os.open(SPOOL, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o644)
        try:
            os.write(fd, line)  # 한 번에 쓴다. 도중에 취소돼도 줄이 섞이지 않는다
        finally:
            os.close(fd)
    except OSError:
        pass

    parts = []
    for label, w in (("5h", rec["five_hour"]), ("7d", rec["seven_day"])):
        if w:
            parts.append(f"{label} {w['pct']}%")
    if rec["ctx_pct"] is not None:
        parts.append(f"ctx {rec['ctx_pct']}%")
    if rec["model"]:
        parts.insert(0, rec["model"])
    sys.stdout.buffer.write(" | ".join(parts).encode("utf-8"))


if __name__ == "__main__":
    main()
