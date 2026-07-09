import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from src.haircut_bot.config import AppConfig, GoogleServiceAccountConfig
from src.haircut_bot.google_oauth import GoogleOAuthClient


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
        redis_rest_url="",
        redis_rest_token="",
        processed_updates_file=Path("/tmp/a"),
        ledger_file=Path("/tmp/b"),
    )


class GoogleOAuthTests(unittest.TestCase):
    def test_state_round_trip(self) -> None:
        client = GoogleOAuthClient(build_config())
        url = client.build_authorization_url(chat_id=123)
        state = parse_qs(urlsplit(url).query)["state"][0]
        payload = client.verify_state(state)
        self.assertEqual(payload.chat_id, 123)


if __name__ == "__main__":
    unittest.main()
