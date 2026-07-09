from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from http.server import BaseHTTPRequestHandler

from src.haircut_bot.bootstrap import build_service


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        _, service = build_service()
        query = parse_qs(urlsplit(self.path).query)
        chat_id = None
        if "chat_id" in query and query["chat_id"]:
            try:
                chat_id = int(query["chat_id"][0])
            except ValueError:
                chat_id = None

        start_url = service.get_workauth_start_url(chat_id)
        if not start_url:
            self._html_response(
                500,
                "<h1>Google OAuth is not configured.</h1>",
            )
            return

        auth_url = service.build_google_oauth_authorization_url(chat_id)
        self.send_response(302)
        self.send_header("Location", auth_url)
        self.end_headers()

    def _html_response(self, status_code: int, body: str) -> None:
        payload = body.encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
