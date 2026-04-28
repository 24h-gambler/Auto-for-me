# Auto-for-me

KTX(코레일) 자동 예매 + 쿠팡 리뷰 기반 상품 추천을 **무한 반복**으로 수행하는 에이전트형 자동화 봇.

- **언제까지?** 매진이 풀려 좌석을 잡거나, 사용자가 만족하는 상품을 찾을 때까지.
- **어떻게?** Playwright(stealth) + Claude(opus-4-7) 오케스트레이터 + Telegram UI.
- **어디서?** VPS/홈서버에 상시 구동 → 텔레그램으로 명령/알림.

> ⚠️ **법적 고지** — 매크로를 통한 KTX 좌석 선점/재판매는 「철도사업법」 및 코레일 약관 위반입니다. 본 코드는 **본인 1건 예매(개인 사용)** 및 **개인 의사결정 보조** 한정으로 제공됩니다. 상업적 재판매·대량 트래픽·티켓 매도 목적 사용을 금합니다. 폴링 주기는 인간 트래픽 수준으로 제한되어 있으며, 캡차는 자동 우회하지 않고 사용자에게 입력을 요청합니다.

---

## 아키텍처

```
┌─────────────┐    ┌──────────────────────┐    ┌──────────────────────┐
│  Telegram   │ ⇄  │  Orchestrator        │ ⇄  │  Claude (opus-4-7)   │
│  (사용자UI) │    │  (tool-using agent)  │    │  - tool use          │
└─────────────┘    └──────────┬───────────┘    │  - prompt caching    │
                              │                └──────────────────────┘
                ┌─────────────┼──────────────┐
                ▼             ▼              ▼
        ┌────────────┐ ┌────────────┐ ┌──────────────┐
        │ KTX Booker │ │ Coupang    │ │ Notifier     │
        │ (Playwright│ │ Scraper +  │ │ (Telegram    │
        │  + stealth)│ │ Ranker     │ │  alerts)     │
        └─────┬──────┘ └─────┬──────┘ └──────────────┘
              │              │
              ▼              ▼
        ┌──────────────────────────────┐
        │ Stealth Browser + Humanizer  │  ← User-data dir 영속, 쿠키 재사용
        │ (mouse curves, type jitter,  │
        │  font/canvas/webgl spoof)    │
        └──────────────────────────────┘
```

## 빠른 시작

```bash
# 1) 의존성
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

# 2) 설정
cp .env.example .env            # API 키 / 텔레그램 토큰 입력
cp config.example.yaml config.yaml

# 3) 실행
python -m src.main
```

## 💸 100% 무료로 KTX 만 돌리기 (Anthropic API 불필요)

자유채팅 / 쿠팡 분석에만 Claude API 가 필요합니다. **KTX 예매는 슬래시 명령으로 직접
부탁하면 API 호출이 0회** 발생하며, `.env` 의 `ANTHROPIC_API_KEY` 를 비워두어도 됩니다.

필요한 것 (전부 무료):
- 자기 PC / 노트북 / 라즈베리파이 — 전기료만
- Telegram 봇 토큰 — `@BotFather` 에서 무료 발급
- letskorail.com 계정 — 무료
- (선택) Oracle Cloud Always Free / Fly.io — PC 가 없을 때

`.env` 최소 설정:
```bash
ANTHROPIC_API_KEY=                       # 비워둬도 됨 (KTX 슬래시 명령에는 불필요)
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_ALLOWED_CHAT_IDS=11111111
KORAIL_ID=your_korail_id
KORAIL_PW=your_korail_pw
HEADLESS=false                           # 결제 페이지 직접 보려면 false
USER_DATA_DIR=./state/profile
```

운영:
```bash
python -m src.main
# 텔레그램에서:
/start
/book 서울 부산 2026-05-10 09:00 120     ← 빠른 폴링, 좌석 잡히면 결제 페이지로
/jobs                                    ← 진행 상황
```

