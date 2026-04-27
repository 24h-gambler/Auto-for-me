# 운영 가이드 — 어디서 돌릴 것인가?

## 권장: PC 켜져 있을 때만 돌리기 (3-Tier 하이브리드)

서버를 24/7 돌리지 않고도 사실상 손실 없이 운영할 수 있습니다.

```
[Tier 1] 사용자 PC (켜져있을 때만)        — Telegram 봇, Playwright, 실제 예매·쿠팡 분석
[Tier 2] GitHub Actions cron (무료)       — PC 꺼져있을 때 KTX 좌석만 감시 (예매 X)
[Tier 3] Telegram 자체가 메시지 큐         — PC 꺼진 동안 명령은 누적, PC 켜지면 처리
```

이걸 가능하게 하는 코드 측 장치:
- `state/state.db` (SQLite) 에 작업 영속 → PC 재부팅에도 진행 중인 KTX 폴링 자동 재개
- `watch_list.json` 에 감시 대상 동기화 → GitHub Actions 워커가 5~10분마다 검사
- 좌석 발견 시 텔레그램 알림 → PC 켜져있다면 봇이 즉시 결제 단계까지, 꺼져있다면 사용자가 PC 켜기

### PC-only 워크플로 (실전)

1. **봇을 OS 부팅 시 자동 실행**되게 등록 (아래 Autostart 섹션).
2. PC 를 평소처럼 사용. 끄고 켜고 자유.
3. KTX 예매가 필요하면 자유 채팅으로 부탁: "이번 주말 부산 KTX 잡아줘 토 9시쯤".
4. 폴링이 길어질 것 같으면 **/watch** 로 클라우드 감시 등록 후 `git push`. PC 꺼도 GitHub Actions 가 좌석 풀림을 감시.
5. 좌석 발견 알림이 오면 PC 를 켜기만 하면 됨 — 봇이 부팅 시 자동 시작 → SQLite 에서 작업 재개 → 그동안 누적된 텔레그램 명령 처리.

---

## 그래도 24/7 서버에서 돌리고 싶다면

이 봇은 텔레그램 long-poll 방식이라 항상 켜져 있어야 명령을 받습니다. 그래서 "텔레그램 → Codespace wake" 같은 모델은 잘 안 맞습니다 (콜드 스타트 ~30초 + 30분 무활동 종료 + 60h/월 한도).

아래는 잘 굴러가는 24/7 옵션들과, 굳이 Codespaces 를 쓰고 싶을 때의 우회법입니다.

---

## 옵션 비교표

| 옵션 | 비용 | 안정성 | 셋업 | 추천 상황 |
|---|---|---|---|---|
| **집의 노트북 / 라즈베리파이 4 (4GB+)** | 전기료 | ★★★★★ | 쉬움 | 평소 운영용. 가장 추천. |
| **Oracle Cloud Always Free** (ARM Ampere VM, 24GB RAM 무료) | 영구 무료 | ★★★★ | 가입 승인 까다로움 | 인프라 가진 사람이 없을 때 |
| **Fly.io 무료 머신 3대** | 무료(카드 등록 필요) | ★★★★ | 쉬움 | 안정적 클라우드 운영 |
| **GitHub Student Pack → DigitalOcean $200** | 12개월 무료(크레딧) | ★★★★★ | 쉬움 | 학생 인증 가능자 |
| **GitHub Codespaces (수동 시작)** | 60h/월 무료 (Pro 180h) | ★★★ | 매우 쉬움 | 예매 오픈일에만 잠깐 돌릴 때 |
| **Codespaces auto-wake (CF Worker → GH API)** | 무료 | ★★ | 어려움 | 고급 사용자, 콜드 스타트 30초 감수 |

> **나의 추천 경로**: 안 쓰는 노트북이 있다 → 그걸로 끝. 없다 → Oracle Free 가입 시도 → 실패하면 Fly.io.

---

## 1) 집 노트북 / RPi 운영 (가장 단순)

```bash
git clone <repo> Auto-for-me && cd Auto-for-me
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
playwright install-deps          # apt 패키지 자동 설치 (linux)
cp .env.example .env && nano .env
cp config.example.yaml config.yaml
```

`systemd` 서비스로 등록 (재부팅에도 자동 시작):

