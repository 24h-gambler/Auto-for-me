from __future__ import annotations

from pathlib import Path
from typing import List

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT = Path(__file__).resolve().parent.parent


class Env(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ROOT / ".env"), extra="ignore")

    # API 키는 빈 값 허용 — 슬래시 명령만 쓰면 호출이 없으므로 키가 없어도 봇 가동 가능.
    anthropic_api_key: str = Field("", alias="ANTHROPIC_API_KEY")

    telegram_bot_token: str = Field(..., alias="TELEGRAM_BOT_TOKEN")
    telegram_allowed_chat_ids: str = Field("", alias="TELEGRAM_ALLOWED_CHAT_IDS")

    korail_id: str = Field("", alias="KORAIL_ID")
    korail_pw: str = Field("", alias="KORAIL_PW")

    headless: bool = Field(True, alias="HEADLESS")
    user_data_dir: str = Field("./state/profile", alias="USER_DATA_DIR")
    # 공백/주석 잔재가 들어가도 안전하게 동작하도록 strip 처리는 사용처에서.
    proxy_url: str = Field("", alias="PROXY_URL")

    # CHROME_CDP_URL 가 설정되면 봇은 chromium 을 직접 띄우지 않고,
    # 사용자가 미리 띄운 진짜 Chrome 에 connect_over_cdp 로 붙는다.
    # 예: http://127.0.0.1:9222
    # 이 모드는 강한 anti-bot (코레일 신 사이트 등) 회피에 필수.
    chrome_cdp_url: str = Field("", alias="CHROME_CDP_URL")

    orchestrator_model: str = Field("claude-opus-4-7", alias="ORCHESTRATOR_MODEL")
    analyzer_model: str = Field("claude-sonnet-4-6", alias="ANALYZER_MODEL")

    @property
    def allowed_chat_ids(self) -> List[int]:
        raw = self.telegram_allowed_chat_ids.strip()
        if not raw:
            return []
        return [int(x) for x in raw.split(",") if x.strip()]


class KTXPassenger(BaseModel):
    adult: int = 1
    child: int = 0
    senior: int = 0


class KTXPoll(BaseModel):
    # 새 코레일 사이트가 봇 탐지가 강해서 보수적으로 조정 (-8003 회피).
    base_interval_sec: int = 15
    jitter_sec: int = 8
    max_interval_sec: int = 120
    max_total_hours: int = 48
    # aggressive=true 면 거의 일정 간격으로 빠르게 폴링 (봇 탐지 위험 ↑).
    # 예매 오픈일 / 매진 임박 구간에 일시적으로 켜는 용도.
    aggressive: bool = False
    aggressive_base_sec: int = 8
    aggressive_cap_sec: int = 20
    aggressive_jitter_sec: int = 4


class KTXConfig(BaseModel):
    passenger: KTXPassenger = KTXPassenger()
    seat_preference: List[str] = ["특실", "일반실"]
    car_preference: List[str] = ["정방향", "역방향"]
    auto_pay: bool = False
    poll: KTXPoll = KTXPoll()


class CoupangRanking(BaseModel):
    min_review_count: int = 50
    min_avg_rating: float = 4.2
    recent_review_window_days: int = 90
    recent_weight: float = 0.6
    require_text_reviews: bool = True
    blacklist_keywords: List[str] = []


class CoupangScraping(BaseModel):
    max_products_per_query: int = 20
    max_reviews_per_product: int = 80
    request_delay_sec: float = 2.0
    request_jitter_sec: float = 1.5


class CoupangConfig(BaseModel):
    ranking: CoupangRanking = CoupangRanking()
    scraping: CoupangScraping = CoupangScraping()


class HumanizeConfig(BaseModel):
    min_action_delay_ms: int = 250
    max_action_delay_ms: int = 1400
    type_delay_ms: List[int] = [60, 180]
    scroll_jitter: bool = True


class AppConfig(BaseModel):
    ktx: KTXConfig = KTXConfig()
    coupang: CoupangConfig = CoupangConfig()
    humanize: HumanizeConfig = HumanizeConfig()


def load_config() -> AppConfig:
    """Load config.yaml if present, else use defaults."""
    path = ROOT / "config.yaml"
    if not path.exists():
        return AppConfig()
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return AppConfig.model_validate(data)


def _load_env() -> Env:
    """Load .env with a clear, actionable error if required keys are missing."""
    try:
        return Env()  # type: ignore[call-arg]
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "환경변수 로드 실패. .env 파일을 만들고 ANTHROPIC_API_KEY 와 "
            "TELEGRAM_BOT_TOKEN 을 채워주세요. "
            "(`cp .env.example .env` 후 편집)\n"
            f"원본 오류: {exc}"
        ) from exc


# Singletons -----------------------------------------------------------------
ENV = _load_env()
CFG = load_config()
