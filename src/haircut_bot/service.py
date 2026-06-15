from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import logging
from zoneinfo import ZoneInfo

from .config import AppConfig
from .google_calendar import GoogleCalendarClient
from .parsing import (
    build_event_title,
    format_delta,
    parse_amount_to_won,
    parse_transaction,
)
from .state_store import RedisStateStore
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
        telegram_client: TelegramBotClient,
        store: ProcessedUpdateStore,
        state_store: RedisStateStore,
    ) -> None:
        self._config = config
        self._calendar_client = calendar_client
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

    def _handle_command(self, text: str, chat_id: int, message_id: int | None) -> ServiceResult:
        command = text.split()[0].split("@", 1)[0].lower()
        if command == "/balance":
            balance = self._get_current_balance(datetime.now(tz=self._tz))
            self._safe_send_message(
                chat_id,
                f"현재 잔액은 {balance:,}원입니다.",
                reply_to_message_id=message_id,
            )
            return ServiceResult(ok=True, message="balance_sent", balance_won=balance)

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
                    "Redis가 아직 연결되지 않았어요. Upstash를 먼저 연결해 주세요.",
                    reply_to_message_id=message_id,
                )
                return ServiceResult(ok=False, message="state_store_not_configured")

            self._save_current_balance(amount_won)
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
                "- /setbalance 36만",
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
