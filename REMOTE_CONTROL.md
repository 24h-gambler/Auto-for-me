# 외출 시 핸드폰으로 PC 켜고 KTX 예매

목표: 집 밖에서 핸드폰만으로
1. 집의 PC 깨우기
2. 자동으로 봇+Chrome 시작
3. 텔레그램에서 `/refresh` → 좌석 잡으면 알림
4. 끝나면 `/shutdown` 으로 PC 끄기

가능합니다. 3가지 조각이 필요합니다:

| 조각 | 무엇 | 비용 |
|---|---|---|
| Wake-on-LAN (WoL) | 핸드폰 → 집 PC 깨우기 | 0원 (라우터/PC 만 있으면) |
| 자동 시작 | PC 부팅 → 봇+Chrome 자동 가동 | 0원 (Task Scheduler) |
| `/shutdown` | 텔레그램에서 PC 끄기 | 0원 (이미 구현됨) |

---

## 1. Wake-on-LAN (WoL) 셋업

### 1-1. PC BIOS 에서 WoL 활성화 (한 번만)
1. PC 재시작 → 부팅 시 `Del` / `F2` / `F12` 등 (메인보드별) 눌러서 BIOS 진입
2. **Power Management** / **Advanced** 메뉴 찾기
3. 다음 중 보이는 거 활성화:
   - `Wake on LAN` → Enabled
   - `Power On by PCI-E` → Enabled
   - `Resume by PCI-E Device` → Enabled
   - `ErP Ready` 가 있다면 → Disabled (아니면 WoL 안 됨)
4. Save & Exit

### 1-2. Windows 에서 WoL 허용 (한 번만)
1. `장치 관리자` → `네트워크 어댑터` → 본인 유선 LAN 카드 우클릭 → `속성`
2. **고급** 탭 → 다음 항목 모두 `사용`:
   - `Wake on Magic Packet` → Enabled
   - `Wake on Pattern Match` → Enabled
3. **전원 관리** 탭 → 모두 체크:
   - `절전 모드 해제 시 컴퓨터 활성화` ✅
   - `Magic Packet 으로 컴퓨터 활성화` ✅
4. 확인

> ⚠️ Windows **빠른 시작** 끄기: `제어판 → 전원 옵션 → 전원 단추 작동 설정 → "현재 사용할 수 없는 설정 변경" → "빠른 시작 켜기" 체크 해제`. 빠른 시작 켜져있으면 종료한 PC 가 WoL 안 받음.

### 1-3. 라우터에서 WoL 패스 허용 (외부에서 깨우려면)

**옵션 A — 라우터 자체에 WoL 기능 (가장 쉬움)**
- 본인 라우터 관리 페이지 (`192.168.0.1` 또는 `192.168.1.1`) 접속
- "WOL" / "Wake on LAN" 메뉴 찾기 (대부분 ASUS, iptime, Linksys 지원)
- PC 의 MAC 주소 등록 → 외부에서 라우터로 명령하면 라우터가 PC 깨움
- iptime 의 경우: ezSetup → 시스템 설정 → WOL 설정

**옵션 B — 클라우드 WoL 서비스**
- **iptimeWol** 앱 (안드로이드) / **Wake On Lan** (iOS) — 무료, MAC 주소만 등록
- 단, 외부에서 쓰려면 라우터에 포트 포워딩 (UDP 9번) 필요

**옵션 C — 가장 간단: 항상 켜둠**
- 외출 짧으면 PC 그냥 절전모드 (sleep) 로 두기. 절전은 WoL 더 잘 됨.
- 또는 Slow PC 라 전기료 부담 없으면 그냥 켜놓고 다님.

### 1-4. 핸드폰 앱
- 안드로이드: **Wake On Lan** by Mike Webb (무료)
- iOS: **Wake On Lan** by Mike Webb (무료)
- 또는 라우터 자체 앱 (iptime 등)

설정:
- MAC 주소: PC 의 LAN 카드 MAC (Windows: `ipconfig /all` → "물리적 주소")
- IP: 라우터 외부 IP (또는 DDNS)
- 포트: 보통 9

테스트: PC 꺼놓고 핸드폰 앱에서 "Wake" → PC 가 켜지면 OK.

---

## 2. 부팅 시 봇+Chrome 자동 시작

### Windows
```powershell
cd C:\Users\2idki\Auto-for-me
PowerShell -ExecutionPolicy Bypass -File scripts\autostart\install-windows.ps1
```

→ Task Scheduler 에 두 작업 등록:
- **AutoForMe-Chrome**: 로그온 즉시 Chrome 띄움
- **AutoForMe-Bot**: 로그온 30초 후 봇 시작 (Chrome 먼저 뜨도록)

