"""설정 로딩.

- 동작 설정은 config.yaml (비밀값 없음)
- 비밀값(API 키/토큰)은 환경변수 또는 .env
"""

from __future__ import annotations

import dataclasses
import logging
import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, field_validator

from dealbot.shops import DEFAULT_SHOPS, LINK_MODES, Shop, ShopRegistry


class AppConfig(BaseModel):
    timezone: str = "Asia/Seoul"
    log_level: str = "INFO"
    scheduler_tick_seconds: int = 30
    prune_price_history_days: int = 180
    prune_events_days: int = 30


class HttpConfig(BaseModel):
    timeout_seconds: float = 20
    max_retries: int = 3
    retry_backoff_seconds: float = 2.0
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )


class CollectorConfig(BaseModel):
    name: str
    type: str
    enabled: bool = True
    interval_minutes: int = 60
    options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("interval_minutes")
    @classmethod
    def _positive_interval(cls, v: int) -> int:
        if v < 1:
            raise ValueError("interval_minutes must be >= 1")
        return v


class ShopConfig(BaseModel):
    """config.yaml 의 shops[] 항목. key 만 있으면 기본값을 덮어쓴다."""

    key: str
    name: str | None = None
    aliases: list[str] | None = None
    domains: list[str] | None = None
    link_mode: str | None = None
    provider: str | None = None
    disclosure: str | None = None
    enabled: bool | None = None
    manual_hint: str | None = None
    manual_fallback: bool | None = None

    @field_validator("link_mode")
    @classmethod
    def _mode(cls, v: str | None) -> str | None:
        if v is not None and v not in LINK_MODES:
            raise ValueError(f"link_mode must be one of {LINK_MODES}")
        return v


class CoupangConfig(BaseModel):
    """파트너스 API 호출 예산 (시간당). 0 이면 무제한."""

    max_calls_per_hour: int = 10
    deeplink_reserve: int = 3  # 발행용 딥링크 호출을 위해 남겨 둘 몫


class MarketCheckConfig(BaseModel):
    """(d) 시중가 대조: 다른 몰의 딜을 쿠팡 검색 API 로 찾은 같은 상품 가격과 비교."""

    enabled: bool = True
    min_below_market_pct: float = 10  # 쿠팡가보다 N% 이상 싸면 특가
    veto_if_not_cheaper: bool = True  # 쿠팡가보다 싸지 않으면 (a)/(c) 로 잡혀도 탈락
    require_for_discount_rule: bool = True  # 대조가 가능한 환경이면 (a) 표시 할인율만으로는 통과 못 함
    max_checks_per_hour: int = 3  # 쿠팡 검색 API 호출 예산 (전체 예산 안에서)
    cache_hours: int = 24
    min_token_match: float = 0.6  # 상품명 토큰 일치 비율


class InterestConfig(BaseModel):
    """관심도 게이트: 모든 상품을 대조할 수 없으니 '사람들이 관심 있어 보이는' 딜만 판정 대상으로.
    아래 신호 중 하나라도 넘으면 통과. 값이 없는 신호는 무시."""

    enabled: bool = True
    min_recommend: int = 1  # 커뮤니티 추천
    min_comments: int = 3  # 커뮤니티 댓글
    min_views: int = 500  # 커뮤니티 조회수
    max_rank: int = 30  # API 목록(골드박스/카테고리 베스트) 순위
    always_pass_sources: list[str] = Field(default_factory=lambda: ["manual", "adpick"])


class EnrichConfig(BaseModel):
    """상품 페이지(OG/JSON-LD)에서 이미지·가격·별점·리뷰 수를 읽어 빈 칸을 채운다."""

    enabled: bool = True
    shops: list[str] = Field(default_factory=lambda: ["toss", "naver", "oliveyoung", "kurly", "musinsa"])
    max_per_run: int = 10


class DealConfig(BaseModel):
    enrich: EnrichConfig = Field(default_factory=EnrichConfig)
    interest: InterestConfig = Field(default_factory=InterestConfig)
    market: MarketCheckConfig = Field(default_factory=MarketCheckConfig)
    min_discount_rate: float = 50
    history_days: int = 30
    min_below_average_pct: float = 15
    min_history_samples: int = 3
    observation_min_gap_hours: float = 6  # 같은 상품 가격 기록은 이 간격으로만 (재판정 때 중복 기록 방지)
    min_price: int = 1000
    exclude_keywords: list[str] = Field(default_factory=list)
    # (c) 커뮤니티 추천 수가 이 값 이상이면 가격 조건과 무관하게 특가로 인정 (0 이면 비활성)
    community_min_recommend: int = 5
    # 쿠폰/이벤트(가격 없음) 글을 다룰지. 다루면 규칙 (c) 로만 판정
    accept_coupons_and_events: bool = True


class PublishConfig(BaseModel):
    enabled: bool = True
    dry_run: bool = False
    max_per_hour: int = 6
    max_per_day: int = 40
    min_interval_seconds: int = 180
    dedup_days: int = 7
    queue_ttl_hours: int = 6
    manual_link_ttl_hours: int = 12  # 내 링크 입력을 기다리는 항목의 유효 시간
    max_publish_attempts: int = 3
    publisher_tick_seconds: int = 20
    send_photo: bool = True
    allow_raw_links: bool = True  # 제휴 변환이 불가능한 쇼핑몰은 원본 링크로라도 발행
    templates_dir: Path = Path("templates")
    template: str = "deal_post.j2"


