from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .config import AppConfig


AUTHORIZATION_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
OAUTH_SCOPES = (
    "https://www.googleapis.com/auth/calendar.readonly",
    "openid",
    "email",
)


@dataclass(frozen=True)
class OAuthStatePayload:
    issued_at: int
    nonce: str
    chat_id: int | None = None


@dataclass(frozen=True)
class OAuthTokens:
    access_token: str
    refresh_token: str | None
    expires_in: int
    scope: str
    token_type: str
    email: str | None = None


class GoogleOAuthClient:
    def __init__(self, config: AppConfig) -> None:
        self._config = config

    @property
    def enabled(self) -> bool:
        return all(
            [
                self._config.google_oauth_client_id,
                self._config.google_oauth_client_secret,
                self._config.google_oauth_redirect_uri,
                self._config.google_oauth_state_secret,
            ]
        )

    def build_authorization_url(self, chat_id: int | None = None) -> str:
        if not self.enabled:
            raise RuntimeError("Google OAuth is not configured.")

        state = self._sign_state(
            OAuthStatePayload(
                issued_at=int(time.time()),
                nonce=secrets.token_urlsafe(18),
                chat_id=chat_id,
            )
        )
        query = {
            "client_id": self._config.google_oauth_client_id,
            "redirect_uri": self._config.google_oauth_redirect_uri,
            "response_type": "code",
            "scope": " ".join(OAUTH_SCOPES),
            "access_type": "offline",
            "include_granted_scopes": "true",
            "prompt": "consent",
            "state": state,
        }
        if self._config.google_oauth_user_email:
            query["login_hint"] = self._config.google_oauth_user_email
        return f"{AUTHORIZATION_URL}?{urlencode(query)}"

    def verify_state(self, raw_state: str, max_age_seconds: int = 900) -> OAuthStatePayload:
        try:
            encoded_payload, encoded_signature = raw_state.split(".", 1)
        except ValueError as exc:
            raise RuntimeError("Invalid OAuth state format.") from exc

        payload_bytes = self._base64url_decode(encoded_payload)
        expected_signature = self._base64url_encode(
            hmac.new(
                self._config.google_oauth_state_secret.encode("utf-8"),
                payload_bytes,
                hashlib.sha256,
            ).digest()
        )
        if not hmac.compare_digest(encoded_signature, expected_signature):
            raise RuntimeError("OAuth state signature mismatch.")

        payload = json.loads(payload_bytes.decode("utf-8"))
        issued_at = int(payload["iat"])
        if int(time.time()) - issued_at > max_age_seconds:
            raise RuntimeError("OAuth state expired.")

        chat_id = payload.get("chat_id")
        return OAuthStatePayload(
            issued_at=issued_at,
            nonce=str(payload["nonce"]),
            chat_id=int(chat_id) if chat_id is not None else None,
        )

    def exchange_code(self, code: str) -> OAuthTokens:
        if not self.enabled:
            raise RuntimeError("Google OAuth is not configured.")

        payload = urlencode(
            {
                "code": code,
                "client_id": self._config.google_oauth_client_id,
                "client_secret": self._config.google_oauth_client_secret,
                "redirect_uri": self._config.google_oauth_redirect_uri,
                "grant_type": "authorization_code",
            }
        ).encode("utf-8")
        request = Request(
            TOKEN_URL,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
        return OAuthTokens(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            expires_in=int(data.get("expires_in", 0)),
            scope=data.get("scope", ""),
            token_type=data.get("token_type", "Bearer"),
            email=self._extract_email_from_id_token(data.get("id_token", "")),
        )

    def refresh_access_token(self, refresh_token: str) -> OAuthTokens:
        if not self.enabled:
            raise RuntimeError("Google OAuth is not configured.")

        payload = urlencode(
            {
                "client_id": self._config.google_oauth_client_id,
                "client_secret": self._config.google_oauth_client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            }
        ).encode("utf-8")
        request = Request(
            TOKEN_URL,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
        return OAuthTokens(
            access_token=data["access_token"],
            refresh_token=refresh_token,
            expires_in=int(data.get("expires_in", 0)),
            scope=data.get("scope", ""),
            token_type=data.get("token_type", "Bearer"),
            email=None,
        )

    def _sign_state(self, payload: OAuthStatePayload) -> str:
        payload_json = json.dumps(
            {
                "iat": payload.issued_at,
                "nonce": payload.nonce,
                "chat_id": payload.chat_id,
            },
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        signature = hmac.new(
            self._config.google_oauth_state_secret.encode("utf-8"),
            payload_json,
            hashlib.sha256,
        ).digest()
        return (
            f"{self._base64url_encode(payload_json)}."
            f"{self._base64url_encode(signature)}"
        )

    def _extract_email_from_id_token(self, id_token: str) -> str | None:
        if not id_token:
            return None
        try:
            parts = id_token.split(".")
            if len(parts) != 3:
                return None
            payload = json.loads(self._base64url_decode(parts[1]).decode("utf-8"))
        except Exception:  # noqa: BLE001
            return None
        email = payload.get("email")
        return str(email) if email else None

    @staticmethod
    def _base64url_encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode("utf-8").rstrip("=")

    @staticmethod
    def _base64url_decode(value: str) -> bytes:
        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(value + padding)
