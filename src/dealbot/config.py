"""설정 로딩.

- 동작 설정은 config.yaml (비밀값 없음)
- 비밀값(API 키/토큰)은 환경변수 또는 .env
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

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

    @field_validator("link_mode")
    @classmethod
    def _mode(cls, v: str | None) -> str | None:
        if v is not None and v not in LINK_MODES:
            raise ValueError(f"link_mode must be one of {LINK_MODES}")
        return v


class DealConfig(BaseModel):
    min_discount_rate: float = 30
    history_days: int = 30
    min_below_average_pct: float = 15
    min_history_samples: int = 3
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


class LinksConfig(BaseModel):
    always_deeplink: bool = False
    resolve_short_links: bool = True


class PushConfig(BaseModel):
    """휴대폰 푸시 (텔레그램과 별개). provider: auto | ntfy | pushover | none"""

    provider: str = "auto"
    events: list[str] = Field(default_factory=lambda: ["manual_link"])  # manual_link | publish_failed | error | daily_summary | startup
    ntfy_url: str = "https://ntfy.sh"


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
    ntfy_topic: str | None = None
    ntfy_token: str | None = None
    pushover_user_key: str | None = None
    pushover_app_token: str | None = None

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
    app: AppConfig = Field(default_factory=AppConfig)
    http: HttpConfig = Field(default_factory=HttpConfig)
    collectors: list[CollectorConfig] = Field(default_factory=list)
    shops: list[ShopConfig] = Field(default_factory=list)
    deal: DealConfig = Field(default_factory=DealConfig)
    publish: PublishConfig = Field(default_factory=PublishConfig)
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
            for f in ("name", "aliases", "domains", "link_mode", "provider", "disclosure", "enabled", "manual_hint"):
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
    return int(raw.strip())


def _env_str(name: str) -> str | None:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return None
    return raw.strip()


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