```ini
# /etc/systemd/system/auto-for-me.service
[Unit]
Description=Auto-for-me bot
After=network-online.target

[Service]
User=YOUR_USER
WorkingDirectory=/home/YOUR_USER/Auto-for-me
ExecStart=/home/YOUR_USER/Auto-for-me/.venv/bin/python -m src.main
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now auto-for-me
sudo journalctl -u auto-for-me -f   # 로그 확인
```

노트북 덮개 닫아도 안 꺼지게:
```bash
sudo sed -i 's/^#HandleLidSwitch=.*/HandleLidSwitch=ignore/' /etc/systemd/logind.conf
sudo systemctl restart systemd-logind
```

---

## 2) Oracle Cloud Always Free

1. https://www.oracle.com/cloud/free/ 가입 → 신용카드 인증 (과금 안 됨)
2. **Compute → Create Instance** → Image: Ubuntu 22.04, Shape: **VM.Standard.A1.Flex (Ampere ARM)**
   - **OCPU 4, Memory 24GB** 까지 항상 무료
3. SSH 접속 후 위 1) 의 명령들을 그대로 실행
4. 보안 리스트에서 outbound 만 열려 있으면 됨 (텔레그램은 outbound)

---

## 3) Fly.io

```bash
fly launch --no-deploy            # 앱 이름만 만들고
fly secrets set ANTHROPIC_API_KEY=... TELEGRAM_BOT_TOKEN=... KORAIL_ID=... KORAIL_PW=...
fly volumes create state --size 1 --region nrt  # 쿠키 영속화
fly deploy
```

`fly.toml` 예시:

```toml
[build]
  dockerfile = "Dockerfile"

[mounts]
  source = "state"
  destination = "/app/state"

[[vm]]
  cpu_kind = "shared"
  cpus = 1
  memory_mb = 1024     # Playwright 가 1GB 안에서 빠듯함. 256MB 안 됨.
```

Dockerfile (간단 예시):

```dockerfile
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "-m", "src.main"]
```

---

## 4) GitHub Codespaces — 수동 시작 패턴

가장 게으른 방법:

1. 이 레포의 **Code → Codespaces → Create codespace** (폰의 GitHub 모바일 앱에서도 가능)
2. Codespace 내 터미널:
   ```bash
   pip install -r requirements.txt
   playwright install chromium
   cp .env.example .env && code .env   # 비밀값 입력
   python -m src.main
   ```
3. 일 끝나면 **Stop codespace** (자동으로 30분 뒤 stop, 60h/월 한도 안 건드리려면 직접 stop)

**예매 오픈일 워크플로**:
- 예매 오픈 30분 전: 폰에서 Codespace 시작 → 봇 가동 → 텔레그램에서 명령
- 좌석 확보 + 결제 완료 → Codespace stop

KTX 추가 일정 폴링이 길어질 것 같으면(몇 시간) → 한도 빨리 닳습니다. 이런 경우엔 1)~3) 중 하나로 옮기세요.

---

## 5) (고급) Codespaces 를 텔레그램으로 자동 기동하는 우회법

진짜 하고 싶다면 다음 흐름이 가능합니다:

```
[텔레그램] → webhook → [Cloudflare Worker(무료)]
                          │
                          ├─ GitHub API: codespace start
                          └─ 시작 후 응답: "30초 뒤 봇 깨어남"
[Codespace 안의 봇]    → 텔레그램에 polling 모드로 붙음
```

핵심 코드 스케치 (Cloudflare Worker):

