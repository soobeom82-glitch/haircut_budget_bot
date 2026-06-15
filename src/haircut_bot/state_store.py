from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import quote
from urllib.request import Request, urlopen

from .config import AppConfig


BALANCE_KEY = "haircut:current_balance_won"
HISTORY_KEY = "haircut:history"
MAX_HISTORY_ITEMS = 50


@dataclass(frozen=True)
class StoredBalance:
    balance_won: int
    initialized: bool


@dataclass(frozen=True)
class StoredHistoryItem:
    action: str
    label: str
    delta_won: int
    balance_won: int
    event_time: str
    amount_label: str = ""


class RedisStateStore:
    def __init__(self, config: AppConfig) -> None:
        self._url = config.redis_rest_url.rstrip("/")
        self._token = config.redis_rest_token
        self._initial_balance_won = config.initial_balance_won

    @property
    def enabled(self) -> bool:
        return bool(self._url and self._token)

    def get_balance(self) -> StoredBalance:
        if not self.enabled:
            return StoredBalance(balance_won=self._initial_balance_won, initialized=False)

        data = self._request("GET", f"/get/{quote(BALANCE_KEY, safe='')}")
        result = data.get("result")
        if result is None:
            return StoredBalance(balance_won=self._initial_balance_won, initialized=False)
        return StoredBalance(balance_won=int(result), initialized=True)

    def set_balance(self, balance_won: int) -> int:
        if not self.enabled:
            raise RuntimeError("Redis state store is not configured.")
        self._request(
            "POST",
            f"/set/{quote(BALANCE_KEY, safe='')}/{quote(str(balance_won), safe='')}",
        )
        return balance_won

    def append_history(self, item: StoredHistoryItem) -> None:
        if not self.enabled:
            return
        payload = quote(json.dumps(item.__dict__, ensure_ascii=False), safe="")
        self._request(
            "POST",
            f"/lpush/{quote(HISTORY_KEY, safe='')}/{payload}",
        )
        self._request(
            "POST",
            f"/ltrim/{quote(HISTORY_KEY, safe='')}/0/{MAX_HISTORY_ITEMS - 1}",
        )

    def get_history(self, limit: int = 5) -> list[StoredHistoryItem]:
        if not self.enabled:
            return []
        safe_limit = max(1, min(limit, 20))
        data = self._request(
            "GET",
            f"/lrange/{quote(HISTORY_KEY, safe='')}/0/{safe_limit - 1}",
        )
        result = data.get("result") or []
        items: list[StoredHistoryItem] = []
        for raw_item in result:
            if isinstance(raw_item, str):
                payload = json.loads(raw_item)
            else:
                payload = raw_item
            items.append(
                StoredHistoryItem(
                    action=payload.get("action", ""),
                    label=payload.get("label", ""),
                    delta_won=int(payload.get("delta_won", 0)),
                    balance_won=int(payload.get("balance_won", 0)),
                    event_time=payload.get("event_time", ""),
                    amount_label=payload.get("amount_label", ""),
                )
            )
        return items

    def _request(self, method: str, path: str) -> dict:
        request = Request(
            f"{self._url}{path}",
            headers={"Authorization": f"Bearer {self._token}"},
            method=method,
        )
        with urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