## 텔레그램 명령어

| 명령 | 설명 |
|---|---|
| `/book 서울 부산 2026-05-10 09:00 120` | 빠른 폴링 + 일반/특실 가리지 않고 가장 먼저 잡히는 좌석 즉시 확보 → 결제 페이지 진입 (추천) |
| `/ktx 서울 부산 2026-05-10 09:00 120 !` | `/book` 과 동일 (`!` = 빠른 폴링) |
| `/ktx 서울 부산 2026-05-10 09:00 120` | 보통 폴링 (8~90초 백오프) |
| `/watch 서울 부산 2026-05-10 09:00 120` | PC 꺼져있을 때 GitHub Actions 가 좌석 감시 → 알림 |
| `/jobs` / `/cancel <id>` | 작업 목록 / 취소 |
| `/captcha <코드>` | 캡차 발생 시 봇이 스크린샷 전송 → 5분 안에 입력 |
| `/coupang ...` | 쿠팡 추천 (Anthropic API 키 필요) |
| (자유 채팅) | Claude 가 의도 파악 (Anthropic API 키 필요) |

## 프로젝트 구조

```
src/
├── main.py              # 엔트리포인트 (텔레그램 + 작업 루프)
├── config.py            # pydantic 설정
├── orchestrator.py      # Claude tool-using agent
├── ai/claude.py         # Anthropic SDK 래퍼 (caching, tool use)
├── browser/
│   ├── stealth.py       # Playwright + stealth + 영속 프로필
│   └── humanize.py      # 마우스 곡선, 타이핑 지터, 스크롤
├── ktx/
│   ├── booker.py        # 검색→좌석 선택→결제 단계 자동화
│   └── selectors.py     # 코레일 DOM 셀렉터 (사이트 변경 시 여기만 수정)
├── coupang/
│   ├── scraper.py       # 검색/상세/리뷰 수집
│   └── ranker.py        # Claude 기반 리뷰 신뢰도 + 최신성 가중 랭킹
├── notify/telegram.py   # 텔레그램 봇 + 캡차 릴레이
└── utils/
    ├── retry.py         # 지수 백오프 + 지터 + 무한 폴링
    └── log.py           # 구조화 로깅
```

## 봇 탐지 회피 (defensive automation)

| 기법 | 구현 위치 |
|---|---|
| User-Agent / Accept-Language / TZ 일치 | `browser/stealth.py` |
| WebDriver 플래그 제거, navigator 패치 | `playwright-stealth` |
| 마우스 베지어 곡선 이동 | `browser/humanize.py:human_mouse_to` |
| 키 입력 간 60–180ms 지터 | `browser/humanize.py:human_type` |
| 스크롤 가속/감속 + 휴지 | `browser/humanize.py:human_scroll` |
| 쿠키/localStorage 영속 | `state/profile/` (user_data_dir) |
| 분당 요청 상한 (쿠팡) | `coupang/scraper.py` |
| 폴링 간격 지터 (KTX) | `ktx/booker.py` + `utils/retry.py` |
| 캡차 자동 해결 | **하지 않음** — 텔레그램으로 사용자에게 요청 |

## 무한 반복 보장

- `tenacity` 기반 무한 폴링 (`utils/retry.py:infinite_poll`).
- 네트워크 단절/세션 만료 → 재로그인 → 재개 (state machine).
- 각 작업은 SQLite 큐에 저장 → 프로세스 재시작에도 살아남음.
- 일정 시간(`max_total_hours`) 또는 사용자 취소 시까지 멈추지 않음.

## 보안

- 카드정보는 본 앱에 저장하지 않음. 코레일 간편결제(저장된 카드)에만 의존.
- 텔레그램 명령은 화이트리스트(`TELEGRAM_ALLOWED_CHAT_IDS`)만 수락.
- `.env` 와 `config.yaml`, `state/` 는 `.gitignore` 처리됨.