```js
export default {
  async fetch(req, env) {
    const update = await req.json();
    const text = update.message?.text || "";
    if (text.startsWith("/wake")) {
      await fetch(`https://api.github.com/user/codespaces/${env.CS_NAME}/start`, {
        method: "POST",
        headers: { Authorization: `Bearer ${env.GH_PAT}`, "User-Agent": "wake" },
      });
      await fetch(`https://api.telegram.org/bot${env.BOT_TOKEN}/sendMessage`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ chat_id: update.message.chat.id, text: "🟢 봇 깨우는 중 (~30초)" }),
      });
    }
    return new Response("ok");
  },
};
```

단점: 콜드 스타트 30초, Codespace 가 idle 30분에 자동으로 죽으면 다시 깨워야 함, 60h/월 카운트.

---

---

## Autostart — OS 켜질 때 봇이 알아서 시작

PC-only 모드의 핵심. 봇이 부팅 시 떠 있어야 SQLite 에 저장된 KTX 작업을 자동 재개합니다.

### macOS
```bash
bash scripts/autostart/install-macos.sh
```
LaunchAgent 가 등록되며, 로그인 시마다 자동 시작 + crash 복구.

### Linux (Ubuntu/Debian/RPi)
```bash
bash scripts/autostart/install-linux.sh
```
systemd user service 로 등록. `sudo loginctl enable-linger $USER` 까지 해두면 부팅 직후(로그인 전에도) 시작됩니다.

### Windows
```powershell
PowerShell -ExecutionPolicy Bypass -File scripts\autostart\install-windows.ps1
```
Task Scheduler 에 `AutoForMe` 작업 등록 — 로그온 시 자동 실행, 무한 재시도.

> 로그는 `state/logs/{stdout,stderr}.log` 또는 `journalctl --user -u auto-for-me`.

---

## 클라우드 KTX 워처 (PC 꺼져있을 때 좌석 감시)

### 1) GitHub repo secrets 등록
GitHub 레포 → **Settings → Secrets and variables → Actions** 에서:
- `KORAIL_ID`
- `KORAIL_PW`
- `TELEGRAM_BOT_TOKEN`

### 2) /watch 로 감시 등록
텔레그램에서:
```
/watch 서울 부산 2026-05-10 09:00 120
```
이러면 봇이 `watch_list.json` 을 업데이트합니다. 그 파일을 git 에 push:
```bash
git add watch_list.json && git commit -m "watch" && git push
```

### 3) 자동 작동
`.github/workflows/ktx-watcher.yml` 이 KST 06–24시 매 10분마다 실행 → 좌석 발견 시 텔레그램으로 알림.

| 비용 | public repo: 무제한 무료 / private repo: 2000분/월 (한 번에 1분 이내라 충분) |
| 한계 | 캡차가 떴을 때 자동 우회 불가 — 1회 알림 후 다음 회차 대기 |
| 알림 쿨다운 | 같은 감시 대상에 30분에 1번만 알림 (스팸 방지). 워크플로 artifact 에 저장됨 |

---

## 셋업 체크리스트

- [ ] `.env` 채움 (Anthropic, Telegram, Korail)
- [ ] 텔레그램 봇 만들고 `/start` 보내서 chat_id 확인 → `TELEGRAM_ALLOWED_CHAT_IDS` 에 추가
- [ ] **letskorail.com 에 결제용 카드 미리 등록** (간편결제) — 봇이 카드정보 다루지 않게 하기 위해
- [ ] `config.yaml` 에서 좌석 우선순위 / 폴링 주기 검토
- [ ] `python -m src.main` 으로 한 번 띄워서 텔레그램에 `/start` 응답 오는지 확인
- [ ] **Autostart 스크립트 실행** (macOS/Linux/Windows 중 OS 에 맞게)
- [ ] **GitHub repo secrets 등록** (KORAIL_ID/PW, TELEGRAM_BOT_TOKEN) — 클라우드 워처용
- [ ] (선택) `state/profile/` 디렉토리 백업 — 쿠키/세션 영속

## 자주 나는 문제

| 증상 | 원인 / 해결 |
|---|---|
| 캡차가 매번 뜸 | 같은 IP 에서 너무 자주 검색. `config.yaml` 의 `base_interval_sec` 를 키우세요 (15+) |
| 로그인이 자꾸 풀림 | `state/profile/` 디렉토리 권한 / 디스크 가득 / 컨테이너 재시작 시 마운트 누락 |
| Playwright 가 시작 안 됨 (linux) | `playwright install-deps` 누락. 또는 RAM 부족 (Chromium 1GB+ 필요) |
| 텔레그램 응답 없음 | `TELEGRAM_ALLOWED_CHAT_IDS` 화이트리스트에 자기 chat_id 가 빠짐 |
| 좌석은 잡히는데 결제가 안 됨 | letskorail.com 에 간편결제 카드를 사전 등록했는지 확인 |