class ThreadsConfig(BaseModel):
    """스레드 자동 발행. 인증은 /threadsauth 로 한 번만."""

    enabled: bool = True
    template: str = "deal_threads.j2"
    send_photo: bool = True
    refresh_before_days: int = 7


class CopyTarget(BaseModel):
    """API 가 없어 복붙해야 하는 곳 (카카오 오픈채팅, 네이버 블로그)."""

    key: str
    name: str
    template: str
    enabled: bool = True


class CopyConfig(BaseModel):
    """발행 후 관리자 챗으로 '복사해서 붙여넣을 문구'를 보낸다."""

    enabled: bool = True
    targets: list[CopyTarget] = Field(
        default_factory=lambda: [
            CopyTarget(key="kakao", name="카카오 오픈채팅", template="deal_kakao.j2"),
            CopyTarget(key="blog", name="네이버 블로그", template="deal_blog.j2"),
        ]
    )


class LinksConfig(BaseModel):
    always_deeplink: bool = False
    resolve_short_links: bool = True


class PushConfig(BaseModel):
    """휴대폰 푸시 (텔레그램과 별개). provider: auto | ntfy | pushover | none"""

    provider: str = "auto"
    events: list[str] = Field(default_factory=lambda: ["manual_link"])  # manual_link | publish_failed | error | daily_summary | startup
    ntfy_url: str = "https://ntfy.sh"


class NaverConnectConfig(BaseModel):
    """네이버 쇼핑커넥트 브라우저 자동화. 셀렉터는 실제 화면을 보고 맞춰야 함 (/shot 으로 확인)."""

    login_url: str = "https://nid.naver.com/nidlogin.login"
    create_url: str = "https://connect.naver.com/"
    login_cookie: str = "NID_AUT"
    timeout_seconds: int = 30
    selectors: dict[str, str] = Field(
        default_factory=lambda: {
            "qr_tab": "text=QR코드",
            "url_input": "input[type='url'], input[placeholder*='URL'], input[placeholder*='주소'], input[type='text']",
            "create_button": "text=링크 발급",
            "result": "input[readonly], textarea[readonly], a[href*='naver.me'], a[href*='connect']",
        }
    )


class BrowserConfig(BaseModel):
    enabled: bool = False
    headless: bool = True
    profile_dir: str = "browser-profile"  # data_dir 아래
    executable_path: str | None = None  # 크로미움 실행 파일 (비우면 자동 탐색, 환경변수 DEALBOT_CHROMIUM_PATH 도 가능)
    naver_connect: NaverConnectConfig = Field(default_factory=NaverConnectConfig)


class MonitoringConfig(BaseModel):
    push: PushConfig = Field(default_factory=PushConfig)
    notify_on_publish: bool = True
    notify_on_failure: bool = True
    notify_on_error: bool = True
    notify_on_manual_link: bool = True
    error_alert_cooldown_minutes: int = 30
    daily_summary_time: str = "21:00"

    @field_validator("daily_summary_time")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        hh, mm = v.split(":")
        if not (0 <= int(hh) < 24 and 0 <= int(mm) < 60):
            raise ValueError("daily_summary_time must be HH:MM")
        return v


class Secrets(BaseModel):
    """환경변수에서만 읽는 값들."""

    coupang_access_key: str | None = None
    coupang_secret_key: str | None = None
    coupang_sub_id: str | None = None
    linkprice_affiliate_id: str | None = None
    adpick_affid: str | None = None
    telegram_bot_token: str | None = None
    telegram_channel_id: str | None = None
    telegram_admin_chat_id: int | None = None
    threads_app_id: str | None = None
    threads_app_secret: str | None = None
    threads_redirect_uri: str = "https://localhost/callback"
    ntfy_topic: str | None = None
    ntfy_token: str | None = None
    pushover_user_key: str | None = None
    pushover_app_token: str | None = None

    @property
    def has_threads_app(self) -> bool:
        return bool(self.threads_app_id and self.threads_app_secret)

    @property
    def has_ntfy(self) -> bool:
        return bool(self.ntfy_topic)

    @property
    def has_pushover(self) -> bool:
        return bool(self.pushover_user_key and self.pushover_app_token)

    @property
    def has_coupang(self) -> bool:
        return bool(self.coupang_access_key and self.coupang_secret_key)

    @property
    def has_linkprice(self) -> bool:
        return bool(self.linkprice_affiliate_id)

    @property
    def has_adpick(self) -> bool:
        return bool(self.adpick_affid)

    @property
    def has_telegram(self) -> bool:
        return bool(self.telegram_bot_token)

    @property
    def has_channel(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_channel_id)

    @property
    def has_admin(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_admin_chat_id)


