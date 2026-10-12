; Inno Setup 스크립트. 실행: iscc packaging\installer.iss /DAppVersion=0.1.0
; 먼저 pyinstaller 로 dist\UsageMonitor\ 를 만들어 둔다.
; 현재 사용자 폴더에 설치하고 관리자 권한을 쓰지 않는다.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{B0B8F0C2-6C1D-4E57-9E0B-6B3E5E2D7A41}
AppName=Usage Monitor
AppVersion={#AppVersion}
AppPublisher=lepela
AppPublisherURL=https://github.com/lepela/usage-monitor
AppSupportURL=https://github.com/lepela/usage-monitor/issues
DefaultDirName={localappdata}\Programs\UsageMonitor
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=UsageMonitor-{#AppVersion}-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; 떠 있는 트레이 프로그램을 설치·제거 전에 닫게 한다 (tray.py 의 APP_MUTEX)
AppMutex=usage-monitor-app
UninstallDisplayIcon={app}\UsageMonitor.exe
UninstallDisplayName=Usage Monitor
VersionInfoVersion={#AppVersion}
VersionInfoProductName=Usage Monitor
LicenseFile=..\LICENSE
; 설치가 시스템에서 바꾸는 것을 설치 전에 알린다
InfoBeforeFile=install-notice.txt

[Languages]
; 한국어 언어 파일은 Inno Setup 버전에 따라 없을 수 있다
#if FileExists(AddBackslash(CompilerPath) + "Languages\Korean.isl")
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
#endif
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "connect"; Description: "Claude Code 설정에 연결하고 로그인 시 자동 실행 (Connect to Claude Code settings and start at sign-in)"

[Files]
Source: "..\dist\UsageMonitor\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{userprograms}\Usage Monitor"; Filename: "{app}\UsageMonitor.exe"; Parameters: "run"

[Run]
; setup: settings.json 연결 + 자동 실행 등록 + 트레이 프로그램 시작
Filename: "{app}\usage-monitor.exe"; Parameters: "setup"; Flags: runhidden; Tasks: connect
; 연결하지 않고 설치했을 때만, 마침 화면에서 프로그램 시작을 고를 수 있다(무인 설치에서는 건너뛴다)
Filename: "{app}\UsageMonitor.exe"; Parameters: "run"; Description: "Usage Monitor 시작 (Start Usage Monitor)"; Flags: nowait postinstall skipifsilent; Tasks: not connect

[UninstallRun]
; 파일을 지우기 전에: 프로그램 종료 + 자동 실행 해제 + settings.json 에서 이 프로그램 항목 제거
Filename: "{app}\usage-monitor.exe"; Parameters: "uninstall"; Flags: runhidden; RunOnceId: "UsageMonitorDisconnect"
