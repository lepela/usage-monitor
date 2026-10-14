"""집계 표(rollup.py)를 읽어 명령 줄 표와 CSV로 낸다. DB는 읽기 전용으로 연다. 쓰기는 수신기만 한다."""
import csv
import sqlite3
import time
import unicodedata
from datetime import date, timedelta

# --by 이름 → usage_hourly 열
DIMENSIONS = {"model": "model", "client": "client", "source": "query_source", "effort": "effort",
              "hour": "hour", "host": "host"}
DIM_LABELS = {"model": "모델", "client": "클라이언트", "source": "요청 종류", "effort": "effort",
              "hour": "시", "host": "PC"}
TABLES = ("hourly", "daily", "prompts")
PROMPT_ROWS = 40                        # --by prompt가 보여 주는 최근 프롬프트 수


class StatsError(Exception):
    pass


def open_ro(db_path):
    if not db_path.exists():
        raise StatsError(f"DB가 없습니다: {db_path}")
    conn = sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='usage_daily'").fetchone():
        raise StatsError("집계 표가 아직 없습니다. 트레이 프로그램을 다시 띄우면(quit 후 run) 수신기가 만듭니다")
    return conn


def since_day(days, today=None):
    """최근 N일의 첫날. 0이면 전체."""
    return "0000-00-00" if days <= 0 else ((today or date.today()) - timedelta(days=days - 1)).isoformat()


# ── 형식 ───────────────────────────────────────────
def compact(n):
    if n is None:
        return "—"
    for unit, size in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(n) >= size:
            return f"{n / size:.1f}{unit}".replace(".0" + unit, unit)
    return str(n)


def usd(micros):
    return f"${(micros or 0) / 1e6:,.2f}"


def pct(v):
    return "—" if v is None else f"{v}%"


def _width(s):
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in s)


def table(headers, rows, left=(0,)):
    """한글 폭을 맞춘 고정폭 표. left에 든 열만 왼쪽 정렬한다."""
    cells = [list(map(str, headers))] + [[str(c) for c in r] for r in rows]
    widths = [max(_width(r[i]) for r in cells) for i in range(len(headers))]

    def line(r):
        out = []
        for i, c in enumerate(r):
            pad = " " * (widths[i] - _width(c))
            out.append(c + pad if i in left else pad + c)
        return "  ".join(out).rstrip()

    rule = "  ".join("-" * w for w in widths)
    return "\n".join([line(cells[0]), rule] + [line(r) for r in cells[1:]])


# ── 표 ─────────────────────────────────────────────
def daily(conn, days):
    rows = conn.execute("SELECT * FROM usage_daily WHERE day >= ? ORDER BY day", (since_day(days),)).fetchall()
    body = [[r["day"], f"{r['requests']:,}", f"{r['sessions']:,}", f"{r['active_minutes']:,}", usd(r["cost_micros_est"]),
             compact(r["input_tokens"]), compact(r["output_tokens"]), compact(r["cache_read"]),
             compact(r["cache_creation"]), pct(r["peak_5h_pct"]), pct(r["last_7d_pct"]),
             "—" if r["collected_minutes"] is None else f"{r['collected_minutes'] / 60:.1f}h"]
            for r in rows]
    out = [table(["일자", "요청", "세션", "활동(분)", "추정 비용", "입력", "출력", "캐시 읽기", "캐시 쓰기",
                  "5h 최고", "7d", "수집"], body)]
    used = [r for r in rows if r["requests"]]
    if used:
        cost = sum(r["cost_micros_est"] for r in used)
        out.append(f"\n사용한 날 {len(used)}일 · 요청 {sum(r['requests'] for r in used):,}건 · "
                   f"추정 비용 {usd(cost)} (하루 평균 {usd(cost / len(used))})")
    return "\n".join(out)