> 자동로그인 설정도 필수: `Win+R → netplwiz → 본인 계정 선택 → "사용자 이름과 암호를 입력해야..." 체크 해제 → 비밀번호 입력 → 확인`. 안 그러면 부팅 후 로그인 화면에서 멈춤.

### macOS
```bash
bash scripts/autostart/install-macos.sh
```
→ LaunchAgent 등록. 로그인 시 자동 시작.

> 단, 코레일은 사람이 직접 로그인해야 함. 즉 자동 시작은 봇+Chrome 만 띄울 뿐, 본인이 핸드폰으로 PC 깨운 뒤 원격 데스크톱 (Chrome Remote Desktop / TeamViewer / 윈도우 원격 데스크톱) 으로 들어가서 코레일 로그인 + 검색 해야 함. 또는 미리 로그인해두면 그 세션 유지됨.

---

## 3. `/shutdown` 으로 PC 끄기

텔레그램에서:
```
/shutdown        ← 60초 뒤 종료
/shutdown 10     ← 10초 뒤 종료
/shutdown 600    ← 10분 뒤 종료
/reboot          ← 30초 뒤 재시작
```

> macOS 는 `sudo shutdown` 권한 필요. 한 번만 sudoers 에 추가:
> ```
> sudo visudo
> # 아래 줄 추가:
> 본인username ALL=(ALL) NOPASSWD: /sbin/shutdown
> ```

Windows 는 별도 권한 설정 불필요.

---

## 4. 전체 외출 시나리오 (정리)

### 사전 준비 (한 번만)
1. ✅ PC BIOS 에서 WoL 활성화
2. ✅ Windows LAN 카드 → WoL 허용 + 빠른시작 끄기
3. ✅ Windows 자동 로그인 (`netplwiz`)
4. ✅ `install-windows.ps1` 으로 부팅 시 봇+Chrome 자동 시작 등록
5. ✅ 코레일 한 번 로그인 (쿠키가 별도 프로필에 영속됨)
6. ✅ 핸드폰 WoL 앱에 PC MAC 주소 등록

### 외출 시 흐름
1. 📱 핸드폰 WoL 앱 → "Wake" → PC 깨움 (~30초)
2. 📱 텔레그램 봇이 "browser.cdp_connected" 후 reachable
3. 📱 텔레그램 → `/refresh` → 메뉴 → 선택
4. 🤖 봇이 자동으로 새로고침 + 좌석 잡기
5. 📱 텔레그램에 "🚨🚨🚨 좌석 잡았습니다" 알림
6. 📱 (집 도착 후) PC 화면에서 결제수단 선택 → 결제하기
7. 📱 결제 완료 후 텔레그램 → `/shutdown` → PC 종료

---

## 5. 주의 / 한계

| 항목 | 내용 |
|---|---|
| 코레일 로그인 | 새로 로그인 필요 시 PC 화면이 필요 — 원격 데스크톱 / 직접 키보드 |
| 결제 | 결제 비밀번호 / 카드사 인증은 사람이 직접 (보안상) |
| 검색 폼 입력 | 검색은 사람이 직접 (매크로 회피) — 미리 검색해두고 외출 |
| WoL | 절전 / 종료 모두 WoL 받지만, 전원 코드 빠지면 안 됨 |
| 인터넷 끊김 | 라우터 죽으면 WoL 도 안 됨. 라우터는 WoL 위해서라도 안정적으로 |

### 실전 패턴: "외출 직전 검색까지 해두기"
1. 집에서 봇+Chrome 시작
2. 코레일 로그인 + 검색 (결과 페이지까지)
3. PC 절전 모드 (검색 결과 페이지 그대로 유지됨, Chrome 도 그대로)
4. 외출
5. 핸드폰 WoL → PC 깨움
6. 봇 재가동되며 그 페이지에 다시 붙음 (CDP)
7. `/refresh` 시작
8. 잡으면 알림 → 집으로 → 결제

이 패턴이 가장 현실적입니다 — 코레일 로그인/검색이라는 가장 까다로운 부분이 외출 전 사람 손으로 끝나있고, 외출 후엔 단순히 새로고침+클릭만 하면 됨.

---

## 6. 트러블슈팅

| 증상 | 원인 / 해결 |
|---|---|
| WoL 안 됨 | BIOS WoL / 빠른시작 / LAN 카드 설정 셋 다 확인 |
| PC 깨우긴 했는데 봇 안 떠 | 자동로그인 안 돼서 로그인 화면에 머물러있음 → `netplwiz` |
| 봇 떴는데 Chrome 못 찾음 | Chrome 자동시작 안 됨 → AutoForMe-Chrome 작업 확인 |
| `/shutdown` 안 됨 (Mac) | sudoers 설정 필요. 위 3번 참고 |
| 절전모드에서 안 깨어남 | 빠른시작 켜져있을 때 자주 발생. 끄기 |
