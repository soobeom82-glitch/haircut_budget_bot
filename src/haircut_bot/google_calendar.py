from __future__ import annotations

import base64
import json
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen

from .config import AppConfig
from .parsing import parse_balance_from_text


CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"


@dataclass
class CreatedCalendarEvent:
    event_id: str
    html_link: str


@dataclass
class CalendarEventSnapshot:
    event_id: str
    summary: str
    description: str


@dataclass
class CalendarEventDetail:
    event_id: str
    summary: str
    description: str
    start_value: str
    end_value: str
    status: str
    html_link: str


class GoogleCalendarClient:
    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._access_token: str | None = None
        self._access_token_expires_at = 0.0

    def get_latest_balance(self, reference_time: datetime) -> int:
        items = self._list_recent_events(reference_time)
        for item in reversed(items):
            summary = item.get("summary", "")
            description = item.get("description", "")
            balance = parse_balance_from_text(summary)
            if balance is None:
                balance = parse_balance_from_text(description)
            if balance is not None:
                return balance
        return self._config.initial_balance_won

    def create_event(
        self,
        summary: str,
        description: str,
        start_time: datetime,
        end_time: datetime,
    ) -> CreatedCalendarEvent:
        payload = {
            "summary": summary,
            "description": description,
            "start": {
                "dateTime": start_time.isoformat(),
                "timeZone": self._config.calendar_timezone,
            },
            "end": {
                "dateTime": end_time.isoformat(),
                "timeZone": self._config.calendar_timezone,
            },
        }
        encoded_calendar_id = quote(self._config.google_calendar_id, safe="")
        url = f"https://www.googleapis.com/calendar/v3/calendars/{encoded_calendar_id}/events"
        response = self._request_json("POST", url, payload)
        return CreatedCalendarEvent(
            event_id=response["id"],
            html_link=response.get("htmlLink", ""),
        )

    def find_event_by_update_id(
        self,
        update_id: int,
        reference_time: datetime,
    ) -> CalendarEventSnapshot | None:
        marker = f"update_id={update_id}"
        items = self._list_recent_events(reference_time)
        for item in reversed(items):
            summary = item.get("summary", "")
            description = item.get("description", "")
            if marker in description or marker in summary:
                return CalendarEventSnapshot(
                    event_id=item.get("id", ""),
                    summary=summary,
                    description=description,
                )
        return None

    def list_calendar_events(
        self,
        calendar_id: str,
        access_token: str,
        time_min: datetime,
        time_max: datetime,
        max_results: int = 10,
    ) -> list[CalendarEventDetail]:
        encoded_calendar_id = quote(calendar_id, safe="")
        query = urlencode(
            {
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": str(max_results),
                "timeMin": time_min.isoformat(),
                "timeMax": time_max.isoformat(),
            }
        )
        url = (
            f"https://www.googleapis.com/calendar/v3/calendars/"
            f"{encoded_calendar_id}/events?{query}"
        )
        response = self._request_json_with_token("GET", url, access_token)
        items = response.get("items", [])
        return [
            CalendarEventDetail(
                event_id=item.get("id", ""),
                summary=item.get("summary", "(제목 없음)"),
                description=item.get("description", ""),
                start_value=item.get("start", {}).get("dateTime")
                or item.get("start", {}).get("date", ""),
                end_value=item.get("end", {}).get("dateTime")
                or item.get("end", {}).get("date", ""),
                status=item.get("status", ""),
                html_link=item.get("htmlLink", ""),
            )
            for item in items
            if item.get("status") != "cancelled"
        ]

    def _list_recent_events(self, reference_time: datetime) -> list[dict]:
        encoded_calendar_id = quote(self._config.google_calendar_id, safe="")
        time_min = (reference_time - timedelta(days=self._config.balance_lookback_days)).isoformat()
        time_max = (reference_time + timedelta(minutes=1)).isoformat()
        query = urlencode(
            {
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": "2500",
                "timeMin": time_min,
                "timeMax": time_max,
            }
        )
        url = (
            f"https://www.googleapis.com/calendar/v3/calendars/"
            f"{encoded_calendar_id}/events?{query}"
        )
        response = self._request_json("GET", url)
        return response.get("items", [])

    def _request_json(self, method: str, url: str, payload: dict | None = None) -> dict:
        token = self._get_access_token()
        return self._request_json_with_token(method, url, token, payload)

    def _request_json_with_token(
        self,
        method: str,
        url: str,
        token: str,
        payload: dict | None = None,
    ) -> dict:
        body = None
        headers = {"Authorization": f"Bearer {token}"}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        request = Request(url, data=body, headers=headers, method=method)
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))

    def _get_access_token(self) -> str:
        now = time.time()
        if self._access_token and now < self._access_token_expires_at - 60:
            return self._access_token

        issued_at = int(now)
        expires_at = issued_at + 3600
        header = self._base64url_json({"alg": "RS256", "typ": "JWT"})
        claims = self._base64url_json(
            {
                "iss": self._config.google_service_account.client_email,
                "scope": CALENDAR_SCOPE,
                "aud": self._config.google_service_account.token_uri,
                "iat": issued_at,
                "exp": expires_at,
            }
        )
        signing_input = f"{header}.{claims}".encode("utf-8")
        signature = self._sign(signing_input)
        assertion = (
            f"{header}.{claims}.{self._base64url(signature)}"
        )

        body = urlencode(
            {
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            }
        ).encode("utf-8")
        request = Request(
            self._config.google_service_account.token_uri,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self._access_token = payload["access_token"]
        self._access_token_expires_at = now + int(payload.get("expires_in", 3600))
        return self._access_token

    def _sign(self, message: bytes) -> bytes:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8") as key_file:
            key_file.write(self._config.google_service_account.private_key)
            key_file.flush()
            result = subprocess.run(
                ["openssl", "dgst", "-sha256", "-sign", key_file.name],
                input=message,
                capture_output=True,
                check=True,
            )
        return result.stdout

    @staticmethod
    def _base64url_json(payload: dict) -> str:
        return GoogleCalendarClient._base64url(
            json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        )

    @staticmethod
    def _base64url(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode("utf-8").rstrip("=")
