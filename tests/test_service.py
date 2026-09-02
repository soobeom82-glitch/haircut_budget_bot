import unittest
from pathlib import Path

from src.haircut_bot.config import AppConfig, GoogleServiceAccountConfig
from src.haircut_bot.service import HaircutBotService
from src.haircut_bot.state_store import StateStoreUnavailableError


def build_config() -> AppConfig:
    return AppConfig(
        port=8080,
        webhook_path="/telegram/webhook",
        telegram_bot_token="token",
        telegram_secret_token="secret",
        telegram_allowed_chat_ids=(),
        google_calendar_id="primary",
        google_service_account=GoogleServiceAccountConfig(
            client_email="service@example.com",
            private_key="private",
            token_uri="https://oauth2.googleapis.com/token",
        ),
        company_google_calendar_id="greg.47@kakaocorp.com",
        calendar_timezone="Asia/Seoul",
        event_prefix="",
        default_amount_unit="man",
        charge_keywords=("충전",),
        default_event_duration_minutes=60,
        recharge_event_duration_minutes=60,
        initial_balance_won=0,
        balance_lookback_days=3650,
        public_base_url="https://haircut-budget-bot.vercel.app",
        google_oauth_client_id="client-id",
        google_oauth_client_secret="client-secret",
        google_oauth_redirect_uri="https://haircut-budget-bot.vercel.app/google/oauth/callback",
        google_oauth_state_secret="state-secret",
        google_oauth_user_email="greg.47@kakaocorp.com",
        database_url="postgresql://user:pass@example.neon.tech/haircut?sslmode=require",
        processed_updates_file=Path("/tmp/a"),
        ledger_file=Path("/tmp/b"),
    )


class FakeCalendarClient:
    def __init__(self) -> None:
        self.create_event_called = False

    def find_event_by_update_id(self, update_id: int, reference_time) -> None:
        return None

    def create_event(self, **kwargs):
        self.create_event_called = True
        raise AssertionError("create_event should not be called when state store is unavailable")


class FakeOAuthClient:
    pass


class FakeTelegramClient:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str, int | None]] = []

    def send_message(self, chat_id: int, text: str, reply_to_message_id: int | None = None) -> None:
        self.messages.append((chat_id, text, reply_to_message_id))


class FakeProcessedUpdateStore:
    def __init__(self) -> None:
        self.marked: list[int] = []

    def has(self, update_id: int) -> bool:
        return False

    def mark(self, update_id: int) -> None:
        self.marked.append(update_id)


class FailingStateStore:
    enabled = True

    def get_balance(self):
        raise StateStoreUnavailableError("Postgres state store request failed")

    def get_company_oauth_credential(self):
        raise StateStoreUnavailableError("Postgres state store request failed")


class ServiceStateStoreFailureTests(unittest.TestCase):
    def test_transaction_returns_graceful_error_when_state_store_is_unavailable(self) -> None:
        calendar = FakeCalendarClient()
        telegram = FakeTelegramClient()
        store = FakeProcessedUpdateStore()
        service = HaircutBotService(
            build_config(),
            calendar,
            FakeOAuthClient(),
            telegram,
            store,
            FailingStateStore(),
        )

        result = service.handle_update(
            {
                "update_id": 101,
                "message": {
                    "message_id": 55,
                    "date": 1788051600,
                    "chat": {"id": 999},
                    "text": "이발 3만",
                },
            }
        )

        self.assertEqual(result["message"], "state_store_unavailable")
        self.assertFalse(result["ok"])
        self.assertEqual(store.marked, [101])
        self.assertFalse(calendar.create_event_called)
        self.assertIn("현재 데이터베이스에 연결하지 못했어요.", telegram.messages[0][1])

    def test_balance_command_returns_graceful_error_when_state_store_is_unavailable(self) -> None:
        telegram = FakeTelegramClient()
        store = FakeProcessedUpdateStore()
        service = HaircutBotService(
            build_config(),
            FakeCalendarClient(),
            FakeOAuthClient(),
            telegram,
            store,
            FailingStateStore(),
        )

        result = service.handle_update(
            {
                "update_id": 202,
                "message": {
                    "message_id": 77,
                    "date": 1788051600,
                    "chat": {"id": 999},
                    "text": "/balance",
                },
            }
        )

        self.assertEqual(result["message"], "state_store_unavailable")
        self.assertFalse(result["ok"])
        self.assertEqual(store.marked, [202])
        self.assertIn("DATABASE_URL", telegram.messages[0][1])


if __name__ == "__main__":
    unittest.main()
