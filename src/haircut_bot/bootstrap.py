from __future__ import annotations

import logging

from .config import AppConfig, load_config
from .google_calendar import GoogleCalendarClient
from .service import HaircutBotService
from .store import ProcessedUpdateStore
from .telegram_api import TelegramBotClient


def build_service() -> tuple[AppConfig, HaircutBotService]:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = load_config()
    store = ProcessedUpdateStore(config.processed_updates_file)
    calendar_client = GoogleCalendarClient(config)
    telegram_client = TelegramBotClient(config.telegram_bot_token)
    service = HaircutBotService(config, calendar_client, telegram_client, store)
    return config, service

