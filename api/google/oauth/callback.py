from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from http.server import BaseHTTPRequestHandler

from src.haircut_bot.bootstrap import build_service


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        _, service = build_service()
        query = parse_qs(urlsplit(self.path).query)
        params = {key: values[0] for key, values in query.items() if values}

        try:
            email = service.complete_google_oauth_callback(params)
        except Exception as exc:  # noqa: BLE001
            self._html_response(
                500,
                "\n".join(
                    [
                        "<h1>Google OAuth connection failed.</h1>",
                        f"<p>{str(exc)}</p>",
                    ]
                ),
            )
            return

        email_text = email or "unknown user"
        self._html_response(
            200,
            "\n".join(
                [
                    "<h1>Google OAuth connection complete.</h1>",
                    f"<p>Connected account: {email_text}</p>",
                    "<p>You can go back to Telegram and run /worktoday.</p>",
                ]
            ),
        )

    def _html_response(self, status_code: int, body: str) -> None:
        payload = body.encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
