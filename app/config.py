from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .utils import is_safe_https_url


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value and value[0] in {'"', "'"} and value[-1:] == value[0]:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: str | None, default: int, *, name: str) -> int:
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc


@dataclass(frozen=True, repr=False)
class Settings:
    bot_token: str
    database_path: Path
    data_dir: Path
    bootstrap_admin_username: str = "mohammadrezakheiry"
    bootstrap_admin_chat_id: int | None = None
    telegram_api_base: str = "https://api.telegram.org"
    poll_timeout_seconds: int = 30
    request_timeout_seconds: int = 45
    job_interval_seconds: int = 10
    order_expiry_minutes: int = 30
    receipt_delay_seconds: int = 60
    currency_label: str = "تومان"
    timezone: str = "Asia/Tehran"
    payment_callback_bind: str = "127.0.0.1"
    payment_callback_port: int = 8787
    payment_callback_secret: str = ""
    public_payment_callback_url: str = ""
    plisio_api_key: str = ""
    plisio_currency: str = "USDT_TRX"
    plisio_source_currency: str = "IRR"
    plisio_amount_multiplier: int = 10
    log_level: str = "INFO"
    button_icon_ids: Mapping[str, str] = field(default_factory=dict)

    @property
    def payment_callback_enabled(self) -> bool:
        return bool(self.payment_callback_secret)

    def __repr__(self) -> str:
        return (
            "Settings(bot_token=<redacted>, "
            f"database_path={self.database_path!r}, "
            f"bootstrap_admin_username={self.bootstrap_admin_username!r})"
        )


_ADMIN_USERNAME = re.compile(r"[A-Za-z0-9_]{5,32}")
_LOG_LEVELS = frozenset({"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"})


