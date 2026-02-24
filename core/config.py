from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*_args: object, **_kwargs: object) -> bool:
        return False


BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = BASE_DIR / "data"
DEFAULT_EXPORT_DIR = DEFAULT_DATA_DIR / "exports"


def _env_str(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None:
        raise ValueError(f"Missing required env: {name}")
    return value


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    return int(raw)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_csv(name: str, default: str = "") -> list[str]:
    raw = os.getenv(name, default)
    return [part.strip() for part in raw.split(",") if part.strip()]


@dataclass(slots=True)
class Settings:
    app_env: str
    app_host: str
    app_port: int
    tz: str
    log_level: str
    bot_token: str
    owner_id: int
    webapp_allowed_origins: list[str]
    webapp_auth_max_age_sec: int
    session_ttl_sec: int
    session_hmac_secret: str
    max_connections_per_user: int
    ws_heartbeat_sec: int
    ws_idle_timeout_sec: int
    ws_max_pending_req: int
    rpc_rate_limit_per_sec: int
    rpc_burst: int
    rpc_list_limit_max: int
    rpc_export_cooldown_sec: int
    rpc_clear_cooldown_sec: int
    data_dir: Path
    chat_max_messages: int
    storage_flush_interval_sec: int
    storage_flush_batch: int
    storage_fsync: bool
    lru_chat_cache_size: int
    export_dir: Path
    export_ttl_hours: int
    notify_window_sec: int
    notify_max_batch: int
    notify_retry_base_sec: int
    notify_retry_max_sec: int
    trust_proxy_headers: bool
    origin_check_strict: bool

    @property
    def chats_dir(self) -> Path:
        return self.data_dir / "chats"

    @property
    def origins_set(self) -> set[str]:
        return set(self.webapp_allowed_origins)

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.chats_dir.mkdir(parents=True, exist_ok=True)
        self.export_dir.mkdir(parents=True, exist_ok=True)


def _normalize_origins(origins: Iterable[str]) -> list[str]:
    result: list[str] = []
    for origin in origins:
        clean = origin.rstrip("/")
        if clean:
            result.append(clean)
    return result


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_dotenv(BASE_DIR / ".env", override=False)
    data_dir = Path(_env_str("DATA_DIR", str(DEFAULT_DATA_DIR))).expanduser().resolve()
    export_dir = Path(_env_str("EXPORT_DIR", str(DEFAULT_EXPORT_DIR))).expanduser().resolve()
    settings = Settings(
        app_env=_env_str("APP_ENV", "production"),
        app_host=_env_str("APP_HOST", "127.0.0.1"),
        app_port=_env_int("APP_PORT", 8000),
        tz=_env_str("TZ", "UTC"),
        log_level=_env_str("LOG_LEVEL", "INFO"),
        bot_token=_env_str("BOT_TOKEN", ""),
        owner_id=_env_int("OWNER_ID", 0),
        webapp_allowed_origins=_normalize_origins(
            _env_csv("WEBAPP_ALLOWED_ORIGINS", "https://localhost")
        ),
        webapp_auth_max_age_sec=_env_int("WEBAPP_AUTH_MAX_AGE_SEC", 300),
        session_ttl_sec=_env_int("SESSION_TTL_SEC", 1800),
        session_hmac_secret=_env_str("SESSION_HMAC_SECRET", ""),
        max_connections_per_user=_env_int("MAX_CONNECTIONS_PER_USER", 3),
        ws_heartbeat_sec=_env_int("WS_HEARTBEAT_SEC", 30),
        ws_idle_timeout_sec=_env_int("WS_IDLE_TIMEOUT_SEC", 120),
        ws_max_pending_req=_env_int("WS_MAX_PENDING_REQ", 100),
        rpc_rate_limit_per_sec=_env_int("RPC_RATE_LIMIT_PER_SEC", 20),
        rpc_burst=_env_int("RPC_BURST", 40),
        rpc_list_limit_max=_env_int("RPC_LIST_LIMIT_MAX", 100),
        rpc_export_cooldown_sec=_env_int("RPC_EXPORT_COOLDOWN_SEC", 30),
        rpc_clear_cooldown_sec=_env_int("RPC_CLEAR_COOLDOWN_SEC", 30),
        data_dir=data_dir,
        chat_max_messages=_env_int("CHAT_MAX_MESSAGES", 10000),
        storage_flush_interval_sec=_env_int("STORAGE_FLUSH_INTERVAL_SEC", 5),
        storage_flush_batch=_env_int("STORAGE_FLUSH_BATCH", 500),
        storage_fsync=_env_bool("STORAGE_FSYNC", True),
        lru_chat_cache_size=_env_int("LRU_CHAT_CACHE_SIZE", 200),
        export_dir=export_dir,
        export_ttl_hours=_env_int("EXPORT_TTL_HOURS", 24),
        notify_window_sec=_env_int("NOTIFY_WINDOW_SEC", 2),
        notify_max_batch=_env_int("NOTIFY_MAX_BATCH", 50),
        notify_retry_base_sec=_env_int("NOTIFY_RETRY_BASE_SEC", 1),
        notify_retry_max_sec=_env_int("NOTIFY_RETRY_MAX_SEC", 60),
        trust_proxy_headers=_env_bool("TRUST_PROXY_HEADERS", True),
        origin_check_strict=_env_bool("ORIGIN_CHECK_STRICT", True),
    )
    if not settings.bot_token:
        raise ValueError("BOT_TOKEN is required")
    if settings.owner_id <= 0:
        raise ValueError("OWNER_ID must be a positive integer")
    if not settings.session_hmac_secret:
        raise ValueError("SESSION_HMAC_SECRET is required")
    settings.ensure_dirs()
    return settings
