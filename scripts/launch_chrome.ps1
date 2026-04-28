# 진짜 Chrome 을 디버깅 포트(9222) 와 별도 프로필로 띄우는 헬퍼.
#
#   PowerShell -ExecutionPolicy Bypass -File scripts\launch_chrome.ps1
#
# 이렇게 띄운 Chrome 에:
#   - 본인이 직접 코레일 로그인 + 검색까지
#   - 봇은 .env 의 CHROME_CDP_URL=http://127.0.0.1:9222 로 붙어서
#     사용자 진짜 크롬 위에서 동작 (자동화 흔적 거의 없음)
#
# 별도 프로필을 쓰는 이유: 평소 Chrome 과 충돌 없이 (동시에) 띄울 수 있고,
# 실수로 봇이 평소 Chrome 까지 영향 주지 않게.

$ErrorActionPreference = "Stop"

# 1) Chrome 실행파일 경로 자동 탐색
$candidates = @(
  "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
  "$env:ProgramFiles (x86)\Google\Chrome\Application\chrome.exe",
  "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)
$chrome = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $chrome) {
  Write-Host "❌ Chrome 을 찾지 못했습니다. https://www.google.com/chrome/ 에서 설치하세요."
  exit 1
}
Write-Host "✅ Chrome: $chrome"

# 2) 봇 전용 프로필 디렉터리 (없으면 생성)
$Root = (Resolve-Path "$PSScriptRoot\..").Path
$Profile = Join-Path $Root "state\chrome-profile"
New-Item -ItemType Directory -Force -Path $Profile | Out-Null
Write-Host "✅ Profile: $Profile"

# 3) 디버깅 포트 9222 가 이미 사용 중인지 확인
$inUse = Test-NetConnection -ComputerName 127.0.0.1 -Port 9222 -InformationLevel Quiet -WarningAction SilentlyContinue
if ($inUse) {
  Write-Host "ℹ️  포트 9222 가 이미 열려있습니다 (Chrome 이 이미 떠있는 듯). 새로 띄우지 않습니다."
  Write-Host "   .env 에 CHROME_CDP_URL=http://127.0.0.1:9222 추가하고 봇 실행하세요."
  exit 0
}

# 4) Chrome 띄우기
$args = @(
  "--remote-debugging-port=9222"
  "--user-data-dir=$Profile"
  "--no-first-run"
  "--no-default-browser-check"
  "--lang=ko-KR"
  "https://www.korail.com/ticket/login"
)
Start-Process -FilePath $chrome -ArgumentList $args
Write-Host ""
Write-Host "🟢 Chrome 띄움 (포트 9222, 별도 프로필)."
Write-Host ""
Write-Host "다음 단계:"
Write-Host "  1. 띄워진 Chrome 에서 코레일 로그인"
Write-Host "  2. https://www.korail.com/ticket/search/general 로 가서 검색"
Write-Host "  3. .env 에 CHROME_CDP_URL=http://127.0.0.1:9222 추가"
Write-Host "  4. python -m src.main 로 봇 가동"
Write-Host "  5. 텔레그램에서 /refresh"