def _validate_api_base(value: str) -> bool:
    if (
        not value
        or value != value.strip()
        or "\\" in value
        or any(character.isspace() or ord(character) < 32 for character in value)
    ):
        return False
    try:
        parsed = urlparse(value)
        port = parsed.port
    except ValueError:
        return False
    valid_port = port is None or 1 <= int(port) <= 65535
    structurally_valid = bool(
        parsed.scheme.casefold() in {"http", "https"}
        and parsed.hostname
        and parsed.username is None
        and parsed.password is None
        and valid_port
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )
    if not structurally_valid:
        return False
    if parsed.scheme.casefold() == "https":
        return True

    # A Bot API token is part of every request path. Plain HTTP is therefore
    # acceptable only for a self-hosted endpoint on this same machine.
    hostname = str(parsed.hostname or "").casefold().rstrip(".")
    if hostname == "localhost" or hostname.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def validate_settings(settings: Settings) -> None:
    """Reject configurations that would fail, spin, or weaken runtime guarantees."""

    if any(character.isspace() or ord(character) < 32 for character in settings.bot_token):
        raise RuntimeError("BOT_TOKEN cannot contain whitespace or control characters")
    if not _ADMIN_USERNAME.fullmatch(settings.bootstrap_admin_username):
        raise RuntimeError("BOOTSTRAP_ADMIN_USERNAME must be a valid Telegram username")
    if (
        settings.bootstrap_admin_chat_id is not None
        and settings.bootstrap_admin_chat_id <= 0
    ):
        raise RuntimeError("BOOTSTRAP_ADMIN_CHAT_ID must be a positive private-chat id")
    if not 1 <= settings.poll_timeout_seconds <= 50:
        raise RuntimeError("POLL_TIMEOUT_SECONDS must be between 1 and 50")
    if settings.request_timeout_seconds <= settings.poll_timeout_seconds:
        raise RuntimeError("REQUEST_TIMEOUT_SECONDS must be greater than POLL_TIMEOUT_SECONDS")
    if settings.request_timeout_seconds > 300:
        raise RuntimeError("REQUEST_TIMEOUT_SECONDS cannot exceed 300")
    if settings.job_interval_seconds <= 0:
        raise RuntimeError("JOB_INTERVAL_SECONDS must be positive")
    if settings.order_expiry_minutes <= 0:
        raise RuntimeError("ORDER_EXPIRY_MINUTES must be positive")
    if settings.receipt_delay_seconds < 0:
        raise RuntimeError("RECEIPT_DELAY_SECONDS cannot be negative")
    if settings.receipt_delay_seconds >= settings.order_expiry_minutes * 60:
        raise RuntimeError("RECEIPT_DELAY_SECONDS must be shorter than the order expiry")
    if not settings.currency_label.strip():
        raise RuntimeError("CURRENCY_LABEL cannot be empty")
    try:
        ZoneInfo(settings.timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise RuntimeError("TIMEZONE must be a valid IANA timezone") from exc
    if not settings.payment_callback_bind.strip():
        raise RuntimeError("PAYMENT_CALLBACK_BIND cannot be empty")
    if not 1 <= settings.payment_callback_port <= 65535:
        raise RuntimeError("PAYMENT_CALLBACK_PORT must be between 1 and 65535")
    if settings.payment_callback_secret and not re.fullmatch(
        r"[A-Za-z0-9_-]{43,128}", settings.payment_callback_secret
    ):
        raise RuntimeError(
            "PAYMENT_CALLBACK_SECRET must be a 43-128 character URL-safe random value"
        )
    if settings.public_payment_callback_url and not is_safe_https_url(
        settings.public_payment_callback_url
    ):
        raise RuntimeError("PUBLIC_PAYMENT_CALLBACK_URL must be a safe absolute HTTPS URL")
    if settings.plisio_amount_multiplier <= 0:
        raise RuntimeError("PLISIO_AMOUNT_MULTIPLIER must be positive")
    if settings.log_level not in _LOG_LEVELS:
        raise RuntimeError("LOG_LEVEL must be CRITICAL, ERROR, WARNING, INFO, or DEBUG")
    if not _validate_api_base(settings.telegram_api_base):
        raise RuntimeError(
            "TELEGRAM_API_BASE must use HTTPS, or HTTP only on a loopback host"
        )
    invalid_icon = next(
        (
            name
            for name, icon_id in settings.button_icon_ids.items()
            if not re.fullmatch(r"[0-9]{5,30}", str(icon_id))
        ),
        None,
    )
    if invalid_icon is not None:
        raise RuntimeError(f"BUTTON_ICON_{invalid_icon.upper()} must be a numeric custom-emoji id")


def load_settings(env_file: str | Path | None = None) -> Settings:
    selected = Path(
        env_file
        or os.environ.get("ENV_FILE", "")
        or (PROJECT_ROOT / ".env")
    ).expanduser()
    from_file = _read_env_file(selected)

    def get(name: str, default: str = "") -> str:
        return os.environ.get(name, from_file.get(name, default))

    token = get("BOT_TOKEN").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN is required. Put it in .env or the process environment.")

    data_dir = Path(get("DATA_DIR", str(PROJECT_ROOT / "data"))).expanduser().resolve()
    database_path = Path(
        get("DATABASE_PATH", str(data_dir / "alone_account.sqlite3"))
    ).expanduser().resolve()
    admin_chat_raw = get("BOOTSTRAP_ADMIN_CHAT_ID").strip()
    icon_names = (
        "shop",
        "wallet",
        "account",
        "support",
        "referral",
        "channel",
        "buy",
        "info",
        "back",
        "pay",
        "discount",
        "card",
        "crypto",
        "copy",
        "receipt",
        "cancel",
        "order",
        "upload",
        "phone",
    )
    icon_ids = {
        name: value
        for name in icon_names
        if (value := get(f"BUTTON_ICON_{name.upper()}").strip())
    }

    settings = Settings(
        bot_token=token,
        database_path=database_path,
        data_dir=data_dir,
        bootstrap_admin_username=get(
            "BOOTSTRAP_ADMIN_USERNAME", "mohammadrezakheiry"
        ).strip().lstrip("@").lower(),
        bootstrap_admin_chat_id=int(admin_chat_raw) if admin_chat_raw else None,
        telegram_api_base=get("TELEGRAM_API_BASE", "https://api.telegram.org").rstrip("/"),
        poll_timeout_seconds=_as_int(
            get("POLL_TIMEOUT_SECONDS"), 30, name="POLL_TIMEOUT_SECONDS"
        ),
        request_timeout_seconds=_as_int(
            get("REQUEST_TIMEOUT_SECONDS"), 45, name="REQUEST_TIMEOUT_SECONDS"
        ),
        job_interval_seconds=_as_int(
            get("JOB_INTERVAL_SECONDS"), 10, name="JOB_INTERVAL_SECONDS"
        ),
        order_expiry_minutes=_as_int(
            get("ORDER_EXPIRY_MINUTES"), 30, name="ORDER_EXPIRY_MINUTES"
        ),
        receipt_delay_seconds=_as_int(
            get("RECEIPT_DELAY_SECONDS"), 60, name="RECEIPT_DELAY_SECONDS"
        ),
        currency_label=get("CURRENCY_LABEL", "تومان"),
        timezone=get("TIMEZONE", "Asia/Tehran"),
        payment_callback_bind=get("PAYMENT_CALLBACK_BIND", "127.0.0.1"),
        payment_callback_port=_as_int(
            get("PAYMENT_CALLBACK_PORT"), 8787, name="PAYMENT_CALLBACK_PORT"
        ),
        payment_callback_secret=get("PAYMENT_CALLBACK_SECRET"),
        public_payment_callback_url=get("PUBLIC_PAYMENT_CALLBACK_URL"),
        plisio_api_key=get("PLISIO_API_KEY"),
        plisio_currency=get("PLISIO_CURRENCY", "USDT_TRX"),
        plisio_source_currency=get("PLISIO_SOURCE_CURRENCY", "IRR").upper(),
        plisio_amount_multiplier=_as_int(
            get("PLISIO_AMOUNT_MULTIPLIER"), 10, name="PLISIO_AMOUNT_MULTIPLIER"
        ),
        log_level=get("LOG_LEVEL", "INFO").upper(),
        button_icon_ids=icon_ids,
    )
    validate_settings(settings)
    data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    database_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name == "posix":
        data_dir.chmod(0o700)
        database_path.parent.chmod(0o700)
    return settings
