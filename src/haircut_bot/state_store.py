from __future__ import annotations

import ssl
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from .config import AppConfig


MAX_HISTORY_ITEMS = 50
MAX_REQUEST_ATTEMPTS = 3
OAUTH_CREDENTIAL_KEY = "company_google_oauth"


class StateStoreUnavailableError(RuntimeError):
    """Raised when the backing database cannot be reached reliably."""


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


@dataclass(frozen=True)
class StoredOAuthCredential:
    refresh_token: str
    email: str
    scope: str
    updated_at: str


class PostgresStateStore:
    def __init__(self, config: AppConfig) -> None:
        self._database_url = config.database_url.strip()
        self._initial_balance_won = config.initial_balance_won
        self._schema_initialized = False

    @property
    def enabled(self) -> bool:
        return bool(self._database_url)

    def get_balance(self) -> StoredBalance:
        if not self.enabled:
            return StoredBalance(balance_won=self._initial_balance_won, initialized=False)

        self._ensure_schema()
        row = self._fetchone(
            """
            SELECT balance_won
            FROM haircut_balance_state
            WHERE state_key = %s
            """,
            ("current_balance",),
        )
        if row is None:
            return StoredBalance(balance_won=self._initial_balance_won, initialized=False)
        return StoredBalance(balance_won=int(row[0]), initialized=True)

    def set_balance(self, balance_won: int) -> int:
        if not self.enabled:
            raise RuntimeError("Postgres state store is not configured.")

        self._ensure_schema()
        self._execute(
            """
            INSERT INTO haircut_balance_state (state_key, balance_won, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (state_key)
            DO UPDATE SET balance_won = EXCLUDED.balance_won, updated_at = NOW()
            """,
            ("current_balance", balance_won),
        )
        return balance_won

    def append_history(self, item: StoredHistoryItem) -> None:
        if not self.enabled:
            return

        self._ensure_schema()
        self._execute(
            """
            INSERT INTO haircut_history (
                action,
                label,
                delta_won,
                balance_won,
                event_time,
                amount_label
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                item.action,
                item.label,
                item.delta_won,
                item.balance_won,
                item.event_time,
                item.amount_label,
            ),
        )
        self._execute(
            f"""
            DELETE FROM haircut_history
            WHERE id NOT IN (
                SELECT id
                FROM haircut_history
                ORDER BY id DESC
                LIMIT {MAX_HISTORY_ITEMS}
            )
            """,
        )

    def get_history(self, limit: int = 5) -> list[StoredHistoryItem]:
        if not self.enabled:
            return []

        self._ensure_schema()
        safe_limit = max(1, min(limit, 20))
        rows = self._fetchall(
            f"""
            SELECT action, label, delta_won, balance_won, event_time, amount_label
            FROM haircut_history
            ORDER BY id DESC
            LIMIT {safe_limit}
            """
        )
        items: list[StoredHistoryItem] = []
        for row in rows:
            event_time = row[4]
            if isinstance(event_time, datetime):
                event_time_text = event_time.isoformat()
            else:
                event_time_text = str(event_time)
            items.append(
                StoredHistoryItem(
                    action=str(row[0] or ""),
                    label=str(row[1] or ""),
                    delta_won=int(row[2] or 0),
                    balance_won=int(row[3] or 0),
                    event_time=event_time_text,
                    amount_label=str(row[5] or ""),
                )
            )
        return items

    def set_company_oauth_credential(self, credential: StoredOAuthCredential) -> None:
        if not self.enabled:
            raise RuntimeError("Postgres state store is not configured.")

        self._ensure_schema()
        self._execute(
            """
            INSERT INTO haircut_oauth_credentials (
                credential_key,
                refresh_token,
                email,
                scope,
                updated_at
            )
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (credential_key)
            DO UPDATE SET
                refresh_token = EXCLUDED.refresh_token,
                email = EXCLUDED.email,
                scope = EXCLUDED.scope,
                updated_at = EXCLUDED.updated_at
            """,
            (
                OAUTH_CREDENTIAL_KEY,
                credential.refresh_token,
                credential.email,
                credential.scope,
                credential.updated_at,
            ),
        )

    def get_company_oauth_credential(self) -> StoredOAuthCredential | None:
        if not self.enabled:
            return None

        self._ensure_schema()
        row = self._fetchone(
            """
            SELECT refresh_token, email, scope, updated_at
            FROM haircut_oauth_credentials
            WHERE credential_key = %s
            """,
            (OAUTH_CREDENTIAL_KEY,),
        )
        if row is None:
            return None

        updated_at = row[3]
        if isinstance(updated_at, datetime):
            updated_at_text = updated_at.isoformat()
        else:
            updated_at_text = str(updated_at)
        return StoredOAuthCredential(
            refresh_token=str(row[0]),
            email=str(row[1] or ""),
            scope=str(row[2] or ""),
            updated_at=updated_at_text,
        )

    def _ensure_schema(self) -> None:
        if self._schema_initialized or not self.enabled:
            return

        self._execute(
            """
            CREATE TABLE IF NOT EXISTS haircut_balance_state (
                state_key TEXT PRIMARY KEY,
                balance_won BIGINT NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        self._execute(
            """
            CREATE TABLE IF NOT EXISTS haircut_history (
                id BIGSERIAL PRIMARY KEY,
                action TEXT NOT NULL,
                label TEXT NOT NULL,
                delta_won BIGINT NOT NULL,
                balance_won BIGINT NOT NULL,
                event_time TIMESTAMPTZ NOT NULL,
                amount_label TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        self._execute(
            """
            CREATE TABLE IF NOT EXISTS haircut_oauth_credentials (
                credential_key TEXT PRIMARY KEY,
                refresh_token TEXT NOT NULL,
                email TEXT NOT NULL DEFAULT '',
                scope TEXT NOT NULL DEFAULT '',
                updated_at TIMESTAMPTZ NOT NULL
            )
            """
        )
        self._schema_initialized = True

    def _fetchone(self, query: str, params: tuple[Any, ...] = ()) -> tuple[Any, ...] | None:
        return self._run_query(query, params, fetch="one")

    def _fetchall(self, query: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        rows = self._run_query(query, params, fetch="all")
        return list(rows or [])

    def _execute(self, query: str, params: tuple[Any, ...] = ()) -> None:
        self._run_query(query, params, fetch="none")

    def _run_query(
        self,
        query: str,
        params: tuple[Any, ...],
        fetch: str,
    ) -> Any:
        last_error: Exception | None = None
        for attempt in range(1, MAX_REQUEST_ATTEMPTS + 1):
            connection = None
            cursor = None
            try:
                connection = self._connect()
                cursor = connection.cursor()
                cursor.execute(query, params)
                result = None
                if fetch == "one":
                    result = cursor.fetchone()
                elif fetch == "all":
                    result = cursor.fetchall()
                connection.commit()
                return result
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if connection is not None:
                    try:
                        connection.rollback()
                    except Exception:  # noqa: BLE001
                        pass
                if attempt < MAX_REQUEST_ATTEMPTS:
                    time.sleep(0.2 * attempt)
                    continue
            finally:
                if cursor is not None:
                    try:
                        cursor.close()
                    except Exception:  # noqa: BLE001
                        pass
                if connection is not None:
                    try:
                        connection.close()
                    except Exception:  # noqa: BLE001
                        pass
        raise StateStoreUnavailableError("Postgres state store request failed") from last_error

    def _connect(self):
        try:
            import pg8000.dbapi  # type: ignore[import-not-found]
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "pg8000 is not installed. Add it to requirements.txt before deploying."
            ) from exc

        parsed = urlsplit(self._database_url)
        if parsed.scheme not in {"postgres", "postgresql"}:
            raise RuntimeError("DATABASE_URL must use postgres:// or postgresql://")

        query = parse_qs(parsed.query)
        ssl_mode = (query.get("sslmode", ["require"])[0] or "require").lower()
        connect_kwargs: dict[str, Any] = {
            "user": unquote(parsed.username or ""),
            "password": unquote(parsed.password or ""),
            "host": parsed.hostname or "",
            "port": parsed.port or 5432,
            "database": parsed.path.lstrip("/"),
            "timeout": 15,
        }
        if ssl_mode != "disable":
            connect_kwargs["ssl_context"] = ssl.create_default_context()
        return pg8000.dbapi.connect(**connect_kwargs)
