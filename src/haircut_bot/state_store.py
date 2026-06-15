from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import quote
from urllib.request import Request, urlopen

from .config import AppConfig


BALANCE_KEY = "haircut:current_balance_won"


@dataclass(frozen=True)
class StoredBalance:
    balance_won: int
    initialized: bool


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

    def _request(self, method: str, path: str) -> dict:
        request = Request(
            f"{self._url}{path}",
            headers={"Authorization": f"Bearer {self._token}"},
            method=method,
        )
        with urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
