from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class GoogleServiceAccountConfig:
    client_email: str
    private_key: str
    token_uri: str


@dataclass(frozen=True)
class AppConfig:
    port: int
    webhook_path: str
    telegram_bot_token: str
    telegram_secret_token: str
    telegram_allowed_chat_ids: tuple[int, ...]
    google_calendar_id: str
    google_service_account: GoogleServiceAccountConfig
    company_google_calendar_id: str
    calendar_timezone: str
    event_prefix: str
    default_amount_unit: str
    charge_keywords: tuple[str, ...]
    default_event_duration_minutes: int
    recharge_event_duration_minutes: int
    initial_balance_won: int
    balance_lookback_days: int
    public_base_url: str
    google_oauth_client_id: str
    google_oauth_client_secret: str
    google_oauth_redirect_uri: str
    google_oauth_state_secret: str
    google_oauth_user_email: str
    database_url: str
    processed_updates_file: Path
    ledger_file: Path


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        os.environ.setdefault(key, value)


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _parse_int(value: str, default: int) -> int:
    text = value.strip()
    return int(text) if text else default


def _parse_chat_ids(value: str) -> tuple[int, ...]:
    chat_ids: list[int] = []
    for chunk in value.split(","):
        item = chunk.strip()
        if not item:
            continue
        chat_ids.append(int(item))
    return tuple(chat_ids)


def _load_service_account() -> GoogleServiceAccountConfig:
    raw_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    file_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
    if raw_json:
        payload = json.loads(raw_json)
    elif file_path:
        payload = json.loads(Path(file_path).read_text(encoding="utf-8"))
    else:
        raise RuntimeError(
            "Set GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_SERVICE_ACCOUNT_FILE."
        )
    return GoogleServiceAccountConfig(
        client_email=payload["client_email"],
        private_key=payload["private_key"],
        token_uri=payload.get("token_uri", "https://oauth2.googleapis.com/token"),
    )


def load_config(base_dir: str | None = None) -> AppConfig:
    root = Path(base_dir or os.getcwd())
    load_dotenv(root / ".env")

    is_vercel = bool(os.getenv("VERCEL", "").strip())
    default_processed_updates_file = (
        "/tmp/processed_updates.json" if is_vercel else ".data/processed_updates.json"
    )
    default_ledger_file = "/tmp/ledger.jsonl" if is_vercel else ".data/ledger.jsonl"
    processed_updates_file = Path(
        os.getenv("PROCESSED_UPDATES_FILE", default_processed_updates_file)
    )
    ledger_file = Path(os.getenv("LEDGER_FILE", default_ledger_file))
    google_oauth_redirect_uri = os.getenv("GOOGLE_OAUTH_REDIRECT_URI", "").strip()
    public_base_url = os.getenv("PUBLIC_BASE_URL", "").strip()
    if not public_base_url and google_oauth_redirect_uri:
        parts = urlsplit(google_oauth_redirect_uri)
        public_base_url = f"{parts.scheme}://{parts.netloc}"

    return AppConfig(
        port=_parse_int(os.getenv("PORT", ""), 8080),
        webhook_path=os.getenv("WEBHOOK_PATH", "/telegram/webhook").strip() or "/telegram/webhook",
        telegram_bot_token=_require_env("TELEGRAM_BOT_TOKEN"),
        telegram_secret_token=os.getenv("TELEGRAM_SECRET_TOKEN", "").strip(),
        telegram_allowed_chat_ids=_parse_chat_ids(
            os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "")
        ),
        google_calendar_id=_require_env("GOOGLE_CALENDAR_ID"),
        google_service_account=_load_service_account(),
        company_google_calendar_id=os.getenv("COMPANY_GOOGLE_CALENDAR_ID", "").strip(),
        calendar_timezone=os.getenv("CALENDAR_TIMEZONE", "Asia/Seoul").strip() or "Asia/Seoul",
        event_prefix=os.getenv("EVENT_PREFIX", "").strip(),
        default_amount_unit=os.getenv("DEFAULT_AMOUNT_UNIT", "man").strip() or "man",
        charge_keywords=tuple(
            keyword.strip()
            for keyword in os.getenv("CHARGE_KEYWORDS", "충전,입금,예치금").split(",")
            if keyword.strip()
        ),
        default_event_duration_minutes=_parse_int(
            os.getenv("DEFAULT_EVENT_DURATION_MINUTES", ""),
            60,
        ),
        recharge_event_duration_minutes=_parse_int(
            os.getenv("RECHARGE_EVENT_DURATION_MINUTES", ""),
            60,
        ),
        initial_balance_won=_parse_int(os.getenv("INITIAL_BALANCE_WON", ""), 0),
        balance_lookback_days=_parse_int(os.getenv("BALANCE_LOOKBACK_DAYS", ""), 3650),
        public_base_url=public_base_url,
        google_oauth_client_id=os.getenv("GOOGLE_OAUTH_CLIENT_ID", "").strip(),
        google_oauth_client_secret=os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "").strip(),
        google_oauth_redirect_uri=google_oauth_redirect_uri,
        google_oauth_state_secret=os.getenv("GOOGLE_OAUTH_STATE_SECRET", "").strip(),
        google_oauth_user_email=os.getenv("GOOGLE_OAUTH_USER_EMAIL", "").strip(),
        database_url=(
            os.getenv("DATABASE_URL", "").strip()
            or os.getenv("POSTGRES_URL", "").strip()
            or os.getenv("POSTGRES_PRISMA_URL", "").strip()
        ),
        processed_updates_file=root / processed_updates_file,
        ledger_file=root / ledger_file,
    )