def breakdown(conn, dim, days):
    col = DIMENSIONS[dim]
    rows = conn.execute(
        f"""SELECT {col} AS k, SUM(requests) AS requests, SUM(cost_micros_est) AS cost,
                   SUM(input_tokens) AS input, SUM(output_tokens) AS output, SUM(cache_read) AS cache_read,
                   SUM(duration_ms) AS duration
            FROM usage_hourly WHERE day >= ? GROUP BY {col}
            ORDER BY {'k' if dim == 'hour' else 'cost DESC'}""",
        (since_day(days),),
    ).fetchall()
    total = sum(r["cost"] for r in rows) or 1
    body = [[(f"{r['k']:02d}시" if dim == "hour" else r["k"] or "(없음)"), f"{r['requests']:,}", usd(r["cost"]),
             f"{r['cost'] / total * 100:.1f}%", compact(r["input"]), compact(r["output"]), compact(r["cache_read"]),
             f"{r['duration'] / r['requests'] / 1000:.1f}s"]
            for r in rows]
    return table([DIM_LABELS[dim], "요청", "추정 비용", "비용 비중", "입력", "출력", "캐시 읽기", "평균 소요"], body)


def prompts(conn, days):
    rows = conn.execute(
        "SELECT * FROM usage_prompts WHERE day >= ? ORDER BY finished_ts DESC LIMIT ?",
        (since_day(days), PROMPT_ROWS),
    ).fetchall()
    body = [[r["submitted_at"] or "—", r["started_at"], r["finished_at"], f"{r['elapsed_ms'] / 1000:,.0f}s",
             r["requests"], usd(r["cost_micros_est"]), compact(r["output_tokens"]),
             "Desktop" if r["client"] == "claude-code-desktop" else "CLI" if r["client"] == "claude-code" else r["client"],
             r["command_name"] or ""]
            for r in reversed(rows)]
    total = conn.execute(
        "SELECT COUNT(*) AS n, SUM(cost_micros_est) AS cost, AVG(elapsed_ms) AS avg_ms FROM usage_prompts WHERE day >= ?",
        (since_day(days),),
    ).fetchone()
    out = table(["보냄", "시작", "완료", "걸린 시간", "요청", "추정 비용", "출력", "클라이언트", "명령"], body, left=(0, 1, 2, 7, 8))
    if total["n"]:
        out += (f"\n\n프롬프트 {total['n']:,}개(최근 {min(total['n'], PROMPT_ROWS)}개 표시) · 추정 비용 {usd(total['cost'])} · "
                f"평균 걸린 시간 {total['avg_ms'] / 1000:,.0f}s")
    return out


def render(db_path, by="day", days=14):
    conn = open_ro(db_path)
    try:
        rolled = conn.execute("SELECT MAX(rolled_at) AS t FROM usage_daily").fetchone()["t"]
        period = "전체 기간" if days <= 0 else f"최근 {days}일"
        head = (f"{period} · 집계 {time.strftime('%m-%d %H:%M', time.localtime(rolled)) if rolled else '없음'}"
                " · 비용은 API 정가로 환산한 추정치입니다(구독 청구액 아님)\n")
        body = daily(conn, days) if by == "day" else prompts(conn, days) if by == "prompt" else breakdown(conn, by, days)
        return head + body
    finally:
        conn.close()


def export(db_path, out_path, which="hourly", days=0):
    """집계 표를 CSV로 쓴다. 엑셀이 한글을 바로 읽도록 UTF-8 BOM을 붙인다. 쓴 줄 수를 돌려준다."""
    conn = open_ro(db_path)
    try:
        name = "usage_" + which
        order = {"hourly": "day, hour, host, client, model, effort, query_source", "daily": "day",
                 "prompts": "finished_ts"}[which]
        cur = conn.execute(f"SELECT * FROM {name} WHERE day >= ? ORDER BY {order}", (since_day(days),))
        cols = [d[0] for d in cur.description]
        n = 0
        with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(cols + ["cost_usd_est"])
            for r in cur:
                w.writerow(list(r) + [round(r["cost_micros_est"] / 1e6, 6)])
                n += 1
        return n
    finally:
        conn.close()
