from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .config import AppConfig
from .service import HaircutBotService


LOGGER = logging.getLogger("haircut_bot")


def run_server(config: AppConfig, service: HaircutBotService) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    handler_class = _build_handler(config, service)
    server = ThreadingHTTPServer(("0.0.0.0", config.port), handler_class)
    LOGGER.info("Listening on port %s, webhook path %s", config.port, config.webhook_path)
    server.serve_forever()


def _build_handler(config: AppConfig, service: HaircutBotService) -> type[BaseHTTPRequestHandler]:
    class WebhookHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/health":
                self._json_response(200, {"status": "ok"})
                return
            self._json_response(404, {"ok": False, "error": "not_found"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path != config.webhook_path:
                self._json_response(404, {"ok": False, "error": "not_found"})
                return

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

        def log_message(self, format: str, *args: object) -> None:
            LOGGER.info("%s - %s", self.address_string(), format % args)

        def _json_response(self, status_code: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return WebhookHandler

