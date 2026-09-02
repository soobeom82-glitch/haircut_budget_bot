from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import logging
from urllib.parse import quote
from zoneinfo import ZoneInfo

from .config import AppConfig
from .google_calendar import CalendarEventDetail, GoogleCalendarClient
from .google_oauth import GoogleOAuthClient
from .parsing import (
    build_event_title,
    format_delta,
    parse_amount_to_won,
    parse_transaction,
)
from .state_store import (
    PostgresStateStore,
    StateStoreUnavailableError,
    StoredHistoryItem,
    StoredOAuthCredential,
)
from .store import ProcessedUpdateStore, append_ledger_entry
from .telegram_api import TelegramBotClient


LOGGER = logging.getLogger("haircut_bot")


@dataclass
class ServiceResult:
    ok: bool
    message: str
    duplicate: bool = False
    ignored: bool = False
    balance_won: int | None = None
    event_title: str | None = None
    event_id: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class HaircutBotService:
    def __init__(
        self,
        config: AppConfig,
        calendar_client: GoogleCalendarClient,
        oauth_client: GoogleOAuthClient,
        telegram_client: TelegramBotClient,
        store: ProcessedUpdateStore,
        state_store: PostgresStateStore,
    ) -> None:
        self._config = config
        self._calendar_client = calendar_client
        self._oauth_client = oauth_client
        self._telegram_client = telegram_client
        self._store = store
        self._state_store = state_store
        self._tz = ZoneInfo(config.calendar_timezone)

    def handle_update(self, update: dict) -> dict:
        update_id = update.get("update_id")
        if isinstance(update_id, int) and self._store.has(update_id):
            return ServiceResult(ok=True, message="duplicate_update", duplicate=True).to_dict()

        message = self._pick_message(update)
        if not message:
            if isinstance(update_id, int):
                self._store.mark(update_id)
            return ServiceResult(ok=True, message="ignored_non_message", ignored=True).to_dict()

        chat = message.get("chat", {})
        chat_id = int(chat["id"])
        text = (message.get("text") or "").strip()
        message_id = message.get("message_id")

        if self._config.telegram_allowed_chat_ids and chat_id not in self._config.telegram_allowed_chat_ids:
            if isinstance(update_id, int):
                self._store.mark(update_id)
            return ServiceResult(ok=False, message="chat_not_allowed", ignored=True).to_dict()

        if not text:
            if isinstance(update_id, int):
                self._store.mark(update_id)
            return ServiceResult(ok=True, message="ignored_non_text", ignored=True).to_dict()

        try:
            if text.startswith("/"):
                result = self._handle_command(text, chat_id, message_id)
                if isinstance(update_id, int):
                    self._store.mark(update_id)
                return result.to_dict()

            event_time = datetime.fromtimestamp(int(message["date"]), tz=self._tz)
            if isinstance(update_id, int):
                existing_event = self._calendar_client.find_event_by_update_id(
                    update_id,
                    event_time,
                )
                if existing_event:
                    self._store.mark(update_id)
                    existing_balance = self._get_current_balance(event_time)
                    return ServiceResult(
                        ok=True,
                        message="duplicate_update_remote",
                        duplicate=True,
                        balance_won=existing_balance,
                        event_title=existing_event.summary,
                        event_id=existing_event.event_id,
                    ).to_dict()

            parsed = parse_transaction(
                text,
                charge_keywords=self._config.charge_keywords,
                default_amount_unit=self._config.default_amount_unit,
            )
            if not parsed:
                self._safe_send_message(
                    chat_id,
                    "형식이 맞지 않아요. 예: `이발 3만`, `염색 4만`, `충전 30만`",
                    reply_to_message_id=message_id,
                )
                if isinstance(update_id, int):
                    self._store.mark(update_id)
                return ServiceResult(ok=False, message="invalid_format").to_dict()

            current_balance = self._get_current_balance(event_time)
            next_balance = current_balance + parsed.delta_won

            duration_minutes = self._config.default_event_duration_minutes
            if parsed.kind == "charge":
                duration_minutes = self._config.recharge_event_duration_minutes

            title = build_event_title(
                self._config.event_prefix,
                parsed.label,
                parsed.amount_won,
                next_balance,
            )
            description = "\n".join(
                [
                    f"raw_message={parsed.raw_text}",
                    f"label={parsed.normalized_label}",
                    f"delta_won={parsed.delta_won}",
                    f"balance_won={next_balance}",
                    f"chat_id={chat_id}",
                    f"message_id={message_id}",
                    f"update_id={update_id}",
                ]
            )
            created_event = self._calendar_client.create_event(
                summary=title,
                description=description,
                start_time=event_time,
                end_time=event_time + timedelta(minutes=duration_minutes),
            )

            ledger_entry = {
                "processed_at": datetime.now(tz=self._tz).isoformat(),
                "event_time": event_time.isoformat(),
                "chat_id": chat_id,
                "message_id": message_id,
                "update_id": update_id,
                "title": title,
                "raw_message": parsed.raw_text,
                "delta_won": parsed.delta_won,
                "balance_won": next_balance,
                "calendar_event_id": created_event.event_id,
                "calendar_event_link": created_event.html_link,
            }
            append_ledger_entry(self._config.ledger_file, ledger_entry)
            self._save_current_balance(next_balance)
            self._append_history(
                StoredHistoryItem(
                    action=parsed.kind,
                    label=parsed.label,
                    delta_won=parsed.delta_won,
                    balance_won=next_balance,
                    event_time=event_time.isoformat(),
                    amount_label=parsed.amount_label,
                )
            )
            if isinstance(update_id, int):
                self._store.mark(update_id)

            confirmation = "\n".join(
                [
                    f"{parsed.label} 처리 완료",
                    f"변동 {format_delta(parsed.delta_won)}",
                    f"잔액 {next_balance:,}원",
                    title,
                ]
            )
            self._safe_send_message(
                chat_id,
                confirmation,
                reply_to_message_id=message_id,
            )
            return ServiceResult(
                ok=True,
                message="event_created",
                balance_won=next_balance,
                event_title=title,
                event_id=created_event.event_id,
            ).to_dict()
        except StateStoreUnavailableError:
            LOGGER.exception("State store unavailable while handling update_id=%s", update_id)
            self._safe_send_message(
                chat_id,
                "현재 데이터베이스에 연결하지 못했어요. "
                "`DATABASE_URL` 설정과 Neon 상태를 확인한 뒤 "
                "같은 내용을 다시 보내 주세요.",
                reply_to_message_id=message_id,
            )
            if isinstance(update_id, int):
                self._store.mark(update_id)
            return ServiceResult(ok=False, message="state_store_unavailable").to_dict()

    def _handle_command(self, text: str, chat_id: int, message_id: int | None) -> ServiceResult:
        command = text.split()[0].split("@", 1)[0].lower()
        if command == "/balance":
            balance = self._get_current_balance(datetime.now(tz=self._tz))
            history = self._get_history(1)
            lines = [f"현재 잔액은 {balance:,}원입니다."]
            if history:
                lines.extend(
                    [
                        "",
                        "최근 거래",
                        self._format_history_line(history[0]),
                    ]
                )
            self._safe_send_message(
                chat_id,
                "\n".join(lines),
                reply_to_message_id=message_id,
            )
            return ServiceResult(ok=True, message="balance_sent", balance_won=balance)

        if command == "/workauth":
            auth_link = self._build_workauth_entry_link(chat_id)
            if not auth_link:
                self._safe_send_message(
                    chat_id,
                    "Google OAuth 설정이 아직 안 됐어요. `GOOGLE_OAUTH_CLIENT_ID`, "
                    "`GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REDIRECT_URI`, "
                    "`GOOGLE_OAUTH_STATE_SECRET`, `PUBLIC_BASE_URL`를 확인해 주세요.",
                    reply_to_message_id=message_id,
                )
                return ServiceResult(ok=False, message="oauth_not_configured")
            self._safe_send_message(
                chat_id,
                "\n".join(
                    [
                        "회사 캘린더 읽기 권한 연결 링크",
                        auth_link,
                    ]
                ),
                reply_to_message_id=message_id,
            )
            return ServiceResult(ok=True, message="workauth_sent")

        if command == "/worktoday":
            if not self._config.company_google_calendar_id:
                self._safe_send_message(
                    chat_id,
                    "회사 캘린더 ID가 없어요. `COMPANY_GOOGLE_CALENDAR_ID`를 설정해 주세요.",
                    reply_to_message_id=message_id,
                )
                return ServiceResult(ok=False, message="company_calendar_not_configured")

            credential = self._state_store.get_company_oauth_credential()
            if credential is None:
                self._safe_send_message(
                    chat_id,
                    "회사 계정 OAuth 연결이 아직 없어요. 먼저 `/workauth`를 실행해 주세요.",
                    reply_to_message_id=message_id,
                )
                return ServiceResult(ok=False, message="company_oauth_not_connected")

            now = datetime.now(tz=self._tz)
            day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = day_start + timedelta(days=1)
            token = self._oauth_client.refresh_access_token(credential.refresh_token)
            events = self._calendar_client.list_calendar_events(
                calendar_id=self._config.company_google_calendar_id,
                access_token=token.access_token,
                time_min=day_start,
                time_max=day_end,
                max_results=20,
            )
            if not events:
                self._safe_send_message(
                    chat_id,
                    "오늘 회사 일정이 없어요.",
                    reply_to_message_id=message_id,
                )
                return ServiceResult(ok=True, message="worktoday_empty")

            lines = [f"오늘 회사 일정 {len(events)}건"]
            for event in events:
                lines.append(self._format_calendar_event_line(event))
            self._safe_send_message(
                chat_id,
                "\n".join(lines),
                reply_to_message_id=message_id,
            )
            return ServiceResult(ok=True, message="worktoday_sent")

        if command == "/history":
            parts = text.split(maxsplit=1)
            limit = 5
            if len(parts) == 2 and parts[1].strip().isdigit():
                limit = max(1, min(int(parts[1].strip()), 10))

            history = self._get_history(limit)
            if not history:
                self._safe_send_message(
                    chat_id,
                    "최근 이력이 아직 없어요.",
                    reply_to_message_id=message_id,
                )
                return ServiceResult(ok=True, message="history_empty")

            lines = ["최근 이력"]
            for item in history:
                lines.append(self._format_history_line(item))

            self._safe_send_message(
                chat_id,
                "\n".join(lines),
                reply_to_message_id=message_id,
            )
            return ServiceResult(ok=True, message="history_sent")

        if command == "/setbalance":
            parts = text.split(maxsplit=1)
            if len(parts) != 2:
                self._safe_send_message(
                    chat_id,
                    "사용법: /setbalance 36만",
                    reply_to_message_id=message_id,
                )
                return ServiceResult(ok=False, message="set_balance_usage")

            amount_won = parse_amount_to_won(
                parts[1],
                default_amount_unit=self._config.default_amount_unit,
            )
            if amount_won is None:
                self._safe_send_message(
                    chat_id,
                    "금액 형식이 맞지 않아요. 예: /setbalance 36만",
                    reply_to_message_id=message_id,
                )
                return ServiceResult(ok=False, message="set_balance_invalid_amount")

            if not self._state_store.enabled:
                self._safe_send_message(
                    chat_id,
                    "데이터베이스가 아직 연결되지 않았어요. `DATABASE_URL`을 먼저 설정해 주세요.",
                    reply_to_message_id=message_id,
                )
                return ServiceResult(ok=False, message="state_store_not_configured")

            self._save_current_balance(amount_won)
            self._append_history(
                StoredHistoryItem(
                    action="set_balance",
                    label="잔액 기준 설정",
                    delta_won=0,
                    balance_won=amount_won,
                    event_time=datetime.now(tz=self._tz).isoformat(),
                    amount_label="",
                )
            )
            self._safe_send_message(
                chat_id,
                f"현재 잔액을 {amount_won:,}원으로 설정했어요.",
                reply_to_message_id=message_id,
            )
            return ServiceResult(ok=True, message="balance_updated", balance_won=amount_won)

        if command == "/chatid":
            self._safe_send_message(
                chat_id,
                f"이 채팅방 ID는 `{chat_id}` 입니다.",
                reply_to_message_id=message_id,
            )
            return ServiceResult(ok=True, message="chat_id_sent")

        help_text = "\n".join(
            [
                "사용 예시",
                "- 이발 3만",
                "- 염색 4만",
                "- 충전 30만",
                "",
                "명령어",
                "- /balance",
                "- /history",
                "- /setbalance 36만",
                "- /workauth",
                "- /worktoday",
                "- /chatid",
            ]
        )
        self._safe_send_message(
            chat_id,
            help_text,
            reply_to_message_id=message_id,
        )
        return ServiceResult(ok=True, message="help_sent")

    @staticmethod
    def _pick_message(update: dict) -> dict | None:
        for key in ("message", "edited_message", "channel_post", "edited_channel_post"):
            message = update.get(key)
            if message:
                return message
        return None

    def _safe_send_message(
        self,
        chat_id: int,
        text: str,
        reply_to_message_id: int | None = None,
    ) -> None:
        try:
            self._telegram_client.send_message(
                chat_id,
                text,
                reply_to_message_id=reply_to_message_id,
            )
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed to send Telegram confirmation to chat_id=%s", chat_id)

    def _get_current_balance(self, reference_time: datetime) -> int:
        if self._state_store.enabled:
            return self._state_store.get_balance().balance_won
        return self._calendar_client.get_latest_balance(reference_time)

    def _save_current_balance(self, balance_won: int) -> None:
        if not self._state_store.enabled:
            return
        self._state_store.set_balance(balance_won)

    def _append_history(self, item: StoredHistoryItem) -> None:
        if not self._state_store.enabled:
            return
        self._state_store.append_history(item)

    def _get_history(self, limit: int) -> list[StoredHistoryItem]:
        if not self._state_store.enabled:
            return []
        return self._state_store.get_history(limit)

    def _format_history_line(self, item: StoredHistoryItem) -> str:
        timestamp = self._format_event_time(item.event_time)
        if item.action == "set_balance":
            return f"{timestamp} {item.label} -> {item.balance_won:,}원"
        return (
            f"{timestamp} {item.label} {format_delta(item.delta_won)} "
            f"잔액 {item.balance_won:,}원"
        )

    def _format_event_time(self, raw_value: str) -> str:
        try:
            event_time = datetime.fromisoformat(raw_value)
        except ValueError:
            return raw_value
        return event_time.astimezone(self._tz).strftime("%m-%d %H:%M")

    def get_workauth_start_url(self, chat_id: int | None = None) -> str | None:
        if not self._oauth_client.enabled or not self._config.public_base_url:
            return None
        base = self._config.public_base_url.rstrip("/")
        if chat_id is None:
            return f"{base}/google/oauth/start"
        return f"{base}/google/oauth/start?chat_id={quote(str(chat_id), safe='')}"

    def build_google_oauth_authorization_url(self, chat_id: int | None = None) -> str:
        return self._oauth_client.build_authorization_url(chat_id)

    def complete_google_oauth_callback(self, params: dict[str, str]) -> str:
        if "error" in params:
            raise RuntimeError(f"Google OAuth error: {params['error']}")

        raw_state = params.get("state", "")
        code = params.get("code", "")
        if not raw_state or not code:
            raise RuntimeError("Missing OAuth state or authorization code.")

        if not self._state_store.enabled:
            raise RuntimeError("DATABASE_URL is not configured.")

        state = self._oauth_client.verify_state(raw_state)
        tokens = self._oauth_client.exchange_code(code)
        if not tokens.refresh_token:
            raise RuntimeError(
                "No refresh token returned. Re-run consent with prompt=consent."
            )

        email = tokens.email or self._config.google_oauth_user_email or ""
        if (
            self._config.google_oauth_user_email
            and email
            and email.lower() != self._config.google_oauth_user_email.lower()
        ):
            raise RuntimeError(
                "Authorized account does not match GOOGLE_OAUTH_USER_EMAIL."
            )
        self._state_store.set_company_oauth_credential(
            StoredOAuthCredential(
                refresh_token=tokens.refresh_token,
                email=email,
                scope=tokens.scope,
                updated_at=datetime.now(tz=self._tz).isoformat(),
            )
        )
        if state.chat_id is not None:
            self._safe_send_message(
                state.chat_id,
                "\n".join(
                    [
                        "회사 구글 캘린더 OAuth 연결이 완료됐어요.",
                        f"연결 계정: {email or '(이메일 확인 불가)'}",
                        "이제 `/worktoday`로 오늘 일정을 확인할 수 있어요.",
                    ]
                ),
            )
        return email

    def _build_workauth_entry_link(self, chat_id: int) -> str | None:
        return self.get_workauth_start_url(chat_id)

    def _format_calendar_event_line(self, event: CalendarEventDetail) -> str:
        start = self._format_calendar_time_value(event.start_value)
        end = self._format_calendar_time_value(event.end_value)
        if end:
            return f"- {start}~{end} {event.summary}"
        return f"- {start} {event.summary}"

    def _format_calendar_time_value(self, raw_value: str) -> str:
        if not raw_value:
            return ""
        if "T" not in raw_value:
            return raw_value
        try:
            dt = datetime.fromisoformat(raw_value)
        except ValueError:
            return raw_value
        return dt.astimezone(self._tz).strftime("%H:%M")
