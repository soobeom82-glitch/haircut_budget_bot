from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler

from src.haircut_bot.bootstrap import build_service


LOGGER = logging.getLogger("haircut_bot")


class handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        config, service = build_service()

        if config.telegram_secret_token:
            header_value = self.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
            if header_value != config.telegram_secret_token:
                self._json_response(403, {"ok": False, "error": "invalid_secret"})
                return

        content_length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except json.JSONDecodeError:
            self._json_response(400, {"ok": False, "error": "invalid_json"})
            return

        try:
            result = service.handle_update(payload)
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("Failed to process update")
            self._json_response(500, {"ok": False, "error": str(exc)})
            return

        self._json_response(200, result)

    def do_GET(self) -> None:  # noqa: N802
        self._json_response(200, {"ok": True, "message": "telegram_webhook_ready"})

    def log_message(self, format: str, *args: object) -> None:
        LOGGER.info("%s - %s", self.address_string(), format % args)

    def _json_response(self, status_code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

