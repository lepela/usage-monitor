# usage-monitor

Claude Code(개인 Pro/Max 구독)의 사용량과 한도를 같은 PC에서 실시간으로 보여 주는 Windows용 대시보드입니다.
5시간·7일 한도를 얼마나 썼는지, 언제 리셋되는지, 지금 속도로 리셋 전에 막히는지를 한 화면에 냅니다.

*A local, real-time dashboard for Claude Code usage and rate limits on Windows: tray icon, desktop widget, and a 1920×480 kiosk view. It only listens to data your own Claude Code sends to `127.0.0.1`; it never signs in to your Claude account.*

Anthropic이 만들거나 보증한 프로그램이 아닙니다. Claude와 Claude Code는 Anthropic의 상표입니다.

![1920×480 키오스크 화면](docs/kiosk.png)

![바탕화면 위젯의 세 가지 구성](docs/widget-layouts.png)

## 목차

- [무엇을 보여 주는가](#무엇을-보여-주는가)
- [동작 방식](#동작-방식)
- [요구 사항](#요구-사항)
- [설치](#설치)
- [setup이 바꾸는 것](#setup이-바꾸는-것)
- [사용](#사용)
- [화면 읽는 법](#화면-읽는-법)
- [한도 관측과 그 비용](#한도-관측과-그-비용)
- [저장하는 것과 저장하지 않는 것](#저장하는-것과-저장하지-않는-것)
- [명령](#명령)
- [설정](#설정)
- [제거](#제거)
- [제한 사항](#제한-사항)
- [문제 해결](#문제-해결)
- [개발](#개발)
- [코드 서명 정책](#코드-서명-정책)
- [라이선스](#라이선스)

## 무엇을 보여 주는가

| 항목 | 내용 |
|---|---|
| 5시간·7일 한도 | 사용률(%), 리셋 시각과 남은 시간, 소진 예상 |
| 토큰 | 최근 60분의 분당 토큰. 입력·출력·캐시 읽기·캐시 쓰기로 나눔 |
| 세션 | 현재 모델, effort, 컨텍스트 사용률, 캐시 적중률 |
| 오늘 | 요청 수, API 정가로 환산한 추정 비용 |
| 수집 상태 | 데이터 소스별 마지막 수신 시각과 상태 |

화면은 세 가지입니다.

- **트레이 아이콘** — 5시간 한도 %가 숫자로 나옵니다. 메뉴에서 나머지 화면을 엽니다
- **바탕화면 위젯** — 테두리 없는 항상-위 창(폭 300px). 작게·중간·크게 세 구성
- **키오스크** — 1920×480 바 모니터용 전체 화면

## 동작 방식

```text
Claude Code CLI ──statusline───▶ spool/statusline.jsonl ─┐
Claude Desktop  ──get_usage hook▶ spool/usage_tool.jsonl ─┼─▶ 수신기 ─▶ SQLite ─SSE─▶ 화면
CLI·Desktop     ──OpenTelemetry 로그(OTLP http/json)──────┘   127.0.0.1:4318
```

이 프로그램은 Anthropic 서버에 접속하지 않습니다. Claude 계정에 로그인하지 않고, 자격 증명 파일도 읽지 않습니다.
같은 PC의 Claude Code가 로컬 수신기로 보내는 데이터만 씁니다. 데이터 소스는 셋입니다.

| 소스 | 주는 것 | 어디서 오는가 |
|---|---|---|
| OpenTelemetry 로그 | 요청별 토큰, 모델, 추정 비용 | Claude Code의 공식 텔레메트리 기능. CLI와 Desktop 모두 |
| statusline | 5시간·7일 한도 %, 리셋 시각 | CLI의 상태줄 스크립트가 받는 JSON |
| `get_usage` hook | 5시간·7일 한도 %, 리셋 시각 | Desktop 앱의 내장 도구 결과를 hook이 받아 적음 |

Desktop 앱은 상태줄 스크립트를 실행하지 않습니다. 그래서 Desktop 세션의 한도는 세 번째 경로로 받습니다. 이 경로는 토큰을 조금 씁니다([한도 관측과 그 비용](#한도-관측과-그-비용)).

수신기와 화면은 모두 `127.0.0.1`에만 열립니다. 다른 기기에서는 접속할 수 없습니다.

## 요구 사항

- Windows 11
- Claude Code (CLI 또는 Desktop 앱), 개인 Pro/Max 구독
- WebView2 런타임 — 위젯 창이 씁니다. Windows 11에 기본으로 들어 있습니다
- Chrome 또는 Edge — 키오스크 화면을 쓸 때만
- Python 3.11 이상 — pip으로 설치할 때만. [python.org](https://www.python.org/downloads/windows/) 설치본을 권합니다

## 설치

방법은 둘입니다. 어느 쪽이든 설치 뒤에 **새로 여는** Claude Code 세션부터 사용량이 들어옵니다. 설치 전에 열려 있던 세션은 텔레메트리를 보내지 않습니다.

### 인스톨러

[Releases](https://github.com/lepela/usage-monitor/releases)에서 `UsageMonitor-<버전>-setup.exe`를 받아 실행합니다. Python이 필요 없습니다.
현재 사용자 폴더(`%LOCALAPPDATA%\Programs\UsageMonitor`)에 설치하고 관리자 권한을 쓰지 않습니다.
설치 화면에서 "Claude Code 설정에 연결"을 고르면 연결, 자동 실행 등록, 프로그램 시작까지 합니다.

실행 파일은 아직 코드 서명이 없습니다([코드 서명 정책](#코드-서명-정책)). 서명 전까지는 다음과 같습니다.

- Windows가 "PC 보호" 경고를 띄웁니다. "추가 정보 → 실행"으로 진행합니다
- Smart App Control이 켜진 PC에서는 실행되지 않습니다. 이 경우 아래 pip 방법을 씁니다

설치 없이 써 보려면 같은 곳의 `UsageMonitor-<버전>-portable.zip`을 풀고 `usage-monitor.exe setup`을 실행합니다.

### pip

```bash
pip install git+https://github.com/lepela/usage-monitor.git
```

```bash
python -m usage_monitor setup
```

`setup`은 Claude Code 설정에 연결하고, 로그인 시 자동 실행을 켜고, 트레이 프로그램을 띄웁니다.
서명된 Python으로 돌기 때문에 Smart App Control이 켜진 PC에서도 동작합니다.

## setup이 바꾸는 것

`~/.claude/settings.json`을 `~/.claude/usage-monitor/backup/`에 백업한 뒤 세 가지를 넣습니다.

```jsonc
{
  "env": {
    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
    "OTEL_METRICS_EXPORTER": "none",
    "OTEL_LOGS_EXPORTER": "otlp",
    "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL": "http/json",
    "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT": "http://127.0.0.1:4318/v1/logs",
    "OTEL_RESOURCE_ATTRIBUTES": "host.name=<PC 이름>"
  },
  "statusLine": { "type": "command", "command": "<python> -S -I \"~/.claude/usage-monitor/hooks/statusline.py\"" },
  "hooks": {
    "UserPromptSubmit": [ /* usage_prompt_nudge.py */ ],
    "PostToolUse":      [ /* matcher: mcp__ccd_session_mgmt__get_usage → usage_post_tool.py */ ],
    "Stop":             [ /* usage_stop_guard.py */ ]
  }
}
```

기존 설정은 다음 규칙으로 지킵니다.

- 이미 다른 `statusLine`이 있으면 그대로 두고 알려 줍니다. 이 경우 CLI 세션의 한도 관측은 들어오지 않습니다
- OTel 로그를 이미 다른 곳으로 보내고 있으면 `env`를 건드리지 않고 알려 줍니다. 이 경우 토큰·비용은 수집되지 않습니다
- 다른 hook은 그대로 두고 이 프로그램의 항목만 덧붙입니다
- pip으로 설치했을 때: hook 스크립트를 `~/.claude/usage-monitor/hooks/`에 복사해 두고 설정에는 그 경로를 적습니다. 패키지를 지워도 Claude Code 설정이 깨지지 않습니다
- 인스톨러로 설치했을 때: 설치 폴더의 `um-hook.exe`를 적습니다. 위 예시의 명령 자리에 `"<설치 폴더>/um-hook.exe" statusline` 같은 값이 들어갑니다

그 밖에 바꾸는 것은 로그인 시 자동 실행 항목 하나입니다(`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`의 `UsageMonitor`). 관리자 권한은 쓰지 않습니다.

## 사용

### 트레이 아이콘

아이콘의 숫자는 5시간 한도 사용률입니다. 아래 막대 색은 0~69% 초록, 70~89% 노랑, 90% 이상 빨강입니다. 값을 모르면 회색 줄표가 나옵니다.

| 메뉴 | 동작 |
|---|---|
| 대시보드 열기 | 기본 브라우저로 키오스크 화면을 엽니다. 아이콘 더블클릭과 같습니다 |
| 위젯 보이기 | 바탕화면 위젯을 보이거나 숨깁니다 |
| 위젯 투명도 | 위젯 창의 투명도를 고릅니다 (100 · 85 · 70 · 55 · 40%) |
| 키오스크 열기 (바 모니터) | 1920×480 디스플레이가 있으면 거기에 전체 화면으로 띄웁니다 |
| 시작할 때 키오스크 열기 | 프로그램이 뜰 때 키오스크도 함께 띄웁니다 |
| 로그인 시 자동 실행 | 자동 실행을 켜고 끕니다 |
| Claude Code에 연결 | 설정 연결을 켜고 끕니다 |
| 종료 | 수신기까지 함께 끝냅니다 |

### 바탕화면 위젯

끌어서 옮기면 위치를 기억합니다. 마우스를 올리면 오른쪽 위에 버튼 네 개가 나옵니다.

| 버튼 | 동작 |
|---|---|
| ◐ | 창의 투명도를 100 → 85 → 70 → 55 → 40% 순서로 바꿉니다. 트레이 메뉴의 "위젯 투명도"에서 바로 고를 수도 있습니다 |
| – | 한 줄 띠(200×30)로 접습니다. 5시간·7일 %만 남습니다. 다시 누르면 이전 구성으로 폅니다 |
| ◱ | 구성을 작게 → 중간 → 크게 순서로 바꿉니다 |
| × | 창을 숨깁니다. 트레이 메뉴의 "위젯 보이기"로 다시 보입니다 |

위치, 구성, 투명도는 저장되고 다음 실행 때 그대로 돌아옵니다.

| 구성 | 크기 | 내용 |
|---|---|---|
| 작게 | 300×132 | 한도 %, 막대, 리셋까지 남은 시간 |
| 중간 | 300×192 | 작게 + 소진 예상 + 오늘 추정 비용·요청 수 + 수집 상태 점 |
| 크게 | 300×412 | 중간 + 최근 60분 토큰 차트 + 모델·컨텍스트·캐시 + 수집 상태 문구 |

### 키오스크

1920×480 바 모니터에 맞춘 화면입니다. 전용 브라우저 프로필(`%LOCALAPPDATA%\usage-monitor-kiosk`)로 띄우므로 평소 쓰는 브라우저와 섞이지 않습니다. 닫을 때는 Alt+F4를 누릅니다.

바 모니터가 Windows에서 480×1920 세로로 잡혀 있으면 찾지 못합니다. 디스플레이 설정에서 방향을 가로로 바꿉니다.

10분 동안 활동이 없으면 유휴 화면으로 바뀌고 밝기가 내려갑니다. 번인을 줄이려고 5분마다 화면 전체를 0~2px 옮깁니다.

## 화면 읽는 법

### 소진 예상

한도 옆 문구는 지금 속도로 리셋 전에 100%에 닿는지를 말합니다.

| 문구 | 뜻 |
|---|---|
| 리셋 전 소진 안 함 | 지금 속도면 리셋 시각까지 100%에 닿지 않습니다 |
| HH:MM 소진 예상 | 리셋 전에 100%에 닿는 예상 시각입니다. 경고 표시가 붙습니다 |
| 사용 없음 | 이번 창의 사용률이 0%입니다 |
| 한도 도달 | 사용률이 100%입니다 |
| 리셋됨 · 새 관측 대기 | 리셋 시각이 지났고 그 뒤 관측이 없습니다. 숫자는 비워 둡니다 |

속도는 두 방식 중 하나로 계산하고, 어느 방식인지 아랫줄에 적습니다.

- **추세** — 최근 60분(7일 한도는 최근 6시간)의 관측으로 구한 기울기입니다. 관측이 3개 이상이고 오르는 중일 때 씁니다
- **창 평균** — 추세를 못 쓸 때 씁니다. 현재 사용률을 창이 시작된 뒤 지난 시간으로 나눈 값입니다

같은 줄의 "관측 HH:MM"은 그 값을 마지막으로 받은 시각입니다. 10분 넘게 지났으면 경과 시간도 함께 나옵니다.

### 수집 상태

| 상태 | 뜻 |
|---|---|
| 실시간 | 2분 안에 수신 |
| 30분 내 | 30분 안에 수신 |
| 유휴 | 그보다 오래됨. 세션을 쓰지 않으면 정상입니다 |
| 수집 끊김 의심 | 한도 관측은 들어오는데 OTel이 2분 넘게 없습니다 |

### 알아 둘 점

- 한도 %는 claude.ai 웹, Desktop, Claude Code가 함께 쓰는 값입니다. 토큰 차트는 Claude Code만 셉니다. 웹에서만 대화해도 %는 오릅니다
- 비용은 Claude Code가 API 정가로 계산한 추정치입니다. 구독 청구액이 아닙니다

## 한도 관측과 그 비용

CLI 세션에서는 상태줄 스크립트가 응답마다 한도 값을 받습니다. 추가 비용이 없습니다.

Desktop 세션에서는 값을 받으려면 모델이 `get_usage` 도구를 한 번 불러야 합니다. hook 세 개가 이 일을 맡습니다.

1. 프롬프트를 보낼 때, 관측할 때가 됐으면 "이번 턴에 `get_usage`를 함께 호출"하라는 문구를 넣습니다
2. 모델이 도구를 부르면 그 결과를 스풀 파일에 적습니다
3. 턴이 끝날 때까지 불리지 않았으면 종료를 한 번 막고 호출을 시킵니다

이 호출은 토큰을 씁니다. 실측한 한 번의 관측은 요청 1~2회 추가였고, API 정가로 환산하면 약 $0.03~0.05였습니다. 세션의 컨텍스트가 클수록 늘어납니다. 턴 중간에 도구 호출이 화면에 보입니다.

관측 간격은 마지막 사용률에 따라 달라집니다.

| 마지막 관측값 | 간격 |
|---|---|
| 5시간 80% 이상 또는 7일 90% 이상 | 5분 |
| 5시간 50% 이상 또는 7일 75% 이상 | 20분 |
| 그 밖 | 60분 |

리셋 시각이 지났거나 관측이 없으면 다음 프롬프트에서 바로 관측합니다. 유도와 강제 호출은 각각 모든 세션을 합쳐 5분에 한 번을 넘지 않습니다.

이 동작을 멈추는 방법은 둘입니다.

- 잠시 멈추기 — `~/.claude/usage-monitor/disable` 파일을 만듭니다. 파일을 지우면 다시 동작합니다
- 완전히 빼기 — `python -m usage_monitor disconnect`

## 저장하는 것과 저장하지 않는 것

모든 데이터는 이 PC의 `~/.claude/usage-monitor/`에만 있습니다. 밖으로 보내지 않습니다.

| 저장함 | 저장하지 않음 |
|---|---|
| 요청별 토큰 수, 모델, effort, 추정 비용, 소요 시간 | 프롬프트와 응답 내용 |
| 세션 ID, PC 이름 | 이메일, 계정 ID, 조직 ID, Windows 사용자명 |
| 한도 %와 리셋 시각의 관측 기록 | Claude 자격 증명, 토큰 |

Claude Code의 텔레메트리에는 이메일과 계정 ID가 붙어 옵니다. 수신기가 저장 전에 지웁니다.
프롬프트 본문 로깅 옵션(`OTEL_LOG_USER_PROMPTS` 등)은 켜지 않습니다.

요청과 관측 기록은 90일 뒤 지웁니다.

| 경로 | 내용 |
|---|---|
| `usage.db` | SQLite 데이터베이스 |
| `spool/` | statusline·hook이 적는 줄 단위 파일 |
| `hooks/` | Claude Code가 실행하는 스크립트 복사본 |
| `state/` | 위젯 위치, 프로그램 설정, 연결 기록 |
| `backup/` | `settings.json` 백업 |
| `receiver.log` | 수신기 로그 (1MB씩 3개까지) |

## 명령

pip으로 설치했으면 `python -m usage_monitor <명령>` 또는 `usage-monitor <명령>`입니다.
인스톨러로 설치했으면 설치 폴더의 `usage-monitor.exe <명령>`입니다.

| 명령 | 동작 |
|---|---|
| `setup` | Claude Code에 연결하고, 자동 실행을 켜고, 프로그램을 띄웁니다 |
| `uninstall` | 프로그램을 끝내고, 자동 실행을 끄고, 연결을 해제합니다 |
| `status` | 연결, 자동 실행, 수신기, 마지막 한도 관측을 보여 줍니다 |
| `run` | 트레이 프로그램을 띄웁니다. 명령을 생략하면 이것입니다 |
| `quit` | 떠 있는 트레이 프로그램을 끝냅니다 |
| `connect` / `disconnect` | Claude Code 설정만 등록하거나 제거합니다 |
| `kiosk` | 바 모니터에 키오스크를 엽니다 |
| `receiver` | 수신기만 콘솔에서 실행합니다 |
| `widget` | 위젯 창만 실행합니다 |

## 설정

기본값으로 동작합니다. 바꾸려면 `~/.claude/usage-monitor/config.toml`을 만듭니다. 예시는 [config.example.toml](config.example.toml)에 있습니다. 환경변수가 파일보다 우선합니다.

| 항목 | 환경변수 | 기본값 |
|---|---|---|
| `port` | `USAGE_MONITOR_PORT` | 4318 |
| `bind` | `USAGE_MONITOR_BIND` | 127.0.0.1 |
| `retention_days` | `USAGE_MONITOR_RETENTION_DAYS` | 90 |
| `default_host` | `USAGE_MONITOR_DEFAULT_HOST` | 이 PC 이름 |
| 데이터 폴더 | `USAGE_MONITOR_HOME` | `~/.claude/usage-monitor` |

포트를 바꿨으면 `python -m usage_monitor connect`를 다시 실행해 Claude Code 설정의 주소를 맞춥니다.

## 제거

인스톨러로 설치했으면 Windows 설정의 "설치된 앱"에서 Usage Monitor를 제거합니다. 제거 과정에서 아래 `uninstall`이 자동으로 실행됩니다.

pip으로 설치했으면 다음 순서로 합니다.

```bash
python -m usage_monitor uninstall
```

```bash
pip uninstall usage-monitor
```

`uninstall`은 `settings.json`에서 이 프로그램이 넣은 항목만 지웁니다. `env` 값은 넣은 값 그대로일 때만 지웁니다.
기록은 `~/.claude/usage-monitor/`에 남습니다. 필요 없으면 폴더를 지웁니다.

## 제한 사항

- Windows 전용입니다. 창·트레이·자동 실행이 Windows API에 묶여 있습니다
- 수신기가 꺼져 있는 동안의 토큰 기록은 남지 않습니다. Claude Code는 전송에 실패한 텔레메트리를 따로 보관하지 않습니다. 한도 관측은 파일에 쌓이므로 다시 켜면 이어 읽습니다
- Desktop만 쓰는 동안 한도 값은 최대 60분 전 값일 수 있습니다. 관측 시각을 함께 표시합니다
- 한도 %는 정수로만 옵니다
- 한 PC의 사용량만 봅니다. 여러 PC를 합치는 기능은 없습니다
- `web/wall.html`(배경화면용)과 `wallpaper/`(Wallpaper Engine 프로젝트)는 실험 단계입니다. Wallpaper Engine에서 띄워 확인하지 않았습니다

## 문제 해결

**토큰 차트가 비어 있습니다.**
`setup` 뒤에 새로 연 세션인지 확인합니다. `python -m usage_monitor status`에서 OTel이 "연결됨"이고 수신기가 "응답함"이어야 합니다.

**한도가 "—"이거나 오래된 값입니다.**
CLI에서는 세션의 첫 응답 뒤부터 값이 옵니다. Desktop에서는 다음 관측 간격까지 기다리거나, 대화에서 사용량을 물어 `get_usage`가 불리게 합니다.

**"수집기 연결 끊김"이 뜹니다.**
트레이 프로그램이 꺼져 있습니다. `python -m usage_monitor run`으로 띄웁니다. 로그는 `~/.claude/usage-monitor/receiver.log`에 있습니다.

**키오스크가 "1920x480 디스플레이가 없습니다"라고 합니다.**
모니터가 세로로 잡혀 있는지 디스플레이 설정에서 확인합니다.

**4318 포트를 다른 프로그램이 씁니다.**
`config.toml`에서 `port`를 바꾸고 `connect`를 다시 실행합니다.

## 개발

```bash
git clone https://github.com/lepela/usage-monitor.git
```

```bash
pip install -e .
```

```bash
python -m unittest discover -s tests -t .
```

| 경로 | 내용 |
|---|---|
| `usage_monitor/receiver.py` · `otlp.py` · `spool.py` · `db.py` · `metrics.py` · `sse.py` | 수신기 |
| `usage_monitor/tray.py` · `widget_host.py` · `kiosk.py` · `autostart.py` | 트레이 프로그램과 창 |
| `usage_monitor/connect.py` | `settings.json` 연결·해제 |
| `usage_monitor/hooks/` | Claude Code가 실행하는 스크립트 원본 |
| `usage_monitor/web/` | 화면 파일. 수신기가 요청마다 읽습니다 |
| `packaging/` | 실행 파일(PyInstaller)과 인스톨러(Inno Setup) 빌드 설정, 빌드 동작 확인 스크립트 |
| `.github/workflows/build.yml` | 테스트 → 실행 파일 → 동작 확인 → 인스톨러. 수동 실행과 `v*` 태그 푸시에서만 돕니다. 태그 빌드는 릴리스에 올립니다 |
| `scripts/` | 개발용 도구 |

화면 파일은 고친 뒤 페이지를 다시 열면 반영됩니다. 파이썬 코드를 고친 뒤에는 `quit` 후 `run`으로 다시 띄웁니다.
`usage_monitor/hooks/`를 고친 뒤에는 `connect`를 다시 실행해야 복사본에 반영됩니다.

가짜 데이터로 화면을 볼 때는 실제 데이터와 섞이지 않게 별도 데이터 폴더와 포트를 씁니다.

```bash
set USAGE_MONITOR_HOME=%TEMP%\usage-monitor-fake&& set USAGE_MONITOR_PORT=4319&& python -m usage_monitor receiver
```

```bash
python scripts/fake_feed.py --url http://127.0.0.1:4319 --home %TEMP%\usage-monitor-fake
```

## 코드 서명 정책

배포하는 실행 파일과 인스톨러는 이 저장소의 소스에서 GitHub Actions(`.github/workflows/build.yml`)로만 만듭니다. 손으로 만든 실행 파일은 배포하지 않습니다.

현재 상태: **서명 없음.** 오픈소스 프로젝트용 무료 코드 서명을 [SignPath Foundation](https://signpath.org/)에 신청할 예정입니다. 승인되면 이 절에 서명 제공자를 적고 릴리스를 서명본으로 바꿉니다.

| 역할 | 담당 |
|---|---|
| 커밋·리뷰 | [lepela](https://github.com/lepela) |
| 릴리스 승인 | [lepela](https://github.com/lepela) |

개인 정보: 이 프로그램은 사용자나 설치·운영하는 사람이 명시적으로 요청하지 않는 한 어떤 정보도 다른 네트워크 시스템으로 보내지 않습니다. 수집 범위는 [저장하는 것과 저장하지 않는 것](#저장하는-것과-저장하지-않는-것)에 있습니다.

*This program will not transfer any information to other networked systems unless specifically requested by the user or the person installing or operating it.*

## 라이선스

[Mozilla Public License 2.0](https://www.mozilla.org/MPL/2.0/). 전문은 [LICENSE](LICENSE)에 있습니다.

© 2026 lepela

- 사용, 복제, 배포, 수정, 상업적 이용을 할 수 있습니다
- 이 저장소의 파일을 고쳐서 배포할 때는 그 파일을 같은 라이선스로 공개합니다
- 직접 만든 다른 파일과 묶어 배포하는 것은 그 파일의 라이선스를 제한하지 않습니다