class Settings(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    app: AppConfig = Field(default_factory=AppConfig)
    http: HttpConfig = Field(default_factory=HttpConfig)
    coupang: CoupangConfig = Field(default_factory=CoupangConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    collectors: list[CollectorConfig] = Field(default_factory=list)
    shops: list[ShopConfig] = Field(default_factory=list)
    deal: DealConfig = Field(default_factory=DealConfig)
    publish: PublishConfig = Field(default_factory=PublishConfig)
    threads: ThreadsConfig = Field(default_factory=ThreadsConfig)
    # yaml 키는 copy 지만 BaseModel.copy 와 겹쳐 속성명은 copy_cfg
    copy_cfg: CopyConfig = Field(default_factory=CopyConfig, alias="copy")
    links: LinksConfig = Field(default_factory=LinksConfig)
    monitoring: MonitoringConfig = Field(default_factory=MonitoringConfig)
    secrets: Secrets = Field(default_factory=Secrets)

    # 파생 경로
    config_path: Path | None = None
    data_dir: Path = Path("data")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "dealbot.db"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def templates_dir(self) -> Path:
        d = self.publish.templates_dir
        if d.is_absolute():
            return d
        base = self.config_path.parent if self.config_path else Path.cwd()
        return (base / d).resolve()

    def shop_registry(self) -> ShopRegistry:
        """기본 쇼핑몰 목록 + config.shops 오버라이드."""
        shops: dict[str, Shop] = {
            s.key: dataclasses.replace(s, aliases=list(s.aliases), domains=list(s.domains)) for s in DEFAULT_SHOPS
        }
        for o in self.shops:
            base = shops.get(o.key) or Shop(key=o.key, name=o.name or o.key)
            for f in ("name", "aliases", "domains", "link_mode", "provider", "disclosure", "enabled", "manual_hint", "manual_fallback"):
                v = getattr(o, f)
                if v is not None:
                    setattr(base, f, v)
            shops[o.key] = base
        return ShopRegistry(list(shops.values()))

    @field_validator("collectors")
    @classmethod
    def _unique_names(cls, v: list[CollectorConfig]) -> list[CollectorConfig]:
        names = [c.name for c in v]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise ValueError(f"duplicate collector names: {sorted(dupes)}")
        return v


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str) -> int | None:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return None
    value = raw.strip().strip("'\"")
    try:
        return int(value)
    except ValueError:
        # 숫자가 아니면 죽지 말고 경고만 — 어떤 값이 잘못됐는지 로그로 알린다
        logging.getLogger(__name__).error(
            "환경변수 %s 는 숫자여야 합니다 (@userinfobot 이 알려준 숫자). 지금 값: %r — 무시합니다", name, raw
        )
        return None


def _env_str(name: str) -> str | None:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return None
    # 따옴표째 붙여넣는 실수가 잦아서 벗겨 준다
    return raw.strip().strip("'\"") or None


def load_settings(config_path: str | os.PathLike[str] | None = None, *, load_env: bool = True) -> Settings:
    """config.yaml + 환경변수를 합쳐 Settings 를 만든다."""
    if load_env:
        load_dotenv(override=False)

    path = Path(config_path or os.getenv("DEALBOT_CONFIG") or "config.yaml")
    raw: dict[str, Any] = {}
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    elif config_path is not None:
        raise FileNotFoundError(f"config file not found: {path}")

    secrets = Secrets(
        coupang_access_key=_env_str("COUPANG_ACCESS_KEY"),
        coupang_secret_key=_env_str("COUPANG_SECRET_KEY"),
        coupang_sub_id=_env_str("COUPANG_SUB_ID"),
        linkprice_affiliate_id=_env_str("LINKPRICE_AFFILIATE_ID"),
        adpick_affid=_env_str("ADPICK_AFFID"),
        telegram_bot_token=_env_str("TELEGRAM_BOT_TOKEN"),
        telegram_channel_id=_env_str("TELEGRAM_CHANNEL_ID"),
        telegram_admin_chat_id=_env_int("TELEGRAM_ADMIN_CHAT_ID"),
        threads_app_id=_env_str("THREADS_APP_ID"),
        threads_app_secret=_env_str("THREADS_APP_SECRET"),
        threads_redirect_uri=_env_str("THREADS_REDIRECT_URI") or "https://localhost/callback",
        ntfy_topic=_env_str("NTFY_TOPIC"),
        ntfy_token=_env_str("NTFY_TOKEN"),
        pushover_user_key=_env_str("PUSHOVER_USER_KEY"),
        pushover_app_token=_env_str("PUSHOVER_APP_TOKEN"),
    )

    settings = Settings(
        **raw,
        secrets=secrets,
        config_path=path.resolve() if path.exists() else None,
        data_dir=Path(os.getenv("DEALBOT_DATA_DIR") or raw.get("data_dir") or "data"),
    )

    # 환경변수 오버라이드
    if os.getenv("LOG_LEVEL"):
        settings.app.log_level = os.environ["LOG_LEVEL"].upper()
    settings.publish.dry_run = _env_bool("DEALBOT_DRY_RUN", settings.publish.dry_run)
    if os.getenv("TZ") and not raw.get("app", {}).get("timezone"):
        settings.app.timezone = os.environ["TZ"]

    return settings
