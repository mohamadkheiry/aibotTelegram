"""Transactional SQLite persistence for the Telegram shop bot.

The module deliberately owns no bot token or deployment configuration.  Every
public operation opens a fresh SQLite connection, enables WAL/foreign keys and
uses a busy timeout.  Write operations use ``BEGIN IMMEDIATE`` so balance,
inventory and payment decisions remain atomic across polling workers.

Amounts are integers in the order/product currency (for example whole toman).
Timestamps are UTC ISO-8601 strings.  Public methods return plain dictionaries,
which keeps this layer convenient for a Telegram handler without leaking live
``sqlite3.Row`` or connection objects.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence
from zoneinfo import ZoneInfo

from .utils import is_safe_https_url, is_safe_telegram_invite_url


class DatabaseError(RuntimeError):
    """Base class for domain-level persistence errors."""


class NotFoundError(DatabaseError):
    """A requested database entity does not exist."""


class ConflictError(DatabaseError):
    """An idempotency or uniqueness conflict was detected."""


class ValidationError(DatabaseError):
    """Input or an attempted state transition is invalid."""


class InsufficientFundsError(DatabaseError):
    """The wallet cannot fund the requested debit or hold."""


class OutOfStockError(DatabaseError):
    """No assignable inventory item is available."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime | str | None = None) -> str:
    if value is None:
        value = _utc_now()
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        value = parsed
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _json_load(value: str | None, default: Any = None) -> Any:
    if value is None:
        return default
    return json.loads(value)


def _has_customer_information(value: str | None) -> bool:
    try:
        payload = _json_load(value, {})
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(payload, Mapping):
        return False
    return bool(
        str(payload.get("text") or "").strip()
        or str(payload.get("file_id") or "").strip()
    )


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _rows(rows: Sequence[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(item) for item in rows]


def _ensure_backup_directory(path: Path, *, restrict_destination: bool) -> None:
    """Create missing backup directories and secure only owned destinations."""

    missing: list[Path] = []
    candidate = path
    while not candidate.exists():
        missing.append(candidate)
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent

    for directory in reversed(missing):
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            if not directory.is_dir():
                raise
        else:
            if os.name == "posix":
                directory.chmod(0o700)

    if not path.is_dir():
        raise NotADirectoryError(path)
    if os.name == "posix" and restrict_destination:
        # A caller that explicitly supplies a backup directory delegates that
        # directory to the backup store, so keep it private even if it existed.
        path.chmod(0o700)


def _prepare_backup_file(path: Path, *, overwrite: bool) -> None:
    """Pre-create a POSIX backup with private permissions before SQLite opens it."""

    if os.name != "posix":
        return
    flags = os.O_WRONLY | os.O_CREAT
    if not overwrite:
        flags |= os.O_EXCL
    descriptor = os.open(path, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)


def _username_parts(username: str) -> tuple[str, str]:
    display = username.strip().lstrip("@").strip()
    if not display:
        raise ValidationError("username cannot be empty")
    return display, display.casefold()


class Database:
    """High-level, connection-safe SQLite repository."""

    ADMIN_ROLES = frozenset({"owner", "admin", "support"})
    # A transfer made from an old card-payment instruction can reach the SMS
    # callback after that intent was cancelled or expired.  Quarantine its
    # amount long enough that a newly-created intent cannot be credited for the
    # delayed transfer solely because the numeric amount happens to match.
    CARD_AMOUNT_REUSE_COOLDOWN = timedelta(hours=24)
    CARD_INTENT_DAILY_LIMIT = 20
    CARD_CANCEL_BURST_LIMIT = 3
    CARD_CANCEL_COOLDOWN = timedelta(hours=1)
    TELEGRAM_SAFE_MESSAGE_LENGTH = 3_900
    ORDER_TRANSITIONS: Mapping[str, frozenset[str]] = {
        "pending_payment": frozenset(
            {"awaiting_confirmation", "rejected", "expired", "cancelled"}
        ),
        "awaiting_confirmation": frozenset({"rejected", "expired", "cancelled"}),
        # These states are reached only after money has been captured. A
        # terminal cancellation/rejection would strand it; a future refund
        # feature must use a separate, proven ledger-reversal workflow.
        "awaiting_stock": frozenset({"processing"}),
        "awaiting_info": frozenset({"processing"}),
        "paid": frozenset({"awaiting_stock", "awaiting_info", "processing"}),
        "processing": frozenset({"awaiting_info"}),
        "completed": frozenset(),
        "rejected": frozenset(),
        "expired": frozenset(),
        "cancelled": frozenset(),
        "refunded": frozenset(),
    }
    PAYMENT_TRANSITIONS: Mapping[str, frozenset[str]] = {
        "pending": frozenset({"verifying", "paid", "failed", "cancelled", "expired"}),
        "verifying": frozenset({"paid", "failed", "cancelled", "expired"}),
        "paid": frozenset(),
        "failed": frozenset(),
        "cancelled": frozenset(),
        "expired": frozenset(),
        "refunded": frozenset(),
    }

    @classmethod
    def _validate_ready_delivery_text(
        cls,
        *,
        product_name: str,
        product_icon: str | None,
        payload: str,
        delivery_instructions: str | None,
        order_number: str = "ORD-YYYYMMDD-XXXXXXXXXXXXXXXX",
    ) -> None:
        """Reject stock that cannot fit in one safe Telegram delivery."""

        # Local import keeps the persistence module independent at import time
        # while reusing the exact production renderer (including HTML escaping).
        from . import texts

        rendered = texts.ready_delivery(
            {
                "order_no": order_number,
                "product_title": product_name,
                "product_icon": product_icon or "",
            },
            str(payload),
            str(delivery_instructions or ""),
        )
        if len(rendered) > cls.TELEGRAM_SAFE_MESSAGE_LENGTH:
            raise ValidationError(
                "ready delivery exceeds Telegram limit; shorten inventory or instructions"
            )

    def __init__(
        self,
        path: str | Path,
        *,
        busy_timeout_ms: int = 5_000,
        schema_path: str | Path | None = None,
        reminder_timezone: str = "Asia/Tehran",
    ) -> None:
        if str(path) == ":memory:":
            raise ValueError("per-operation connections require a file-backed SQLite database")
        if busy_timeout_ms < 1:
            raise ValueError("busy_timeout_ms must be positive")
        self.path = Path(path).expanduser().resolve()
        self.busy_timeout_ms = int(busy_timeout_ms)
        self.reminder_timezone = ZoneInfo(reminder_timezone)
        self.schema_path = (
            Path(schema_path).expanduser().resolve()
            if schema_path is not None
            else Path(__file__).with_name("schema.sql")
        )

    def initialize(self) -> None:
        """Create or migrate all currently known objects, idempotently."""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        schema = self.schema_path.read_text(encoding="utf-8")
        connection = self._connect()
        try:
            connection.executescript(schema)
            self._migrate_schema(connection)
        finally:
            connection.close()

    @staticmethod
    def _migrate_schema(connection: sqlite3.Connection) -> None:
        """Apply additive migrations needed by databases from earlier releases."""

        connection.execute("BEGIN IMMEDIATE")
        try:
            category_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(categories)").fetchall()
            }
            if "icon" not in category_columns:
                connection.execute("ALTER TABLE categories ADD COLUMN icon TEXT")
            if "description" not in category_columns:
                connection.execute("ALTER TABLE categories ADD COLUMN description TEXT")
            if "source_admin_update_id" not in category_columns:
                connection.execute(
                    "ALTER TABLE categories ADD COLUMN source_admin_update_id INTEGER"
                )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_categories_admin_update "
                "ON categories(source_admin_update_id) "
                "WHERE source_admin_update_id IS NOT NULL"
            )
            inventory_columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(inventory_items)"
                ).fetchall()
            }
            if "source_admin_update_id" not in inventory_columns:
                connection.execute(
                    "ALTER TABLE inventory_items ADD COLUMN source_admin_update_id INTEGER"
                )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_inventory_admin_update "
                "ON inventory_items(source_admin_update_id) "
                "WHERE source_admin_update_id IS NOT NULL"
            )
            admin_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(admins)").fetchall()
            }
            if "identity_verified_at" not in admin_columns:
                connection.execute(
                    "ALTER TABLE admins ADD COLUMN identity_verified_at TEXT"
                )
                connection.execute(
                    """
                    UPDATE admins AS admin
                    SET identity_verified_at = updated_at
                    WHERE chat_id IS NOT NULL
                      AND EXISTS (
                          SELECT 1 FROM users AS user
                          WHERE user.chat_id = admin.chat_id
                            AND user.username_key = admin.username_key
                      )
                    """
                )
            if "is_bootstrap_owner" not in admin_columns:
                connection.execute(
                    "ALTER TABLE admins ADD COLUMN is_bootstrap_owner INTEGER "
                    "NOT NULL DEFAULT 0 CHECK (is_bootstrap_owner IN (0, 1))"
                )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_admin_bootstrap_owner "
                "ON admins(is_bootstrap_owner) WHERE is_bootstrap_owner = 1"
            )
            ticket_message_columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(ticket_messages)"
                ).fetchall()
            }
            if "attachment_kind" not in ticket_message_columns:
                connection.execute(
                    "ALTER TABLE ticket_messages ADD COLUMN attachment_kind TEXT"
                )
                connection.execute(
                    """
                    UPDATE ticket_messages SET attachment_kind = 'document'
                    WHERE attachment_file_id IS NOT NULL
                    """
                )
            order_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(orders)").fetchall()
            }
            if "reward_processed_at" not in order_columns:
                connection.execute(
                    "ALTER TABLE orders ADD COLUMN reward_processed_at TEXT"
                )
            if "order_origin" not in order_columns:
                connection.execute(
                    "ALTER TABLE orders ADD COLUMN order_origin TEXT NOT NULL "
                    "DEFAULT 'customer' CHECK (order_origin IN "
                    "('customer', 'admin_assignment'))"
                )
                connection.execute(
                    """
                    UPDATE orders
                    SET order_origin = 'admin_assignment'
                    WHERE order_number LIKE 'ADM-%'
                       OR idempotency_key LIKE 'admin-inventory:%'
                    """
                )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_orders_reward_pending
                ON orders(status, id)
                WHERE reward_processed_at IS NULL
                  AND status IN (
                      'paid','awaiting_stock','awaiting_info','processing','completed'
                  )
                """
            )
            backup_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(backups)").fetchall()
            }
            if "path" not in backup_columns and "destination_path" in backup_columns:
                # A short-lived legacy schema named this field
                # ``destination_path``. Rebuild the table so current backup
                # operations and future fresh databases share one shape.
                connection.execute("DROP INDEX IF EXISTS idx_backups_created")
                connection.execute("ALTER TABLE backups RENAME TO backups_legacy_v5")
                connection.execute(
                    """
                    CREATE TABLE backups (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        path TEXT NOT NULL,
                        status TEXT NOT NULL
                            CHECK (status IN ('running', 'completed', 'failed')),
                        sha256 TEXT,
                        size_bytes INTEGER CHECK (size_bytes IS NULL OR size_bytes >= 0),
                        error_text TEXT,
                        created_at TEXT NOT NULL,
                        completed_at TEXT
                    )
                    """
                )
                connection.execute(
                    f"""
                    INSERT INTO backups(
                        id, path, status, sha256, size_bytes, error_text,
                        created_at, completed_at
                    )
                    SELECT id, destination_path, status,
                           {"sha256" if "sha256" in backup_columns else "NULL"},
                           {"size_bytes" if "size_bytes" in backup_columns else "NULL"},
                           {"error_text" if "error_text" in backup_columns else "NULL"},
                           created_at,
                           {"completed_at" if "completed_at" in backup_columns else "NULL"}
                    FROM backups_legacy_v5
                    """
                )
                connection.execute("DROP TABLE backups_legacy_v5")
                connection.execute(
                    """
                    CREATE INDEX idx_backups_created
                    ON backups(created_at DESC, id DESC)
                    """
                )
                backup_columns = {
                    str(row["name"])
                    for row in connection.execute("PRAGMA table_info(backups)").fetchall()
                }
            if "sha256" not in backup_columns:
                connection.execute("ALTER TABLE backups ADD COLUMN sha256 TEXT")
            if "size_bytes" not in backup_columns:
                connection.execute(
                    "ALTER TABLE backups ADD COLUMN size_bytes INTEGER "
                    "CHECK (size_bytes IS NULL OR size_bytes >= 0)"
                )
            if "error_text" not in backup_columns:
                connection.execute("ALTER TABLE backups ADD COLUMN error_text TEXT")
            if "completed_at" not in backup_columns:
                connection.execute("ALTER TABLE backups ADD COLUMN completed_at TEXT")
            # Crypto invoices are correlated by their unique provider ID, not
            # by a user-facing rounded amount. Only card callbacks need global
            # active amount uniqueness.
            connection.execute("DROP INDEX IF EXISTS uq_active_payment_payable_amount")
            connection.execute(
                """
                CREATE UNIQUE INDEX uq_active_payment_payable_amount
                ON payments(method, currency, payable_amount)
                WHERE method = 'card' AND status IN ('pending', 'verifying')
                """
            )
            resolution_table = connection.execute(
                """
                SELECT sql FROM sqlite_master
                WHERE type = 'table'
                  AND name = 'provider_payment_event_resolutions'
                """
            ).fetchone()
            if resolution_table is not None and "credit_confirmed" not in str(
                resolution_table["sql"] or ""
            ):
                # SQLite cannot extend a CHECK constraint in place.  Rebuild
                # the append-only resolution table while preserving every
                # existing provider/manual decision from schema v6.
                connection.execute(
                    "DROP TRIGGER IF EXISTS provider_payment_event_resolutions_no_update"
                )
                connection.execute(
                    "DROP TRIGGER IF EXISTS provider_payment_event_resolutions_no_delete"
                )
                connection.execute(
                    """
                    ALTER TABLE provider_payment_event_resolutions
                    RENAME TO provider_payment_event_resolutions_v6
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE provider_payment_event_resolutions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        event_id INTEGER NOT NULL UNIQUE
                            REFERENCES provider_payment_events(id) ON DELETE RESTRICT,
                        action TEXT NOT NULL
                            CHECK (action IN (
                                'refund_confirmed', 'dismiss', 'credit_confirmed',
                                'provider_completed', 'provider_terminal_zero'
                            )),
                        actor_admin_id INTEGER
                            REFERENCES admins(id) ON DELETE RESTRICT,
                        resolving_event_id INTEGER
                            REFERENCES provider_payment_events(id) ON DELETE RESTRICT,
                        note TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        CHECK (
                            (action IN (
                                'refund_confirmed', 'dismiss', 'credit_confirmed'
                            ) AND actor_admin_id IS NOT NULL
                              AND resolving_event_id IS NULL)
                            OR
                            (action IN (
                                'provider_completed', 'provider_terminal_zero'
                            ) AND actor_admin_id IS NULL
                              AND resolving_event_id IS NOT NULL)
                        )
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO provider_payment_event_resolutions(
                        id, event_id, action, actor_admin_id,
                        resolving_event_id, note, created_at
                    )
                    SELECT id, event_id, action, actor_admin_id,
                           resolving_event_id, note, created_at
                    FROM provider_payment_event_resolutions_v6
                    """
                )
                connection.execute("DROP TABLE provider_payment_event_resolutions_v6")
                connection.execute(
                    """
                    CREATE TRIGGER provider_payment_event_resolutions_no_update
                    BEFORE UPDATE ON provider_payment_event_resolutions
                    BEGIN
                        SELECT RAISE(
                            ABORT,
                            'provider payment event resolutions are immutable'
                        );
                    END
                    """
                )
                connection.execute(
                    """
                    CREATE TRIGGER provider_payment_event_resolutions_no_delete
                    BEFORE DELETE ON provider_payment_event_resolutions
                    BEGIN
                        SELECT RAISE(
                            ABORT,
                            'provider payment event resolutions are immutable'
                        );
                    END
                    """
                )
            admin_update_columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(processed_admin_updates)"
                ).fetchall()
            }
            if "fingerprint" not in admin_update_columns:
                connection.execute(
                    "ALTER TABLE processed_admin_updates ADD COLUMN fingerprint TEXT"
                )
                connection.execute(
                    "UPDATE processed_admin_updates SET fingerprint = "
                    "'legacy:' || update_id WHERE fingerprint IS NULL"
                )
            if "status" not in admin_update_columns:
                connection.execute(
                    "ALTER TABLE processed_admin_updates ADD COLUMN status TEXT "
                    "NOT NULL DEFAULT 'completed' "
                    "CHECK (status IN ('started', 'completed'))"
                )
            if "effect_json" not in admin_update_columns:
                connection.execute(
                    "ALTER TABLE processed_admin_updates ADD COLUMN effect_json TEXT"
                )
            if "updated_at" not in admin_update_columns:
                connection.execute(
                    "ALTER TABLE processed_admin_updates ADD COLUMN updated_at TEXT"
                )
                connection.execute(
                    "UPDATE processed_admin_updates SET updated_at = created_at "
                    "WHERE updated_at IS NULL"
                )
            if "completed_at" not in admin_update_columns:
                connection.execute(
                    "ALTER TABLE processed_admin_updates ADD COLUMN completed_at TEXT"
                )
                connection.execute(
                    "UPDATE processed_admin_updates SET completed_at = created_at "
                    "WHERE status = 'completed' AND completed_at IS NULL"
                )
            connection.execute(
                """
                INSERT INTO schema_meta(key, value) VALUES ('schema_version', '11')
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=self.busy_timeout_ms / 1_000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _required(
        connection: sqlite3.Connection,
        query: str,
        parameters: Sequence[Any],
        entity: str,
    ) -> sqlite3.Row:
        row = connection.execute(query, parameters).fetchone()
        if row is None:
            raise NotFoundError(f"{entity} not found")
        return row

    # -- Users and administrator identities ---------------------------------

    def upsert_user(
        self,
        telegram_user_id: int,
        chat_id: int,
        *,
        username: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        phone: str | None = None,
        email: str | None = None,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        stamp = _timestamp(now)
        username_display: str | None = None
        username_key: str | None = None
        if username:
            username_display, username_key = _username_parts(username)
        with self._transaction() as connection:
            matches = connection.execute(
                "SELECT * FROM users WHERE telegram_user_id = ? OR chat_id = ?",
                (int(telegram_user_id), int(chat_id)),
            ).fetchall()
            if len(matches) > 1:
                raise ConflictError("telegram_user_id and chat_id identify different users")
            if matches:
                user_id = matches[0]["id"]
                if username_key is not None:
                    connection.execute(
                        """
                        UPDATE users
                        SET username = NULL, username_key = NULL, updated_at = ?
                        WHERE username_key = ? AND id <> ?
                        """,
                        (stamp, username_key, int(user_id)),
                    )
                connection.execute(
                    """
                    UPDATE users
                    SET telegram_user_id = ?, chat_id = ?,
                        username = ?, username_key = ?,
                        first_name = COALESCE(?, first_name),
                        last_name = COALESCE(?, last_name),
                        phone = COALESCE(?, phone), email = COALESCE(?, email),
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        int(telegram_user_id),
                        int(chat_id),
                        username_display,
                        username_key,
                        first_name,
                        last_name,
                        phone,
                        email,
                        stamp,
                        user_id,
                    ),
                )
            else:
                if username_key is not None:
                    connection.execute(
                        """
                        UPDATE users
                        SET username = NULL, username_key = NULL, updated_at = ?
                        WHERE username_key = ?
                        """,
                        (stamp, username_key),
                    )
                cursor = connection.execute(
                    """
                    INSERT INTO users(
                        telegram_user_id, chat_id, username, username_key,
                        first_name, last_name, phone, email,
                        joined_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(telegram_user_id),
                        int(chat_id),
                        username_display,
                        username_key,
                        first_name,
                        last_name,
                        phone,
                        email,
                        stamp,
                        stamp,
                        stamp,
                    ),
                )
                user_id = cursor.lastrowid
            return dict(self._required(connection, "SELECT * FROM users WHERE id = ?", (user_id,), "user"))

    def get_user(self, user_id: int) -> dict[str, Any] | None:
        with self._read() as connection:
            return _row(connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())

    def get_user_by_chat_id(self, chat_id: int) -> dict[str, Any] | None:
        with self._read() as connection:
            return _row(connection.execute("SELECT * FROM users WHERE chat_id = ?", (chat_id,)).fetchone())

    def get_user_by_telegram_id(self, telegram_user_id: int) -> dict[str, Any] | None:
        with self._read() as connection:
            return _row(
                connection.execute(
                    "SELECT * FROM users WHERE telegram_user_id = ?",
                    (telegram_user_id,),
                ).fetchone()
            )

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        _, username_key = _username_parts(username)
        with self._read() as connection:
            matches = connection.execute(
                "SELECT * FROM users WHERE username_key = ? ORDER BY id",
                (username_key,),
            ).fetchall()
        if len(matches) > 1:
            raise ConflictError("username identifies multiple legacy users")
        return dict(matches[0]) if matches else None

    def update_user_profile(
        self,
        user_id: int,
        *,
        customer_name: str | None = None,
        phone: str | None = None,
        email: str | None = None,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        """Save purchase profile data separately from Telegram display fields."""

        if customer_name is None and phone is None and email is None:
            user = self.get_user(user_id)
            if user is None:
                raise NotFoundError("user not found")
            return user
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE users SET
                    customer_name = COALESCE(?, customer_name),
                    phone = COALESCE(?, phone), email = COALESCE(?, email),
                    updated_at = ?
                WHERE id = ?
                """,
                (customer_name, phone, email, _timestamp(now), user_id),
            )
            if cursor.rowcount != 1:
                raise NotFoundError("user not found")
            return dict(self._required(connection, "SELECT * FROM users WHERE id = ?", (user_id,), "user"))

    def list_users(
        self,
        *,
        search: str | None = None,
        blocked: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 1_000))
        offset = max(0, int(offset))
        clauses: list[str] = []
        parameters: list[Any] = []
        if search:
            term = f"%{search.strip().lstrip('@').casefold()}%"
            clauses.append(
                "(username_key LIKE ? OR CAST(chat_id AS TEXT) LIKE ? "
                "OR COALESCE(first_name, '') LIKE ? OR COALESCE(last_name, '') LIKE ?)"
            )
            parameters.extend((term, term, term, term))
        if blocked is not None:
            clauses.append("is_blocked = ?")
            parameters.append(int(bool(blocked)))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.extend((limit, offset))
        with self._read() as connection:
            result = connection.execute(
                f"SELECT * FROM users{where} ORDER BY id DESC LIMIT ? OFFSET ?",
                parameters,
            ).fetchall()
            return _rows(result)

    def count_users(
        self,
        *,
        search: str | None = None,
        blocked: bool | None = None,
    ) -> int:
        clauses: list[str] = []
        parameters: list[Any] = []
        if search:
            term = f"%{search.strip().lstrip('@').casefold()}%"
            clauses.append(
                "(username_key LIKE ? OR CAST(chat_id AS TEXT) LIKE ? "
                "OR COALESCE(first_name, '') LIKE ? OR COALESCE(last_name, '') LIKE ?)"
            )
            parameters.extend((term, term, term, term))
        if blocked is not None:
            clauses.append("is_blocked = ?")
            parameters.append(int(bool(blocked)))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._read() as connection:
            return int(
                connection.execute(
                    f"SELECT COUNT(*) FROM users{where}", parameters
                ).fetchone()[0]
            )

    def set_user_blocked(self, user_id: int, blocked: bool, *, now: datetime | str | None = None) -> dict[str, Any]:
        stamp = _timestamp(now)
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE users SET is_blocked = ?, updated_at = ? WHERE id = ?",
                (int(bool(blocked)), stamp, user_id),
            )
            if cursor.rowcount != 1:
                raise NotFoundError("user not found")
            return dict(self._required(connection, "SELECT * FROM users WHERE id = ?", (user_id,), "user"))

    def user_summary(self, user_id: int) -> dict[str, Any]:
        with self._read() as connection:
            user = self._required(connection, "SELECT * FROM users WHERE id = ?", (user_id,), "user")
            order_stats = connection.execute(
                """
                SELECT COUNT(*) AS order_count,
                       COALESCE(SUM(CASE WHEN status IN (
                           'paid','awaiting_stock','awaiting_info','processing','completed'
                       ) AND order_origin = 'customer' AND subtotal_amount > 0
                           THEN 1 ELSE 0 END), 0)
                           AS successful_order_count,
                       COALESCE(SUM(CASE WHEN status IN (
                           'paid','awaiting_stock','awaiting_info','processing','completed'
                       ) AND order_origin = 'customer' AND subtotal_amount > 0
                                         THEN subtotal_amount - discount_amount ELSE 0 END), 0)
                           AS purchase_total,
                       MIN(CASE WHEN status IN (
                           'paid','awaiting_stock','awaiting_info','processing','completed'
                       ) AND order_origin = 'customer' AND subtotal_amount > 0
                              AND paid_at IS NOT NULL THEN paid_at END)
                           AS first_purchase_at,
                       MAX(CASE WHEN status IN (
                           'paid','awaiting_stock','awaiting_info','processing','completed'
                       ) AND order_origin = 'customer' AND subtotal_amount > 0
                              AND paid_at IS NOT NULL THEN paid_at END)
                           AS last_purchase_at
                FROM orders WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
            last_order_at = connection.execute(
                "SELECT MAX(created_at) FROM orders WHERE user_id = ?",
                (user_id,),
            ).fetchone()[0]
            referral_stats = connection.execute(
                "SELECT COUNT(*) FROM referrals WHERE inviter_user_id = ?",
                (user_id,),
            ).fetchone()[0]
            referral_rewards = connection.execute(
                """
                SELECT COALESCE(SUM(re.amount), 0) FROM reward_events re
                JOIN referrals r ON r.id = re.referral_id WHERE r.inviter_user_id = ?
                """,
                (user_id,),
            ).fetchone()[0]
            values = dict(order_stats)
            return {
                "user": dict(user),
                "wallet_balance": self._wallet_balance(connection, user_id),
                **values,
                "total_paid": int(values["purchase_total"]),
                "last_order_at": last_order_at,
                "referral_count": int(referral_stats),
                "referral_rewards": int(referral_rewards),
            }

    def bootstrap_admin(
        self,
        username: str,
        chat_id: int | None = None,
        *,
        role: str = "owner",
        active: bool = True,
        created_by_admin_id: int | None = None,
        require_identity_match: bool = False,
        bootstrap_root: bool = False,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        """Idempotently create/update an admin using both stable identity hints."""

        if role not in self.ADMIN_ROLES:
            raise ValidationError(f"unsupported admin role: {role}")
        display, key = _username_parts(username)
        stamp = _timestamp(now)
        with self._transaction() as connection:
            if bootstrap_root:
                if role != "owner" or created_by_admin_id is not None:
                    raise ValidationError("bootstrap root must be an owner")
                root = connection.execute(
                    "SELECT * FROM admins WHERE is_bootstrap_owner = 1"
                ).fetchone()
                if root is not None:
                    if (
                        chat_id is not None
                        and root["chat_id"] is not None
                        and int(root["chat_id"]) != int(chat_id)
                    ):
                        # An explicit configuration rotation may move the
                        # stable marker only to an already-proven, active
                        # owner.  It never reactivates the old root or grants
                        # a fresh identity implicitly.
                        target = connection.execute(
                            "SELECT * FROM admins WHERE chat_id = ?",
                            (int(chat_id),),
                        ).fetchone()
                        if (
                            target is None
                            or target["identity_verified_at"] is None
                            or not bool(target["is_active"])
                            or target["role"] != "owner"
                        ):
                            raise ConflictError(
                                "configured bootstrap chat_id differs from the stable root identity"
                            )
                        connection.execute(
                            "UPDATE admins SET is_bootstrap_owner = 0 WHERE id = ?",
                            (int(root["id"]),),
                        )
                        connection.execute(
                            "UPDATE admins SET is_bootstrap_owner = 1, updated_at = ? "
                            "WHERE id = ?",
                            (stamp, int(target["id"])),
                        )
                        return dict(
                            self._required(
                                connection,
                                "SELECT * FROM admins WHERE id = ?",
                                (int(target["id"]),),
                                "admin",
                            )
                        )
                    if root["chat_id"] is None and chat_id is not None:
                        collision = connection.execute(
                            "SELECT * FROM admins WHERE chat_id = ? AND id <> ?",
                            (int(chat_id), int(root["id"])),
                        ).fetchone()
                        if collision is not None:
                            if (
                                collision["identity_verified_at"] is None
                                or not bool(collision["is_active"])
                                or collision["role"] != "owner"
                            ):
                                raise ConflictError(
                                    "configured bootstrap chat_id belongs to another admin"
                                )
                            connection.execute(
                                "UPDATE admins SET is_bootstrap_owner = 0 WHERE id = ?",
                                (int(root["id"]),),
                            )
                            connection.execute(
                                "UPDATE admins SET is_bootstrap_owner = 1, updated_at = ? "
                                "WHERE id = ?",
                                (stamp, int(collision["id"])),
                            )
                            return dict(
                                self._required(
                                    connection,
                                    "SELECT * FROM admins WHERE id = ?",
                                    (int(collision["id"]),),
                                    "admin",
                                )
                            )
                        connection.execute(
                            """
                            UPDATE admins
                            SET username = ?, username_key = ?, chat_id = ?,
                                identity_verified_at = ?, updated_at = ?
                            WHERE id = ?
                            """,
                            (display, key, int(chat_id), stamp, stamp, int(root["id"])),
                        )
                    # Once seeded, role/active state belongs to the database.
                    # A restart must not silently reactivate a deliberately
                    # disabled or demoted bootstrap identity.
                    return dict(
                        self._required(
                            connection,
                            "SELECT * FROM admins WHERE id = ?",
                            (int(root["id"]),),
                            "admin",
                        )
                    )
                # First marker adoption for databases created by an older
                # release. Prefer the configured stable chat, then username;
                # without a chat hint, a single proven owner is a safe anchor.
                if chat_id is not None:
                    root = connection.execute(
                        "SELECT * FROM admins WHERE chat_id = ?",
                        (int(chat_id),),
                    ).fetchone()
                if root is None:
                    root = connection.execute(
                        "SELECT * FROM admins WHERE username_key = ?",
                        (key,),
                    ).fetchone()
                if root is not None and chat_id is not None and (
                    root["chat_id"] is not None
                    and int(root["chat_id"]) != int(chat_id)
                ):
                    raise ConflictError(
                        "configured bootstrap chat_id differs from the stable root identity"
                    )
                if root is not None and (
                    root["role"] != "owner"
                    or not bool(root["is_active"])
                    or (
                        root["chat_id"] is not None
                        and root["identity_verified_at"] is None
                    )
                ):
                    raise ConflictError(
                        "configured bootstrap identity is not a proven active owner"
                    )
                if root is None:
                    verified_owners = connection.execute(
                        """
                        SELECT * FROM admins
                        WHERE role = 'owner' AND is_active = 1
                          AND identity_verified_at IS NOT NULL
                        ORDER BY id
                        """
                    ).fetchall()
                    if chat_id is not None and verified_owners:
                        # A stable configured chat that matches none of the
                        # proven owners is a configuration conflict, not a
                        # reason to synthesize a second root.
                        raise ConflictError(
                            "configured bootstrap chat_id differs from the stable root identity"
                        )
                    if len(verified_owners) == 1:
                        root = verified_owners[0]
                    elif len(verified_owners) > 1:
                        raise ConflictError(
                            "cannot infer bootstrap root from multiple verified owners"
                        )
                if root is not None:
                    if root["chat_id"] is None and chat_id is not None:
                        collision = connection.execute(
                            "SELECT id FROM admins WHERE chat_id = ? AND id <> ?",
                            (int(chat_id), int(root["id"])),
                        ).fetchone()
                        if collision is not None:
                            raise ConflictError(
                                "configured bootstrap chat_id belongs to another admin"
                            )
                        connection.execute(
                            """
                            UPDATE admins
                            SET username = ?, username_key = ?, chat_id = ?,
                                role = 'owner', is_active = 1,
                                identity_verified_at = ?, updated_at = ?
                            WHERE id = ?
                            """,
                            (display, key, int(chat_id), stamp, stamp, int(root["id"])),
                        )
                    connection.execute(
                        "UPDATE admins SET is_bootstrap_owner = 1 WHERE id = ?",
                        (int(root["id"]),),
                    )
                    return dict(
                        self._required(
                            connection,
                            "SELECT * FROM admins WHERE id = ?",
                            (int(root["id"]),),
                            "admin",
                        )
                    )
            identity_verified_at = stamp if chat_id is not None else None
            if require_identity_match:
                if chat_id is None:
                    raise ValidationError("delegated administrator requires a chat_id")
                chat_user = connection.execute(
                    "SELECT * FROM users WHERE chat_id = ?",
                    (int(chat_id),),
                ).fetchone()
                username_user = connection.execute(
                    "SELECT * FROM users WHERE username_key = ?",
                    (key,),
                ).fetchone()
                if chat_user is not None and chat_user["username_key"] != key:
                    raise ConflictError(
                        "chat_id belongs to a user with a different Telegram username"
                    )
                if username_user is not None and int(username_user["chat_id"]) != int(
                    chat_id
                ):
                    raise ConflictError(
                        "Telegram username belongs to a user with a different chat_id"
                    )
                identity_verified_at = (
                    stamp
                    if chat_user is not None and username_user is not None
                    else None
                )
            matches = connection.execute(
                "SELECT * FROM admins WHERE username_key = ? OR chat_id = ?",
                (key, int(chat_id) if chat_id is not None else None),
            ).fetchall()
            if len(matches) > 1:
                raise ConflictError("username and chat_id identify different admins")
            if matches:
                current = matches[0]
                identity_is_verified = current["identity_verified_at"] is not None
                if current["username_key"] != key and (
                    identity_is_verified or not require_identity_match
                ):
                    raise ConflictError("chat_id is already bound to another admin username")
                if (
                    chat_id is not None
                    and current["chat_id"] is not None
                    and int(current["chat_id"]) != int(chat_id)
                    and (identity_is_verified or not require_identity_match)
                ):
                    raise ConflictError("admin username is already bound to a different chat_id")
                if (
                    current["role"] == "owner"
                    and bool(current["is_active"])
                    and current["identity_verified_at"] is not None
                    and (role != "owner" or not active)
                ):
                    other_active_owners = int(
                        connection.execute(
                            "SELECT COUNT(*) FROM admins "
                            "WHERE role = 'owner' AND is_active = 1 "
                            "AND identity_verified_at IS NOT NULL AND id <> ?",
                            (int(current["id"]),),
                        ).fetchone()[0]
                    )
                    if other_active_owners == 0:
                        raise ConflictError("the last active owner cannot be demoted or disabled")
                admin_id = current["id"]
                connection.execute(
                    """
                    UPDATE admins
                    SET username = ?, username_key = ?, chat_id = COALESCE(?, chat_id), role = ?,
                        is_active = ?, identity_verified_at = COALESCE(
                            ?, identity_verified_at
                        ), updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        display,
                        key,
                        int(chat_id) if chat_id is not None else None,
                        role,
                        int(bool(active)),
                        identity_verified_at,
                        stamp,
                        admin_id,
                    ),
                )
            else:
                cursor = connection.execute(
                    """
                    INSERT INTO admins(
                        username, username_key, chat_id, role, is_active,
                        identity_verified_at,
                        created_by_admin_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        display,
                        key,
                        int(chat_id) if chat_id is not None else None,
                        role,
                        int(bool(active)),
                        identity_verified_at,
                        created_by_admin_id,
                        stamp,
                        stamp,
                    ),
                )
                admin_id = cursor.lastrowid
            if bootstrap_root:
                connection.execute(
                    "UPDATE admins SET is_bootstrap_owner = 1 WHERE id = ?",
                    (int(admin_id),),
                )
            return dict(self._required(connection, "SELECT * FROM admins WHERE id = ?", (admin_id,), "admin"))

    def add_admin(
        self,
        username: str,
        chat_id: int,
        *,
        role: str = "admin",
        active: bool = True,
        created_by_admin_id: int | None = None,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        """Create a delegated grant, pending until Telegram identity is proven."""

        return self.bootstrap_admin(
            username,
            chat_id,
            role=role,
            active=active,
            created_by_admin_id=created_by_admin_id,
            require_identity_match=True,
            now=now,
        )

    def bind_admin_chat(
        self,
        username: str,
        chat_id: int,
        *,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        """Safely bind a username-only administrator on first interaction."""

        display, key = _username_parts(username)
        stamp = _timestamp(now)
        with self._transaction() as connection:
            identity = connection.execute(
                "SELECT * FROM users WHERE chat_id = ?",
                (int(chat_id),),
            ).fetchone()
            if identity is None or identity["username_key"] != key:
                raise ConflictError(
                    "chat_id and Telegram username have not been proven by a matching update"
                )
            matches = connection.execute(
                "SELECT * FROM admins WHERE username_key = ? OR chat_id = ?",
                (key, int(chat_id)),
            ).fetchall()
            if len(matches) > 1:
                raise ConflictError(
                    "username and chat_id identify different administrators"
                )
            if not matches:
                raise NotFoundError("admin not found")
            admin = matches[0]
            if admin["username_key"] != key and admin["identity_verified_at"] is None:
                raise ConflictError(
                    "pending admin grant requires the originally assigned username"
                )
            chat_owner = connection.execute("SELECT id FROM admins WHERE chat_id = ?", (int(chat_id),)).fetchone()
            if chat_owner is not None and chat_owner["id"] != admin["id"]:
                raise ConflictError("chat_id is already bound to another admin")
            if admin["chat_id"] is not None and int(admin["chat_id"]) != int(chat_id):
                raise ConflictError("admin username is already bound to a different chat_id")
            connection.execute(
                "UPDATE admins SET username = ?, username_key = ?, chat_id = ?, "
                "identity_verified_at = ?, updated_at = ? WHERE id = ?",
                (display, key, int(chat_id), stamp, stamp, admin["id"]),
            )
            return dict(self._required(connection, "SELECT * FROM admins WHERE id = ?", (admin["id"],), "admin"))

    def is_admin(
        self,
        *,
        username: str | None = None,
        chat_id: int | None = None,
        roles: Sequence[str] | None = None,
    ) -> bool:
        if username is None and chat_id is None:
            return False
        clauses: list[str] = []
        parameters: list[Any] = []
        if username is not None:
            _, key = _username_parts(username)
            clauses.append("username_key = ?")
            parameters.append(key)
        if chat_id is not None:
            clauses.append("chat_id = ?")
            parameters.append(int(chat_id))
        operator = " AND " if len(clauses) > 1 else " OR "
        query = (
            "SELECT role FROM admins WHERE is_active = 1 "
            "AND identity_verified_at IS NOT NULL AND ("
            f"{operator.join(clauses)})"
        )
        with self._read() as connection:
            rows = connection.execute(query, parameters).fetchall()
        if roles is None:
            return bool(rows)
        allowed = set(roles)
        return any(row["role"] in allowed for row in rows)

    def list_admins(self, *, active_only: bool = False) -> list[dict[str, Any]]:
        with self._read() as connection:
            query = "SELECT * FROM admins"
            if active_only:
                query += " WHERE is_active = 1 AND identity_verified_at IS NOT NULL"
            query += " ORDER BY CASE role WHEN 'owner' THEN 0 WHEN 'admin' THEN 1 ELSE 2 END, id"
            return _rows(connection.execute(query).fetchall())

    def set_admin_active(self, admin_id: int, active: bool, *, now: datetime | str | None = None) -> dict[str, Any]:
        stamp = _timestamp(now)
        with self._transaction() as connection:
            current = self._required(
                connection,
                "SELECT * FROM admins WHERE id = ?",
                (admin_id,),
                "admin",
            )
            if (
                not active
                and current["role"] == "owner"
                and bool(current["is_active"])
                and current["identity_verified_at"] is not None
            ):
                other_active_owners = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM admins "
                        "WHERE role = 'owner' AND is_active = 1 "
                        "AND identity_verified_at IS NOT NULL AND id <> ?",
                        (admin_id,),
                    ).fetchone()[0]
                )
                if other_active_owners == 0:
                    raise ConflictError("the last active owner cannot be disabled")
            cursor = connection.execute(
                "UPDATE admins SET is_active = ?, updated_at = ? WHERE id = ?",
                (int(bool(active)), stamp, admin_id),
            )
            if cursor.rowcount != 1:
                raise NotFoundError("admin not found")
            return dict(self._required(connection, "SELECT * FROM admins WHERE id = ?", (admin_id,), "admin"))

    # -- Settings, update offsets and conversational state ------------------

    def set_setting(self, key: str, value: Any, *, now: datetime | str | None = None) -> Any:
        if not key.strip():
            raise ValidationError("setting key cannot be empty")
        stamp = _timestamp(now)
        encoded = _json_dump(value)
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO settings(key, value_json, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE
                SET value_json = excluded.value_json, updated_at = excluded.updated_at
                """,
                (key, encoded, stamp),
            )
        return value

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self._read() as connection:
            row = connection.execute("SELECT value_json FROM settings WHERE key = ?", (key,)).fetchone()
        return default if row is None else _json_load(row["value_json"])

    def save_update_offset(self, offset: int, *, now: datetime | str | None = None) -> int:
        """Persist a monotonic getUpdates offset; retries cannot move it back."""

        requested = int(offset)
        stamp = _timestamp(now)
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT value_json FROM settings WHERE key = 'telegram_update_offset'"
            ).fetchone()
            current = int(_json_load(existing["value_json"])) if existing else -1
            stored = max(current, requested)
            connection.execute(
                """
                INSERT INTO settings(key, value_json, updated_at) VALUES ('telegram_update_offset', ?, ?)
                ON CONFLICT(key) DO UPDATE
                SET value_json = excluded.value_json, updated_at = excluded.updated_at
                """,
                (_json_dump(stored), stamp),
            )
            return stored

    def get_update_offset(self, default: int = 0) -> int:
        return int(self.get_setting("telegram_update_offset", default))

    def begin_admin_update(
        self,
        update_id: int,
        fingerprint: str,
        *,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        """Start or resume one authenticated admin update.

        Telegram may replay an update after a hard process stop but before the
        getUpdates offset is saved.  A completed row is skipped, while a
        started row is deliberately resumed.  The payload fingerprint prevents
        an update id from ever being reused for different input.
        """

        update_id = int(update_id)
        clean_fingerprint = str(fingerprint).strip()
        if not clean_fingerprint:
            raise ValidationError("admin update fingerprint is required")
        stamp = _timestamp(now)
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM processed_admin_updates WHERE update_id = ?",
                (update_id,),
            ).fetchone()
            if row is not None:
                if str(row["fingerprint"]) != clean_fingerprint:
                    raise ConflictError(
                        "admin update id belongs to a different payload"
                    )
                connection.execute(
                    "UPDATE processed_admin_updates SET updated_at = ? "
                    "WHERE update_id = ?",
                    (stamp, update_id),
                )
                result = dict(row)
                result["should_process"] = row["status"] != "completed"
                result["is_replay"] = row["status"] == "started"
                return result
            connection.execute(
                """
                INSERT INTO processed_admin_updates(
                    update_id, fingerprint, status, created_at, updated_at
                ) VALUES (?, ?, 'started', ?, ?)
                """,
                (update_id, clean_fingerprint, stamp, stamp),
            )
            result = dict(
                self._required(
                    connection,
                    "SELECT * FROM processed_admin_updates WHERE update_id = ?",
                    (update_id,),
                    "admin update",
                )
            )
            result["should_process"] = True
            result["is_replay"] = False
            return result

    def get_or_store_admin_update_effect(
        self,
        update_id: int,
        effect_key: str,
        proposed_value: Any,
        *,
        now: datetime | str | None = None,
    ) -> Any:
        """Freeze a non-idempotent effect's intended value before mutation."""

        key = str(effect_key).strip()
        if not key:
            raise ValidationError("admin effect key is required")
        stamp = _timestamp(now)
        with self._transaction() as connection:
            row = self._required(
                connection,
                "SELECT * FROM processed_admin_updates WHERE update_id = ?",
                (int(update_id),),
                "admin update",
            )
            if row["status"] == "completed":
                raise ConflictError("completed admin update cannot add an effect")
            if row["effect_json"] is not None:
                stored = _json_load(row["effect_json"], {})
                if not isinstance(stored, dict) or stored.get("key") != key:
                    raise ConflictError(
                        "admin update is already bound to another effect"
                    )
                return stored.get("value")
            encoded = _json_dump({"key": key, "value": proposed_value})
            connection.execute(
                "UPDATE processed_admin_updates SET effect_json = ?, updated_at = ? "
                "WHERE update_id = ?",
                (encoded, stamp, int(update_id)),
            )
            return proposed_value

    def complete_admin_update(
        self, update_id: int, *, now: datetime | str | None = None
    ) -> dict[str, Any]:
        """Mark a normally-returned admin handler terminal for replay."""

        stamp = _timestamp(now)
        with self._transaction() as connection:
            row = self._required(
                connection,
                "SELECT * FROM processed_admin_updates WHERE update_id = ?",
                (int(update_id),),
                "admin update",
            )
            if row["status"] != "completed":
                connection.execute(
                    "UPDATE processed_admin_updates SET status = 'completed', "
                    "completed_at = ?, updated_at = ? WHERE update_id = ?",
                    (stamp, stamp, int(update_id)),
                )
            return dict(
                self._required(
                    connection,
                    "SELECT * FROM processed_admin_updates WHERE update_id = ?",
                    (int(update_id),),
                    "admin update",
                )
            )

    def get_admin_update(self, update_id: int) -> dict[str, Any] | None:
        with self._read() as connection:
            return _row(
                connection.execute(
                    "SELECT * FROM processed_admin_updates WHERE update_id = ?",
                    (int(update_id),),
                ).fetchone()
            )

    def record_admin_update_once(
        self, update_id: int, *, now: datetime | str | None = None
    ) -> bool:
        """Backward-compatible immediate claim used only by older callers."""

        fingerprint = f"legacy:{int(update_id)}"
        state = self.begin_admin_update(update_id, fingerprint, now=now)
        if not state["should_process"]:
            return False
        self.complete_admin_update(update_id, now=now)
        return not bool(state["is_replay"])

    def set_user_state(
        self,
        user_id: int,
        state: str,
        data: Mapping[str, Any] | None = None,
        *,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        if not state.strip():
            raise ValidationError("state cannot be empty")
        stamp = _timestamp(now)
        encoded = _json_dump(dict(data or {}))
        with self._transaction() as connection:
            self._required(connection, "SELECT id FROM users WHERE id = ?", (user_id,), "user")
            connection.execute(
                """
                INSERT INTO user_states(user_id, state, data_json, updated_at) VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE
                SET state = excluded.state, data_json = excluded.data_json, updated_at = excluded.updated_at
                """,
                (user_id, state, encoded, stamp),
            )
            result = dict(self._required(connection, "SELECT * FROM user_states WHERE user_id = ?", (user_id,), "state"))
            result["data"] = _json_load(result.pop("data_json"), {})
            return result

    def get_user_state(self, user_id: int) -> dict[str, Any] | None:
        with self._read() as connection:
            result = _row(connection.execute("SELECT * FROM user_states WHERE user_id = ?", (user_id,)).fetchone())
        if result is not None:
            result["data"] = _json_load(result.pop("data_json"), {})
        return result

    def clear_user_state(self, user_id: int) -> bool:
        with self._transaction() as connection:
            return connection.execute("DELETE FROM user_states WHERE user_id = ?", (user_id,)).rowcount == 1

    # -- Channels and catalog -----------------------------------------------

    def upsert_force_join_channel(
        self,
        telegram_chat_id: int | str,
        title: str,
        *,
        invite_url: str | None = None,
        active: bool = True,
        sort_order: int = 0,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        channel_key = str(telegram_chat_id).strip()
        if not channel_key or not title.strip():
            raise ValidationError("channel id and title are required")
        if invite_url is not None and not is_safe_telegram_invite_url(invite_url):
            raise ValidationError("invite_url must be a canonical HTTPS Telegram link")
        stamp = _timestamp(now)
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO force_join_channels(
                    telegram_chat_id, title, invite_url, is_active, sort_order, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(telegram_chat_id) DO UPDATE SET
                    title = excluded.title, invite_url = excluded.invite_url,
                    is_active = excluded.is_active, sort_order = excluded.sort_order,
                    updated_at = excluded.updated_at
                """,
                (channel_key, title.strip(), invite_url, int(bool(active)), int(sort_order), stamp, stamp),
            )
            return dict(
                self._required(
                    connection,
                    "SELECT * FROM force_join_channels WHERE telegram_chat_id = ?",
                    (channel_key,),
                    "channel",
                )
            )

    def list_force_join_channels(self, *, active_only: bool = True) -> list[dict[str, Any]]:
        query = "SELECT * FROM force_join_channels"
        if active_only:
            query += " WHERE is_active = 1"
        query += " ORDER BY sort_order, id"
        with self._read() as connection:
            return _rows(connection.execute(query).fetchall())

    def set_force_join_channel_active(
        self,
        channel_id: int,
        active: bool,
        *,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE force_join_channels SET is_active = ?, updated_at = ? WHERE id = ?",
                (int(bool(active)), _timestamp(now), channel_id),
            )
            if cursor.rowcount != 1:
                raise NotFoundError("channel not found")
            return dict(
                self._required(connection, "SELECT * FROM force_join_channels WHERE id = ?", (channel_id,), "channel")
            )

    def delete_force_join_channel(self, channel_id: int) -> bool:
        with self._transaction() as connection:
            return connection.execute("DELETE FROM force_join_channels WHERE id = ?", (channel_id,)).rowcount == 1

    def create_category(
        self,
        name: str,
        *,
        parent_id: int | None = None,
        source_admin_update_id: int | None = None,
        icon: str | None = None,
        description: str | None = None,
        active: bool = True,
        sort_order: int = 0,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        clean_name = name.strip()
        if not clean_name:
            raise ValidationError("category name cannot be empty")
        stamp = _timestamp(now)
        with self._transaction() as connection:
            if source_admin_update_id is not None:
                by_update = connection.execute(
                    "SELECT * FROM categories WHERE source_admin_update_id = ?",
                    (int(source_admin_update_id),),
                ).fetchone()
                if by_update is not None:
                    expected_icon = (
                        str(icon).strip() or None if icon is not None else None
                    )
                    expected_description = (
                        str(description).strip() or None
                        if description is not None
                        else None
                    )
                    if (
                        by_update["parent_id"] != parent_id
                        or by_update["name"] != clean_name
                        or by_update["icon"] != expected_icon
                        or by_update["description"] != expected_description
                        or bool(by_update["is_active"]) != bool(active)
                        or int(by_update["sort_order"]) != int(sort_order)
                    ):
                        raise ConflictError(
                            "admin update belongs to another category"
                        )
                    return dict(by_update)
            if parent_id is not None:
                self._required(connection, "SELECT id FROM categories WHERE id = ?", (parent_id,), "parent category")
                existing = connection.execute(
                    "SELECT * FROM categories WHERE parent_id = ? AND name = ? COLLATE NOCASE",
                    (parent_id, clean_name),
                ).fetchone()
            else:
                existing = connection.execute(
                    "SELECT * FROM categories WHERE parent_id IS NULL AND name = ? COLLATE NOCASE",
                    (clean_name,),
                ).fetchone()
            if existing:
                raise ConflictError("category name already exists under this parent")
            cursor = connection.execute(
                """
                INSERT INTO categories(
                    source_admin_update_id, parent_id, name, icon, description, is_active,
                    sort_order, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(source_admin_update_id)
                    if source_admin_update_id is not None
                    else None,
                    parent_id,
                    clean_name,
                    (str(icon).strip() or None) if icon is not None else None,
                    (str(description).strip() or None)
                    if description is not None
                    else None,
                    int(bool(active)),
                    int(sort_order),
                    stamp,
                    stamp,
                ),
            )
            return dict(self._required(connection, "SELECT * FROM categories WHERE id = ?", (cursor.lastrowid,), "category"))

    def list_categories(self, *, parent_id: int | None = None, active_only: bool = True) -> list[dict[str, Any]]:
        clauses = ["parent_id IS NULL"] if parent_id is None else ["parent_id = ?"]
        parameters: list[Any] = [] if parent_id is None else [parent_id]
        if active_only:
            clauses.append("is_active = 1")
        query = f"SELECT * FROM categories WHERE {' AND '.join(clauses)} ORDER BY sort_order, id"
        with self._read() as connection:
            return _rows(connection.execute(query, parameters).fetchall())

    def get_category(self, category_id: int) -> dict[str, Any] | None:
        with self._read() as connection:
            return _row(connection.execute("SELECT * FROM categories WHERE id = ?", (category_id,)).fetchone())

    def set_category_active(
        self,
        category_id: int,
        active: bool,
        *,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE categories SET is_active = ?, updated_at = ? WHERE id = ?",
                (int(bool(active)), _timestamp(now), category_id),
            )
            if cursor.rowcount != 1:
                raise NotFoundError("category not found")
            return dict(self._required(connection, "SELECT * FROM categories WHERE id = ?", (category_id,), "category"))

    def update_category(
        self,
        category_id: int,
        *,
        now: datetime | str | None = None,
        **changes: Any,
    ) -> dict[str, Any]:
        """Update category metadata while preventing duplicate names and parent cycles."""

        allowed = {"name", "parent_id", "icon", "description", "sort_order"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValidationError(f"unsupported category fields: {sorted(unknown)}")
        if not changes:
            category = self.get_category(category_id)
            if category is None:
                raise NotFoundError("category not found")
            return category

        with self._transaction() as connection:
            category = self._required(
                connection,
                "SELECT * FROM categories WHERE id = ?",
                (category_id,),
                "category",
            )
            effective_name = str(changes.get("name", category["name"])).strip()
            if not effective_name:
                raise ValidationError("category name cannot be empty")

            effective_parent = changes.get("parent_id", category["parent_id"])
            if effective_parent is not None:
                effective_parent = int(effective_parent)
                if effective_parent < 1:
                    raise ValidationError("parent category id must be positive")
                if effective_parent == int(category_id):
                    raise ValidationError("category cannot be its own parent")
                ancestor_id: int | None = effective_parent
                seen: set[int] = set()
                while ancestor_id is not None:
                    if ancestor_id == int(category_id):
                        raise ValidationError("category parent would create a cycle")
                    if ancestor_id in seen:
                        raise ConflictError("existing category hierarchy contains a cycle")
                    seen.add(ancestor_id)
                    ancestor = self._required(
                        connection,
                        "SELECT parent_id FROM categories WHERE id = ?",
                        (ancestor_id,),
                        "parent category",
                    )
                    ancestor_id = int(ancestor["parent_id"]) if ancestor["parent_id"] is not None else None

            duplicate = connection.execute(
                """
                SELECT id FROM categories
                WHERE id <> ? AND name = ? COLLATE NOCASE
                  AND ((parent_id = ?) OR (parent_id IS NULL AND ? IS NULL))
                """,
                (category_id, effective_name, effective_parent, effective_parent),
            ).fetchone()
            if duplicate is not None:
                raise ConflictError("a category with this name already exists under the selected parent")

            normalized: dict[str, Any] = {}
            if "name" in changes:
                normalized["name"] = effective_name
            if "parent_id" in changes:
                normalized["parent_id"] = effective_parent
            if "icon" in changes:
                normalized["icon"] = str(changes["icon"]).strip() or None
            if "description" in changes:
                normalized["description"] = str(changes["description"]).strip() or None
            if "sort_order" in changes:
                normalized["sort_order"] = int(changes["sort_order"])
            assignments = [f"{field} = ?" for field in normalized]
            parameters = [*normalized.values(), _timestamp(now), category_id]
            connection.execute(
                f"UPDATE categories SET {', '.join(assignments)}, updated_at = ? WHERE id = ?",
                parameters,
            )
            return dict(
                self._required(
                    connection,
                    "SELECT * FROM categories WHERE id = ?",
                    (category_id,),
                    "category",
                )
            )

    def delete_category(self, category_id: int) -> bool:
        """Delete an empty category; dependent subcategories/products must be handled first."""

        with self._transaction() as connection:
            self._required(
                connection,
                "SELECT id FROM categories WHERE id = ?",
                (category_id,),
                "category",
            )
            if connection.execute(
                "SELECT 1 FROM categories WHERE parent_id = ? LIMIT 1",
                (category_id,),
            ).fetchone():
                raise ConflictError("category still has subcategories")
            if connection.execute(
                "SELECT 1 FROM products WHERE category_id = ? LIMIT 1",
                (category_id,),
            ).fetchone():
                raise ConflictError("category still has products")
            return connection.execute(
                "DELETE FROM categories WHERE id = ?",
                (category_id,),
            ).rowcount == 1

    def create_product(
        self,
        category_id: int,
        name: str,
        *,
        product_type: str,
        price_amount: int,
        currency: str = "TOMAN",
        icon: str | None = None,
        short_description: str | None = None,
        long_description: str | None = None,
        description: str | None = None,
        duration_days: int | None = None,
        duration_label: str | None = None,
        account_type: str | None = None,
        activation: str | None = None,
        renewable: bool = False,
        warranty_text: str | None = None,
        features: Sequence[str] | None = None,
        activation_instructions: str | None = None,
        usage_terms: str | None = None,
        rules_text: str | None = None,
        rules_url: str | None = None,
        reserve_enabled: bool = False,
        info_request_text: str | None = None,
        completion_text: str | None = None,
        delivery_instructions: str | None = None,
        reminder_days: Sequence[int] = (7, 3, 1),
        stock_limit: int | None = None,
        sku: str | None = None,
        visible: bool = True,
        available: bool = True,
        active: bool = True,
        idempotency_key: str | None = None,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        if product_type not in {"ready", "manual"}:
            raise ValidationError("product_type must be 'ready' or 'manual'")
        if product_type == "manual" and reserve_enabled:
            raise ValidationError("reservations are valid only for ready products")
        normalized_currency = currency.strip().upper()
        if normalized_currency != "TOMAN":
            raise ValidationError("this shop supports TOMAN product prices only")
        if price_amount < 0:
            raise ValidationError("price_amount cannot be negative")
        if duration_days is not None and duration_days <= 0:
            raise ValidationError("duration_days must be positive")
        if stock_limit is not None:
            stock_limit = int(stock_limit)
            if stock_limit < 0:
                raise ValidationError("stock_limit cannot be negative")
        if rules_url is not None and not is_safe_https_url(rules_url):
            raise ValidationError("rules_url must be a safe absolute HTTPS URL")
        normalized_reminders = self._normalize_reminder_days(reminder_days)
        clean_name = name.strip()
        if not clean_name:
            raise ValidationError("product name cannot be empty")
        if len(clean_name) > 200:
            raise ValidationError("product name cannot exceed 200 characters")
        requested_product: dict[str, Any] = {
            "category_id": int(category_id),
            "sku": sku,
            "name": clean_name,
            "icon": icon,
            "short_description": (
                short_description if short_description is not None else description
            ),
            "long_description": long_description,
            "product_type": product_type,
            "price_amount": int(price_amount),
            "currency": normalized_currency,
            "duration_days": duration_days,
            "duration_label": duration_label,
            "account_type": account_type,
            "activation": activation,
            "is_renewable": int(bool(renewable)),
            "warranty_text": warranty_text,
            "features_json": _json_dump(list(features or [])),
            "activation_instructions": activation_instructions,
            "usage_terms": usage_terms,
            "rules_text": rules_text,
            "rules_url": rules_url,
            "reserve_enabled": int(bool(reserve_enabled)),
            "info_request_text": info_request_text,
            "completion_text": completion_text,
            "delivery_instructions": delivery_instructions,
            "reminder_days_json": _json_dump(normalized_reminders),
            "stock_limit": stock_limit,
            "is_visible": int(bool(visible)),
            "is_available": int(bool(available)),
            "is_active": int(bool(active)),
        }

        def validate_existing_product(existing: sqlite3.Row, conflict: str) -> None:
            if any(existing[field] != value for field, value in requested_product.items()):
                raise ConflictError(conflict)

        stamp = _timestamp(now)
        idempotency_key = idempotency_key or uuid.uuid4().hex
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM products WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing:
                validate_existing_product(
                    existing, "product idempotency key belongs to another product"
                )
                return dict(existing)
            self._required(connection, "SELECT id FROM categories WHERE id = ?", (category_id,), "category")
            if sku:
                sku_match = connection.execute("SELECT * FROM products WHERE sku = ?", (sku,)).fetchone()
                if sku_match:
                    validate_existing_product(sku_match, "SKU belongs to another product")
                    return dict(sku_match)
            cursor = connection.execute(
                """
                INSERT INTO products(
                    category_id, sku, idempotency_key, name, icon,
                    short_description, long_description,
                    product_type, price_amount, currency, duration_days, duration_label,
                    account_type, activation, is_renewable, warranty_text, features_json,
                    activation_instructions, usage_terms, rules_text, rules_url,
                    reserve_enabled, info_request_text, completion_text,
                    delivery_instructions, reminder_days_json, stock_limit,
                    is_visible, is_available, is_active, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    category_id,
                    sku,
                    idempotency_key,
                    clean_name,
                    icon,
                    short_description if short_description is not None else description,
                    long_description,
                    product_type,
                    int(price_amount),
                    normalized_currency,
                    duration_days,
                    duration_label,
                    account_type,
                    activation,
                    int(bool(renewable)),
                    warranty_text,
                    _json_dump(list(features or [])),
                    activation_instructions,
                    usage_terms,
                    rules_text,
                    rules_url,
                    int(bool(reserve_enabled)),
                    info_request_text,
                    completion_text,
                    delivery_instructions,
                    _json_dump(normalized_reminders),
                    stock_limit,
                    int(bool(visible)),
                    int(bool(available)),
                    int(bool(active)),
                    stamp,
                    stamp,
                ),
            )
            return dict(self._required(connection, "SELECT * FROM products WHERE id = ?", (cursor.lastrowid,), "product"))

    def get_product(self, product_id: int) -> dict[str, Any] | None:
        with self._read() as connection:
            return _row(connection.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone())

    def list_products(self, *, category_id: int | None = None, visible_only: bool = True) -> list[dict[str, Any]]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if category_id is not None:
            clauses.append("category_id = ?")
            parameters.append(category_id)
        if visible_only:
            clauses.append("is_visible = 1")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._read() as connection:
            return _rows(connection.execute(f"SELECT * FROM products{where} ORDER BY id", parameters).fetchall())

    def update_product(self, product_id: int, *, now: datetime | str | None = None, **changes: Any) -> dict[str, Any]:
        """Update catalog fields while keeping visibility and availability distinct."""

        allowed = {
            "name", "icon", "short_description", "long_description", "price_amount",
            "currency", "duration_days", "duration_label", "account_type", "activation",
            "warranty_text", "activation_instructions", "usage_terms", "rules_text",
            "rules_url", "info_request_text", "completion_text", "delivery_instructions",
            "stock_limit", "category_id", "product_type", "sku",
        }
        boolean_fields = {"is_renewable", "reserve_enabled", "is_visible", "is_available", "is_active"}
        json_fields = {"features", "reminder_days"}
        unknown = set(changes) - allowed - boolean_fields - json_fields
        if unknown:
            raise ValidationError(f"unsupported product fields: {sorted(unknown)}")
        if not changes:
            product = self.get_product(product_id)
            if product is None:
                raise NotFoundError("product not found")
            return product

        if "name" in changes:
            changes["name"] = str(changes["name"]).strip()
            if not changes["name"]:
                raise ValidationError("product name cannot be empty")
            if len(changes["name"]) > 200:
                raise ValidationError("product name cannot exceed 200 characters")
        if "category_id" in changes:
            changes["category_id"] = int(changes["category_id"])
            if changes["category_id"] < 1:
                raise ValidationError("category id must be positive")
        if "product_type" in changes:
            changes["product_type"] = str(changes["product_type"]).strip().lower()
            if changes["product_type"] not in {"ready", "manual"}:
                raise ValidationError("product_type must be 'ready' or 'manual'")
        if "currency" in changes:
            changes["currency"] = str(changes["currency"]).strip().upper()
            if changes["currency"] != "TOMAN":
                raise ValidationError("this shop supports TOMAN product prices only")
        if "stock_limit" in changes and changes["stock_limit"] is not None:
            changes["stock_limit"] = int(changes["stock_limit"])
            if changes["stock_limit"] < 0:
                raise ValidationError("stock_limit cannot be negative")
        if "rules_url" in changes and changes["rules_url"] is not None:
            if not is_safe_https_url(changes["rules_url"]):
                raise ValidationError("rules_url must be a safe absolute HTTPS URL")
        assignments: list[str] = []
        parameters: list[Any] = []
        for key, value in changes.items():
            column = key
            if key in boolean_fields:
                value = int(bool(value))
            elif key == "features":
                column = "features_json"
                value = _json_dump(list(value or []))
            elif key == "reminder_days":
                column = "reminder_days_json"
                values = self._normalize_reminder_days(value)
                value = _json_dump(values)
            assignments.append(f"{column} = ?")
            parameters.append(value)
        assignments.append("updated_at = ?")
        parameters.extend((_timestamp(now), product_id))
        with self._transaction() as connection:
            current = self._required(
                connection,
                "SELECT * FROM products WHERE id = ?",
                (product_id,),
                "product",
            )
            effective_type = changes.get("product_type", current["product_type"])
            effective_reserve = changes.get(
                "reserve_enabled", current["reserve_enabled"]
            )
            if effective_type != "ready" and bool(effective_reserve):
                raise ValidationError("reservations are valid only for ready products")
            if effective_type == "ready" and set(changes).intersection(
                {"name", "icon", "delivery_instructions"}
            ):
                effective_name = str(changes.get("name", current["name"]))
                effective_icon = changes.get("icon", current["icon"])
                effective_instructions = changes.get(
                    "delivery_instructions", current["delivery_instructions"]
                )
                for item in connection.execute(
                    "SELECT payload FROM inventory_items WHERE product_id = ?",
                    (int(product_id),),
                ).fetchall():
                    self._validate_ready_delivery_text(
                        product_name=effective_name,
                        product_icon=effective_icon,
                        payload=item["payload"],
                        delivery_instructions=effective_instructions,
                    )
            if "category_id" in changes:
                self._required(
                    connection,
                    "SELECT id FROM categories WHERE id = ?",
                    (changes["category_id"],),
                    "category",
                )
            if changes.get("product_type") == "manual" and current["product_type"] != "manual":
                inventory_exists = connection.execute(
                    "SELECT 1 FROM inventory_items WHERE product_id = ? LIMIT 1",
                    (product_id,),
                ).fetchone()
                if inventory_exists is not None:
                    raise ConflictError("remove ready-product inventory before changing type to manual")
                live_ready_order = connection.execute(
                    """
                    SELECT 1 FROM orders
                    WHERE product_id = ? AND product_type_snapshot = 'ready'
                      AND status NOT IN (
                          'completed', 'cancelled', 'expired', 'rejected', 'refunded'
                      )
                    LIMIT 1
                    """,
                    (product_id,),
                ).fetchone()
                if live_ready_order is not None:
                    raise ConflictError(
                        "resolve all live ready-product orders before changing type"
                    )
            cursor = connection.execute(
                f"UPDATE products SET {', '.join(assignments)} WHERE id = ?",
                parameters,
            )
            if cursor.rowcount != 1:
                raise NotFoundError("product not found")
            return dict(self._required(connection, "SELECT * FROM products WHERE id = ?", (product_id,), "product"))

    def soft_delete_product(
        self,
        product_id: int,
        *,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        """Retire a product without removing order, payment, or inventory history."""

        with self._transaction() as connection:
            self._required(
                connection,
                "SELECT id FROM products WHERE id = ?",
                (product_id,),
                "product",
            )
            connection.execute(
                """
                UPDATE products
                SET is_active = 0, is_visible = 0, is_available = 0,
                    reserve_enabled = 0, updated_at = ?
                WHERE id = ?
                """,
                (_timestamp(now), product_id),
            )
            return dict(
                self._required(
                    connection,
                    "SELECT * FROM products WHERE id = ?",
                    (product_id,),
                    "product",
                )
            )

    def add_inventory_item(
        self,
        product_id: int,
        payload: str,
        *,
        source_admin_update_id: int | None = None,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        if not payload:
            raise ValidationError("inventory payload cannot be empty")
        stamp = _timestamp(now)
        payload_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        with self._transaction() as connection:
            if source_admin_update_id is not None:
                by_update = connection.execute(
                    "SELECT * FROM inventory_items WHERE source_admin_update_id = ?",
                    (int(source_admin_update_id),),
                ).fetchone()
                if by_update is not None:
                    if (
                        int(by_update["product_id"]) != int(product_id)
                        or by_update["payload_hash"] != payload_hash
                    ):
                        raise ConflictError(
                            "admin update belongs to another inventory item"
                        )
                    return dict(by_update)
            product = self._required(connection, "SELECT * FROM products WHERE id = ?", (product_id,), "product")
            if product["product_type"] != "ready":
                raise ValidationError("inventory items are only valid for ready products")
            self._validate_ready_delivery_text(
                product_name=product["name"],
                product_icon=product["icon"],
                payload=payload,
                delivery_instructions=product["delivery_instructions"],
            )
            existing = connection.execute(
                "SELECT * FROM inventory_items WHERE product_id = ? AND payload_hash = ?",
                (product_id, payload_hash),
            ).fetchone()
            if existing:
                raise ConflictError("inventory payload already exists for this product")
            if product["stock_limit"] is not None:
                unassigned = int(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM inventory_items
                        WHERE product_id = ? AND status IN ('available', 'disabled')
                        """,
                        (product_id,),
                    ).fetchone()[0]
                )
                if unassigned >= int(product["stock_limit"]):
                    raise ConflictError("inventory stock limit has been reached")
            cursor = connection.execute(
                """
                INSERT INTO inventory_items(
                    source_admin_update_id, product_id, payload, payload_hash, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    int(source_admin_update_id)
                    if source_admin_update_id is not None
                    else None,
                    product_id,
                    payload,
                    payload_hash,
                    stamp,
                ),
            )
            return dict(
                self._required(connection, "SELECT * FROM inventory_items WHERE id = ?", (cursor.lastrowid,), "inventory item")
            )

    def inventory_count(self, product_id: int, *, status: str = "available") -> int:
        with self._read() as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM inventory_items WHERE product_id = ? AND status = ?",
                    (product_id, status),
                ).fetchone()[0]
            )

    def list_inventory_items(
        self,
        product_id: int,
        *,
        status: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        parameters: list[Any] = [product_id]
        status_filter = ""
        if status is not None:
            status_filter = " AND status = ?"
            parameters.append(status)
        parameters.append(max(1, min(int(limit), 1_000)))
        with self._read() as connection:
            return _rows(
                connection.execute(
                    """
                    SELECT id, product_id, status, assigned_order_id, assigned_user_id,
                           assigned_at, created_at
                    FROM inventory_items WHERE product_id = ?
                    """ + status_filter + " ORDER BY id DESC LIMIT ?",
                    parameters,
                ).fetchall()
            )

    def set_inventory_status(self, item_id: int, status: str) -> dict[str, Any]:
        if status not in {"available", "disabled"}:
            raise ValidationError("only unassigned inventory can be enabled or disabled")
        with self._transaction() as connection:
            item = self._required(connection, "SELECT * FROM inventory_items WHERE id = ?", (item_id,), "inventory item")
            if item["status"] == "assigned":
                raise ConflictError("assigned inventory cannot be changed")
            connection.execute("UPDATE inventory_items SET status = ? WHERE id = ?", (status, item_id))
            return dict(
                self._required(connection, "SELECT * FROM inventory_items WHERE id = ?", (item_id,), "inventory item")
            )

    def update_inventory_item_payload(
        self,
        item_id: int,
        payload: str,
    ) -> dict[str, Any]:
        """Replace credentials for unassigned inventory without exposing them in lists."""

        if not str(payload).strip():
            raise ValidationError("inventory payload cannot be empty")
        payload_hash = hashlib.sha256(str(payload).encode("utf-8")).hexdigest()
        with self._transaction() as connection:
            item = self._required(
                connection,
                """
                SELECT inventory.*, product.name AS product_name,
                       product.icon AS product_icon,
                       product.delivery_instructions
                FROM inventory_items inventory
                JOIN products product ON product.id = inventory.product_id
                WHERE inventory.id = ?
                """,
                (int(item_id),),
                "inventory item",
            )
            if item["status"] == "assigned":
                raise ConflictError("assigned inventory cannot be edited")
            duplicate = connection.execute(
                """
                SELECT id FROM inventory_items
                WHERE product_id = ? AND payload_hash = ? AND id <> ?
                """,
                (item["product_id"], payload_hash, int(item_id)),
            ).fetchone()
            if duplicate is not None:
                raise ConflictError("the same inventory payload already exists")
            self._validate_ready_delivery_text(
                product_name=item["product_name"],
                product_icon=item["product_icon"],
                payload=str(payload),
                delivery_instructions=item["delivery_instructions"],
            )
            connection.execute(
                "UPDATE inventory_items SET payload = ?, payload_hash = ? WHERE id = ?",
                (str(payload), payload_hash, int(item_id)),
            )
            return dict(
                self._required(
                    connection,
                    "SELECT * FROM inventory_items WHERE id = ?",
                    (int(item_id),),
                    "inventory item",
                )
            )

    def delete_inventory_item(self, item_id: int) -> dict[str, Any]:
        """Delete only inventory that has never been assigned to an order/user."""

        with self._transaction() as connection:
            item = self._required(
                connection,
                "SELECT * FROM inventory_items WHERE id = ?",
                (item_id,),
                "inventory item",
            )
            if item["status"] not in {"available", "disabled"}:
                raise ConflictError("assigned inventory cannot be deleted")
            connection.execute("DELETE FROM inventory_items WHERE id = ?", (item_id,))
            return dict(item)

    # -- Orders, reservations and atomic inventory --------------------------

    @staticmethod
    def _allocate_paid_timestamp(connection: sqlite3.Connection, stamp: str) -> str:
        """Preserve first-payment commit order within one UTC second.

        Only paid_at uses the extra precision. Receipt deadlines and provider
        evidence timestamps retain their original timestamps. Call this inside
        the first-payment transaction, never during replay.
        """
        current = _parse_timestamp(stamp)
        latest = connection.execute(
            "SELECT MAX(paid_at) FROM orders WHERE substr(paid_at, 1, 19) = ?",
            (current.isoformat(timespec="seconds")[:19],),
        ).fetchone()[0]
        if latest is not None:
            current = max(current, _parse_timestamp(latest) + timedelta(microseconds=1))
        return current.isoformat(timespec="microseconds")

    def create_order(
        self,
        user_id: int,
        product_id: int,
        *,
        quantity: int = 1,
        idempotency_key: str | None = None,
        expires_in_minutes: int = 30,
        defer_free_confirmation: bool = False,
        order_notice: (
            Callable[
                [Mapping[str, Any]],
                tuple[str, str, Mapping[str, Any] | None],
            ]
            | None
        ) = None,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        if int(quantity) != 1:
            raise ValidationError("this shop supports exactly one subscription per order")
        if expires_in_minutes <= 0:
            raise ValidationError("expires_in_minutes must be positive")
        idempotency_key = idempotency_key or uuid.uuid4().hex
        created = _parse_timestamp(_timestamp(now))
        stamp = _timestamp(created)
        expires = _timestamp(created + timedelta(minutes=expires_in_minutes))

        def queue_created_notice(
            connection: sqlite3.Connection,
            order: Mapping[str, Any],
        ) -> None:
            if order_notice is None:
                return
            expected_key = f"order:{int(order['id'])}:created-summary"
            existing_notice = connection.execute(
                "SELECT id FROM outbound_messages WHERE idempotency_key = ?",
                (expected_key,),
            ).fetchone()
            if existing_notice is not None:
                return
            body, notice_key, markup = order_notice(order)
            if str(notice_key).strip() != expected_key:
                raise ValidationError("created-order notice key is not canonical")
            self._queue_user_message_in_transaction(
                connection,
                int(user_id),
                body,
                expected_key,
                stamp,
                reply_markup=markup,
            )

        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM orders WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing:
                if (
                    int(existing["user_id"]) != int(user_id)
                    or int(existing["product_id"]) != int(product_id)
                    or int(existing["quantity"]) != int(quantity)
                ):
                    raise ConflictError("order idempotency key belongs to another purchase")
                result = dict(existing)
                queue_created_notice(connection, result)
                return result
            user = self._required(connection, "SELECT * FROM users WHERE id = ?", (user_id,), "user")
            if user["is_blocked"]:
                raise ValidationError("blocked user cannot create an order")
            active_order_count = connection.execute(
                """
                SELECT COUNT(*) FROM orders
                WHERE user_id = ? AND status IN ('pending_payment', 'awaiting_confirmation')
                """,
                (user_id,),
            ).fetchone()[0]
            if int(active_order_count) >= 10:
                raise ConflictError("تعداد سفارش‌های پرداخت‌نشده شما بیش از حد مجاز است")
            product = self._required(connection, "SELECT * FROM products WHERE id = ?", (product_id,), "product")
            if not product["is_active"]:
                raise ValidationError("product is inactive")
            if not product["is_available"]:
                raise OutOfStockError("product is currently unavailable")
            subtotal = int(product["price_amount"]) * int(quantity)
            # Internal callers historically use zero-price orders as already
            # confirmed allocations. The customer UI explicitly defers them
            # until the buyer presses Payment on the saved summary.
            initially_paid = subtotal == 0 and not defer_free_confirmation
            status = "paid" if initially_paid else "pending_payment"
            # A subscription starts when credentials/service are delivered,
            # not when the payment is merely accepted.
            subscription_ends = None
            for _ in range(5):
                order_number = f"ORD-{created:%Y%m%d}-{uuid.uuid4().hex[:10].upper()}"
                try:
                    cursor = connection.execute(
                        """
                        INSERT INTO orders(
                            order_number, idempotency_key, user_id, product_id,
                            product_name_snapshot, product_icon_snapshot,
                            product_type_snapshot, duration_days_snapshot, duration_label_snapshot,
                            quantity, unit_price_amount, subtotal_amount, payable_amount,
                            currency, status, expires_at, paid_at, subscription_ends_at,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            order_number,
                            idempotency_key,
                            user_id,
                            product_id,
                            product["name"],
                            product["icon"],
                            product["product_type"],
                            product["duration_days"],
                            product["duration_label"],
                            int(quantity),
                            int(product["price_amount"]),
                            subtotal,
                            subtotal,
                            product["currency"],
                            status,
                            expires,
                            self._allocate_paid_timestamp(connection, stamp)
                            if initially_paid else None,
                            subscription_ends,
                            stamp,
                            stamp,
                        ),
                    )
                    order_id = int(cursor.lastrowid)
                    break
                except sqlite3.IntegrityError as error:
                    if "order_number" not in str(error):
                        raise
            else:
                raise ConflictError("could not allocate a unique order number")
            result = dict(
                self._required(
                    connection,
                    "SELECT * FROM orders WHERE id = ?",
                    (order_id,),
                    "order",
                )
            )
            queue_created_notice(connection, result)
            return result

    def get_order(self, order_id: int | str) -> dict[str, Any] | None:
        with self._read() as connection:
            if isinstance(order_id, str) and not order_id.isdigit():
                result = connection.execute("SELECT * FROM orders WHERE order_number = ?", (order_id,)).fetchone()
            else:
                result = connection.execute("SELECT * FROM orders WHERE id = ?", (int(order_id),)).fetchone()
            return _row(result)

    def get_order_by_number(self, order_number: str) -> dict[str, Any] | None:
        with self._read() as connection:
            return _row(
                connection.execute("SELECT * FROM orders WHERE order_number = ?", (order_number,)).fetchone()
            )

    def list_orders(
        self,
        *,
        user_id: int | None = None,
        status: str | None = None,
        product_id: int | None = None,
        created_from: str | None = None,
        created_until: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        parameters: list[Any] = []
        for column, value in (("user_id", user_id), ("status", status), ("product_id", product_id)):
            if value is not None:
                clauses.append(f"{column} = ?")
                parameters.append(value)
        if created_from is not None:
            clauses.append("created_at >= ?")
            parameters.append(created_from)
        if created_until is not None:
            clauses.append("created_at < ?")
            parameters.append(created_until)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.extend((max(1, min(int(limit), 1_000)), max(0, int(offset))))
        with self._read() as connection:
            return _rows(
                connection.execute(
                    f"SELECT * FROM orders{where} ORDER BY id DESC LIMIT ? OFFSET ?",
                    parameters,
                ).fetchall()
            )

    def count_orders(
        self,
        *,
        user_id: int | None = None,
        status: str | None = None,
        product_id: int | None = None,
        created_from: str | None = None,
        created_until: str | None = None,
    ) -> int:
        clauses: list[str] = []
        parameters: list[Any] = []
        for column, value in (
            ("user_id", user_id),
            ("status", status),
            ("product_id", product_id),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                parameters.append(value)
        if created_from is not None:
            clauses.append("created_at >= ?")
            parameters.append(created_from)
        if created_until is not None:
            clauses.append("created_at < ?")
            parameters.append(created_until)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._read() as connection:
            return int(
                connection.execute(
                    f"SELECT COUNT(*) FROM orders{where}", parameters
                ).fetchone()[0]
            )

    def list_orders_pending_reward_processing(
        self,
        *,
        limit: int = 1_000,
        offset: int = 0,
        after_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """List paid-order states whose reward pipeline has not fully finished."""

        cursor_clause = ""
        parameters: list[Any] = []
        if after_id is not None:
            cursor_clause = "AND id > ?"
            parameters.append(max(0, int(after_id)))
        parameters.extend(
            (
                max(1, min(int(limit), 5_000)),
                max(0, int(offset)),
            )
        )
        with self._read() as connection:
            return _rows(
                connection.execute(
                    f"""
                    SELECT * FROM orders
                    WHERE reward_processed_at IS NULL
                      AND status IN (
                          'paid','awaiting_stock','awaiting_info','processing','completed'
                      )
                      {cursor_clause}
                    ORDER BY id
                    LIMIT ? OFFSET ?
                    """,
                    parameters,
                ).fetchall()
            )

    def list_paid_orders_pending_fulfillment(
        self,
        *,
        limit: int = 100,
        after_id: int = 0,
    ) -> list[dict[str, Any]]:
        """Return paid orders still awaiting their product-specific workflow."""

        with self._read() as connection:
            return _rows(
                connection.execute(
                    """
                    SELECT * FROM orders
                    WHERE status = 'paid' AND id > ?
                    ORDER BY id
                    LIMIT ?
                    """,
                    (
                        max(0, int(after_id)),
                        max(1, min(int(limit), 5_000)),
                    ),
                ).fetchall()
            )

    def list_zero_external_paid_orders_missing_notice(
        self,
        *,
        limit: int = 100,
        after_id: int = 0,
    ) -> list[dict[str, Any]]:
        """Return successful wallet-only/discount/free UI orders lacking notice."""

        with self._read() as connection:
            return _rows(
                connection.execute(
                    """
                    SELECT o.*,
                           CASE
                               WHEN o.subtotal_amount = 0 THEN 'free'
                               WHEN o.subtotal_amount > 0
                                AND o.discount_amount >= o.subtotal_amount
                                   THEN 'discount'
                               ELSE 'wallet'
                           END AS success_kind
                    FROM orders o
                    WHERE o.id > ?
                      AND o.status IN (
                          'paid', 'awaiting_stock', 'awaiting_info',
                          'processing', 'completed'
                      )
                      AND o.external_paid_amount = 0
                      AND (
                          (
                              o.subtotal_amount = 0
                              AND EXISTS (
                                  SELECT 1 FROM outbound_messages created
                                  WHERE created.idempotency_key =
                                      'order:' || o.id || ':created-summary'
                              )
                          )
                          OR
                          (
                              o.subtotal_amount > 0
                              AND o.discount_amount >= o.subtotal_amount
                          )
                          OR (
                              o.subtotal_amount - o.discount_amount > 0
                              AND o.wallet_captured_amount =
                                  o.subtotal_amount - o.discount_amount
                          )
                      )
                      AND NOT EXISTS (
                          SELECT 1 FROM outbound_messages om
                          WHERE om.idempotency_key =
                              'order:' || o.id || ':' ||
                              CASE
                                  WHEN o.subtotal_amount = 0 THEN 'free-confirmed'
                                  WHEN o.subtotal_amount > 0
                                   AND o.discount_amount >= o.subtotal_amount
                                      THEN 'discount-confirmed'
                                  ELSE 'wallet-confirmed'
                              END
                            AND om.status IN ('sent', 'failed', 'cancelled')
                      )
                    ORDER BY o.id
                    LIMIT ?
                    """,
                    (
                        max(0, int(after_id)),
                        max(1, min(int(limit), 5_000)),
                    ),
                ).fetchall()
            )

    def order_success_notice_ready(self, order_id: int) -> bool:
        """Allow fulfilment after success was sent or its retry became terminal."""

        with self._read() as connection:
            order = self._required(
                connection,
                "SELECT * FROM orders WHERE id = ?",
                (int(order_id),),
                "order",
            )
            is_free_ui_order = False
            if int(order["subtotal_amount"]) <= 0:
                is_free_ui_order = connection.execute(
                    "SELECT 1 FROM outbound_messages WHERE idempotency_key = ?",
                    (f"order:{int(order_id)}:created-summary",),
                ).fetchone() is not None
                if not is_free_ui_order:
                    return True
            payment = connection.execute(
                """
                SELECT id FROM payments
                WHERE order_id = ? AND purpose = 'order' AND status = 'paid'
                ORDER BY id DESC LIMIT 1
                """,
                (int(order_id),),
            ).fetchone()
            if is_free_ui_order:
                key = f"order:{int(order_id)}:free-confirmed"
            elif payment is not None:
                key = f"payment:{int(payment['id'])}:order-confirmed"
            elif int(order["discount_amount"]) >= int(order["subtotal_amount"]):
                key = f"order:{int(order_id)}:discount-confirmed"
            elif (
                int(order["wallet_captured_amount"]) > 0
                and int(order["wallet_captured_amount"])
                == int(order["subtotal_amount"]) - int(order["discount_amount"])
            ):
                key = f"order:{int(order_id)}:wallet-confirmed"
            else:
                return False
            outbound = connection.execute(
                "SELECT status FROM outbound_messages WHERE idempotency_key = ?",
                (key,),
            ).fetchone()
            return outbound is not None and outbound["status"] in {
                "sent",
                "failed",
                "cancelled",
            }

    def list_ready_processing_orders(
        self,
        *,
        limit: int = 100,
        after_id: int = 0,
    ) -> list[dict[str, Any]]:
        """Return ready orders waiting for stock without an assignment."""

        with self._read() as connection:
            return _rows(
                connection.execute(
                    """
                    SELECT o.* FROM orders o
                    WHERE o.status = 'processing'
                      AND o.product_type_snapshot = 'ready'
                      AND o.id > ?
                      AND NOT EXISTS (
                          SELECT 1 FROM inventory_items assigned
                          WHERE assigned.assigned_order_id = o.id
                      )
                    ORDER BY o.id
                    LIMIT ?
                    """,
                    (
                        max(0, int(after_id)),
                        max(1, min(int(limit), 1_000)),
                    ),
                ).fetchall()
            )

    def list_orders_missing_notice(
        self,
        notice_kind: str,
        *,
        limit: int = 100,
        after_id: int = 0,
    ) -> list[dict[str, Any]]:
        """Select only eligible orders lacking a durable workflow notice."""

        predicates = {
            "reserved": """
                o.status = 'awaiting_stock'
                AND EXISTS (SELECT 1 FROM users u WHERE u.id = o.user_id)
                AND NOT EXISTS (
                    SELECT 1 FROM outbound_messages om
                    WHERE om.idempotency_key = 'order:' || o.id || ':reserved-notice'
                )
            """,
            "information": """
                o.status = 'awaiting_info'
                AND EXISTS (SELECT 1 FROM users u WHERE u.id = o.user_id)
                AND EXISTS (SELECT 1 FROM products p WHERE p.id = o.product_id)
                AND NOT EXISTS (
                    SELECT 1 FROM outbound_messages om
                    WHERE om.idempotency_key = 'order:' || o.id || ':info-request'
                       OR om.idempotency_key LIKE
                          'order:' || o.id || ':info-request:%'
                )
            """,
            "delivery": """
                o.status = 'completed'
                AND o.product_type_snapshot = 'ready'
                AND o.delivered_payload IS NOT NULL
                AND o.delivered_payload <> ''
                AND EXISTS (SELECT 1 FROM users u WHERE u.id = o.user_id)
                AND EXISTS (SELECT 1 FROM products p WHERE p.id = o.product_id)
                AND NOT EXISTS (
                    SELECT 1 FROM outbound_messages om
                    WHERE om.idempotency_key = 'order:' || o.id || ':delivery'
                )
            """,
            "expired": """
                o.status = 'expired'
                AND EXISTS (SELECT 1 FROM users u WHERE u.id = o.user_id)
                AND NOT EXISTS (
                    SELECT 1 FROM outbound_messages om
                    WHERE om.idempotency_key = 'order:' || o.id || ':expired-notice'
                )
            """,
        }
        predicate = predicates.get(str(notice_kind))
        if predicate is None:
            raise ValidationError("unsupported order notice kind")
        with self._read() as connection:
            return _rows(
                connection.execute(
                    f"""
                    SELECT o.* FROM orders o
                    WHERE o.id > ? AND ({predicate})
                    ORDER BY o.id
                    LIMIT ?
                    """,
                    (max(0, int(after_id)), max(1, min(int(limit), 1_000))),
                ).fetchall()
            )

    def set_order_customer_info(
        self,
        order_id: int,
        info: Mapping[str, Any],
        *,
        receipt_file_id: str | None = None,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        payload = dict(info)
        if not (
            str(payload.get("text") or "").strip()
            or str(payload.get("file_id") or "").strip()
        ):
            raise ValidationError("customer information requires text or a file")
        stamp = _timestamp(now)
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE orders
                SET customer_info_json = ?, receipt_file_id = COALESCE(?, receipt_file_id),
                    updated_at = ?
                WHERE id = ?
                """,
                (_json_dump(payload), receipt_file_id, stamp, order_id),
            )
            if cursor.rowcount != 1:
                raise NotFoundError("order not found")
            return dict(self._required(connection, "SELECT * FROM orders WHERE id = ?", (order_id,), "order"))

    set_order_customer_data = set_order_customer_info

    def submit_manual_order_info(
        self,
        order_id: int,
        user_id: int,
        info: Mapping[str, Any],
        *,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        """Persist customer data and enter processing in one transaction.

        A customer may replace data while a manual order is still processing;
        exact replays are no-ops. Terminal and unrelated orders fail closed.
        """

        payload = dict(info)
        if not (
            str(payload.get("text") or "").strip()
            or str(payload.get("file_id") or "").strip()
        ):
            raise ValidationError("customer information requires text or a file")
        encoded = _json_dump(payload)
        stamp = _timestamp(now)
        with self._transaction() as connection:
            order = self._required(
                connection,
                "SELECT * FROM orders WHERE id = ?",
                (int(order_id),),
                "order",
            )
            if int(order["user_id"]) != int(user_id):
                raise ValidationError("user does not own this order")
            if order["product_type_snapshot"] != "manual":
                raise ValidationError("only manual orders accept customer information")
            if order["status"] not in {"awaiting_info", "processing"}:
                raise ValidationError("order no longer accepts customer information")
            if (
                order["status"] == "processing"
                and str(order["customer_info_json"] or "") == encoded
            ):
                return dict(order)
            connection.execute(
                """
                UPDATE orders
                SET customer_info_json = ?, status = 'processing', updated_at = ?
                WHERE id = ?
                """,
                (encoded, stamp, int(order_id)),
            )
            return dict(
                self._required(
                    connection,
                    "SELECT * FROM orders WHERE id = ?",
                    (int(order_id),),
                    "order",
                )
            )

    def set_order_admin_note(
        self,
        order_id: int,
        note: str | None,
        *,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE orders SET admin_note = ?, updated_at = ? WHERE id = ?",
                (note, _timestamp(now), order_id),
            )
            if cursor.rowcount != 1:
                raise NotFoundError("order not found")
            return dict(self._required(connection, "SELECT * FROM orders WHERE id = ?", (order_id,), "order"))

    def reserve_product(
        self,
        user_id: int,
        product_id: int,
        *,
        order_id: int | None = None,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        stamp = _timestamp(now)
        with self._transaction() as connection:
            product = self._required(connection, "SELECT * FROM products WHERE id = ?", (product_id,), "product")
            if not product["reserve_enabled"]:
                raise ValidationError("reservation is disabled for this product")
            if product["product_type"] != "ready":
                raise ValidationError("reservations are valid only for ready products")
            self._required(connection, "SELECT id FROM users WHERE id = ?", (user_id,), "user")
            if order_id is not None:
                order = self._required(connection, "SELECT * FROM orders WHERE id = ?", (order_id,), "order")
                if order["user_id"] != user_id or order["product_id"] != product_id:
                    raise ValidationError("reservation order does not match user/product")
                if order["product_type_snapshot"] != "ready":
                    raise ValidationError("only ready-product orders can reserve inventory")
                if order["status"] not in {"paid", "processing", "awaiting_stock"}:
                    raise ValidationError("only a paid ready-product order can reserve inventory")
            if order_id is not None:
                existing = connection.execute(
                    "SELECT * FROM reservations WHERE order_id = ? AND status = 'queued'",
                    (order_id,),
                ).fetchone()
            else:
                existing = connection.execute(
                    """
                    SELECT * FROM reservations
                    WHERE product_id = ? AND user_id = ? AND order_id IS NULL
                      AND status = 'queued'
                    """,
                    (product_id, user_id),
                ).fetchone()
            if existing:
                return dict(existing)
            cursor = connection.execute(
                """
                INSERT INTO reservations(product_id, user_id, order_id, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (product_id, user_id, order_id, stamp),
            )
            if order_id is not None:
                connection.execute(
                    "UPDATE orders SET status = 'awaiting_stock', updated_at = ? WHERE id = ?",
                    (stamp, order_id),
                )
            return dict(
                self._required(connection, "SELECT * FROM reservations WHERE id = ?", (cursor.lastrowid,), "reservation")
            )

    @staticmethod
    def _ready_order_priority_sql(alias: str) -> str:
        # New paid_at values carry the durable first-payment sequence. For
        # legacy equal timestamps, preserve known queue order; where there is
        # no queue evidence the final ID is only a deterministic fallback.
        if alias not in {"older", "o", "current_order"}:
            raise ValueError("unsupported order alias")
        return (
            f"COALESCE({alias}.paid_at, {alias}.created_at), "
            "COALESCE((SELECT MIN(queue.id) FROM reservations queue "
            f"WHERE queue.order_id = {alias}.id AND queue.status = 'queued'), "
            f"9223372036854775807), {alias}.id"
        )

    def _assign_inventory(
        self,
        connection: sqlite3.Connection,
        order_id: int,
        stamp: str,
    ) -> sqlite3.Row:
        order = self._required(connection, "SELECT * FROM orders WHERE id = ?", (order_id,), "order")
        if order["product_type_snapshot"] != "ready":
            raise ValidationError("manual products do not use inventory assignment")
        if order["status"] not in {"paid", "processing", "awaiting_stock"}:
            raise ValidationError("inventory can only be assigned to a paid order")
        existing = connection.execute(
            "SELECT * FROM inventory_items WHERE assigned_order_id = ?",
            (order_id,),
        ).fetchone()
        if existing:
            return existing
        # A fresh checkout must not consume a restock before an earlier paid
        # order, including an order whose reservation has not yet been queued.
        # Keep the fairness check in the same transaction as the stock claim.
        prior_order = connection.execute(
            f"""
            SELECT older.id FROM orders older
            JOIN orders current_order ON current_order.id = ?
            WHERE older.product_id = ?
              AND older.product_type_snapshot = 'ready'
              AND older.status IN ('paid', 'processing', 'awaiting_stock')
              AND NOT EXISTS (
                  SELECT 1 FROM inventory_items assigned
                  WHERE assigned.assigned_order_id = older.id
              )
              AND ({self._ready_order_priority_sql('older')})
                < ({self._ready_order_priority_sql('current_order')})
            LIMIT 1
            """,
            (order_id, order["product_id"]),
        ).fetchone()
        if prior_order is not None:
            raise OutOfStockError("available inventory belongs to an earlier paid order")
        candidate = connection.execute(
            """
            SELECT inventory.*, product.delivery_instructions
            FROM inventory_items inventory
            JOIN products product ON product.id = inventory.product_id
            WHERE inventory.product_id = ? AND inventory.status = 'available'
            ORDER BY inventory.id LIMIT 1
            """,
            (order["product_id"],),
        ).fetchone()
        if candidate is None:
            raise OutOfStockError("no inventory item is available")
        self._validate_ready_delivery_text(
            product_name=order["product_name_snapshot"],
            product_icon=order["product_icon_snapshot"],
            payload=candidate["payload"],
            delivery_instructions=candidate["delivery_instructions"],
            order_number=order["order_number"],
        )
        cursor = connection.execute(
            """
            UPDATE inventory_items
            SET status = 'assigned', assigned_order_id = ?, assigned_user_id = ?, assigned_at = ?
            WHERE id = ? AND status = 'available'
            """,
            (order_id, order["user_id"], stamp, candidate["id"]),
        )
        if cursor.rowcount != 1:
            raise OutOfStockError("inventory was claimed concurrently")
        item = self._required(
            connection,
            "SELECT * FROM inventory_items WHERE id = ?",
            (candidate["id"],),
            "inventory item",
        )
        subscription_ends = (
            _timestamp(
                _parse_timestamp(stamp)
                + timedelta(days=int(order["duration_days_snapshot"]))
            )
            if order["duration_days_snapshot"]
            else None
        )
        connection.execute(
            """
            UPDATE orders
            SET delivered_payload = ?, status = 'completed', completed_at = ?,
                subscription_ends_at = COALESCE(subscription_ends_at, ?), updated_at = ?
            WHERE id = ?
            """,
            (item["payload"], stamp, subscription_ends, stamp, order_id),
        )
        connection.execute(
            """
            UPDATE reservations
            SET status = 'fulfilled', fulfilled_inventory_item_id = ?, fulfilled_at = ?
            WHERE order_id = ? AND status = 'queued'
            """,
            (item["id"], stamp, order_id),
        )
        self._schedule_order_reminders(connection, order_id, stamp)
        return item

    def assign_inventory(
        self,
        order_id: int,
        *,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        """Atomically claim the oldest available stock item for one paid order."""

        with self._transaction() as connection:
            return dict(self._assign_inventory(connection, order_id, _timestamp(now)))

    def fulfill_next_reservation(
        self,
        product_id: int,
        *,
        now: datetime | str | None = None,
    ) -> dict[str, Any] | None:
        stamp = _timestamp(now)
        with self._transaction() as connection:
            reservation = connection.execute(
                f"""
                SELECT r.* FROM reservations r
                JOIN orders o ON o.id = r.order_id
                WHERE r.product_id = ? AND r.status = 'queued'
                  AND o.status IN ('paid', 'processing', 'awaiting_stock')
                  AND NOT EXISTS (
                      SELECT 1 FROM orders older
                      WHERE older.product_id = o.product_id
                        AND older.product_type_snapshot = 'ready'
                        AND older.status IN ('paid', 'processing', 'awaiting_stock')
                        AND ({self._ready_order_priority_sql('older')})
                          < ({self._ready_order_priority_sql('o')})
                  )
                ORDER BY {self._ready_order_priority_sql('o')} LIMIT 1
                """,
                (product_id,),
            ).fetchone()
            if reservation is None:
                return None
            item = self._assign_inventory(connection, reservation["order_id"], stamp)
            result = dict(reservation)
            result["inventory_item"] = dict(item)
            return result

    def fulfill_next_available_reservation(
        self,
        *,
        now: datetime | str | None = None,
    ) -> dict[str, Any] | None:
        """Fulfil the oldest queued reservation whose product has stock."""

        stamp = _timestamp(now)
        with self._transaction() as connection:
            reservation = connection.execute(
                f"""
                SELECT r.* FROM reservations r
                JOIN orders o ON o.id = r.order_id
                WHERE r.status = 'queued'
                  AND o.status IN ('paid', 'processing', 'awaiting_stock')
                  AND NOT EXISTS (
                      SELECT 1 FROM orders older
                      WHERE older.product_id = o.product_id
                        AND older.product_type_snapshot = 'ready'
                        AND older.status IN ('paid', 'processing', 'awaiting_stock')
                        AND ({self._ready_order_priority_sql('older')})
                          < ({self._ready_order_priority_sql('o')})
                  )
                  AND EXISTS (
                      SELECT 1 FROM inventory_items i
                      WHERE i.product_id = r.product_id
                        AND i.status = 'available'
                  )
                ORDER BY {self._ready_order_priority_sql('o')} LIMIT 1
                """
            ).fetchone()
            if reservation is None:
                return None
            item = self._assign_inventory(connection, reservation["order_id"], stamp)
            result = dict(reservation)
            result["inventory_item"] = dict(item)
            return result

    def fulfill_next_processing_ready_order(
        self,
        *,
        now: datetime | str | None = None,
    ) -> dict[str, Any] | None:
        """Assign stock to the oldest paid ready order left in manual processing."""

        stamp = _timestamp(now)
        with self._transaction() as connection:
            candidate = connection.execute(
                f"""
                SELECT o.id FROM orders o
                WHERE o.status = 'processing'
                  AND o.product_type_snapshot = 'ready'
                  AND NOT EXISTS (
                      SELECT 1 FROM inventory_items assigned
                      WHERE assigned.assigned_order_id = o.id
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM orders older
                      WHERE older.product_id = o.product_id
                        AND older.product_type_snapshot = 'ready'
                        AND older.status IN ('paid', 'processing', 'awaiting_stock')
                        AND ({self._ready_order_priority_sql('older')})
                          < ({self._ready_order_priority_sql('o')})
                  )
                  AND EXISTS (
                      SELECT 1 FROM inventory_items available
                      WHERE available.product_id = o.product_id
                        AND available.status = 'available'
                  )
                ORDER BY {self._ready_order_priority_sql('o')} LIMIT 1
                """
            ).fetchone()
            if candidate is None:
                return None
            item = self._assign_inventory(connection, int(candidate["id"]), stamp)
            order = self._required(
                connection,
                "SELECT * FROM orders WHERE id = ?",
                (int(candidate["id"]),),
                "order",
            )
            return {**dict(order), "inventory_item": dict(item)}

    def assign_inventory_item_to_user(
        self,
        item_id: int,
        user_id: int,
        *,
        actor_admin_id: int | None = None,
        delivery_notice: (
            Callable[[Mapping[str, Any]], tuple[str, str]] | None
        ) = None,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        """Create a completed admin order and its optional delivery outbox atomically."""

        stamp = _timestamp(now)
        current = _parse_timestamp(stamp)

        def queue_delivery(
            connection: sqlite3.Connection,
            result: Mapping[str, Any],
        ) -> None:
            if delivery_notice is None:
                return
            body, idempotency_key = delivery_notice(result)
            clean_body = str(body).strip()
            clean_key = str(idempotency_key).strip()
            if not clean_body or not clean_key:
                raise ValidationError("inventory delivery notice cannot be empty")
            if len(clean_body) > self.TELEGRAM_SAFE_MESSAGE_LENGTH:
                raise ValidationError("inventory delivery notice exceeds Telegram limit")
            existing = connection.execute(
                "SELECT * FROM outbound_messages WHERE idempotency_key = ?",
                (clean_key,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["recipient_user_id"] != int(user_id)
                    or existing["audience_json"] is not None
                    or existing["body"] != clean_body
                    or existing["reply_markup_json"] is not None
                ):
                    raise ConflictError(
                        "inventory delivery idempotency key belongs to another message"
                    )
                return
            connection.execute(
                """
                INSERT INTO outbound_messages(
                    idempotency_key, recipient_user_id, body,
                    scheduled_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (clean_key, int(user_id), clean_body, stamp, stamp, stamp),
            )

        with self._transaction() as connection:
            item = self._required(
                connection,
                """
                SELECT i.*, p.name, p.icon, p.product_type, p.duration_days,
                       p.duration_label, p.currency, p.reminder_days_json
                FROM inventory_items i JOIN products p ON p.id = i.product_id
                WHERE i.id = ?
                """,
                (item_id,),
                "inventory item",
            )
            if item["status"] == "assigned":
                if item["assigned_user_id"] != user_id:
                    raise ConflictError("inventory item is already assigned")
                order = self._required(
                    connection,
                    "SELECT * FROM orders WHERE id = ?",
                    (item["assigned_order_id"],),
                    "order",
                )
                result = {**dict(order), "payload": item["payload"]}
                queue_delivery(connection, result)
                return result
            if item["status"] != "available":
                raise ConflictError("inventory item is not available")
            queued_backlog = connection.execute(
                """
                SELECT o.id FROM orders o
                WHERE o.product_id = ?
                  AND o.product_type_snapshot = 'ready'
                  AND o.status IN ('paid', 'processing', 'awaiting_stock')
                  AND NOT EXISTS (
                      SELECT 1 FROM inventory_items assigned
                      WHERE assigned.assigned_order_id = o.id
                  )
                ORDER BY o.id
                LIMIT 1
                """,
                (int(item["product_id"]),),
            ).fetchone()
            if queued_backlog is not None:
                raise ConflictError(
                    "inventory must be fulfilled through the existing FIFO order queue"
                )
            self._required(connection, "SELECT id FROM users WHERE id = ?", (user_id,), "user")
            if actor_admin_id is not None:
                self._required(connection, "SELECT id FROM admins WHERE id = ?", (actor_admin_id,), "admin")
            subscription_ends = (
                _timestamp(current + timedelta(days=int(item["duration_days"])))
                if item["duration_days"]
                else None
            )
            order_number = f"ADM-{current:%Y%m%d}-{uuid.uuid4().hex[:10].upper()}"
            cursor = connection.execute(
                """
                INSERT INTO orders(
                    order_number, idempotency_key, order_origin, user_id, product_id,
                    product_name_snapshot, product_icon_snapshot, product_type_snapshot,
                    duration_days_snapshot, duration_label_snapshot, quantity,
                    unit_price_amount, subtotal_amount, payable_amount, currency,
                    status, delivered_payload, expires_at, paid_at, completed_at,
                    subscription_ends_at, created_at, updated_at,
                    reward_processed_at, admin_note
                ) VALUES (?, ?, 'admin_assignment', ?, ?, ?, ?, ?, ?, ?, 1, 0, 0, 0, ?,
                          'completed', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_number,
                    f"admin-inventory:{item_id}:{user_id}",
                    user_id,
                    item["product_id"],
                    item["name"],
                    item["icon"],
                    item["product_type"],
                    item["duration_days"],
                    item["duration_label"],
                    item["currency"],
                    item["payload"],
                    stamp,
                    stamp,
                    stamp,
                    subscription_ends,
                    stamp,
                    stamp,
                    stamp,
                    f"Assigned manually by admin {actor_admin_id}",
                ),
            )
            order_id = int(cursor.lastrowid)
            changed = connection.execute(
                """
                UPDATE inventory_items
                SET status = 'assigned', assigned_order_id = ?, assigned_user_id = ?, assigned_at = ?
                WHERE id = ? AND status = 'available'
                """,
                (order_id, user_id, stamp, item_id),
            ).rowcount
            if changed != 1:
                raise ConflictError("inventory item was claimed concurrently")
            self._schedule_order_reminders(connection, order_id, stamp)
            order = self._required(connection, "SELECT * FROM orders WHERE id = ?", (order_id,), "order")
            result = {**dict(order), "payload": item["payload"]}
            queue_delivery(connection, result)
            return result

    def complete_order(
        self,
        order_id: int,
        delivered_payload: str,
        *,
        admin_note: str = "Manual completion",
        outbound_body: str | None = None,
        outbound_idempotency_key: str | None = None,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        if not delivered_payload:
            raise ValidationError("delivery payload cannot be empty")
        if (outbound_body is None) != (outbound_idempotency_key is None):
            raise ValidationError("completion outbox body and idempotency key are required together")
        clean_outbound_body = str(outbound_body or "").strip()
        clean_outbound_key = str(outbound_idempotency_key or "").strip()
        if outbound_body is not None and (
            not clean_outbound_body or not clean_outbound_key
        ):
            raise ValidationError(
                "completion outbox body and idempotency key cannot be empty"
            )
        if (
            outbound_body is not None
            and len(clean_outbound_body) > self.TELEGRAM_SAFE_MESSAGE_LENGTH
        ):
            raise ValidationError("completion notification exceeds Telegram limit")
        stamp = _timestamp(now)

        def queue_completion_notice(
            connection: sqlite3.Connection,
            order: sqlite3.Row,
        ) -> None:
            if outbound_body is None:
                return
            existing = connection.execute(
                "SELECT * FROM outbound_messages WHERE idempotency_key = ?",
                (clean_outbound_key,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["recipient_user_id"] != int(order["user_id"])
                    or existing["audience_json"] is not None
                    or existing["body"] != clean_outbound_body
                    or existing["reply_markup_json"] is not None
                ):
                    raise ConflictError(
                        "completion notification idempotency key belongs to another message"
                    )
                return
            connection.execute(
                """
                INSERT INTO outbound_messages(
                    idempotency_key, recipient_user_id, body,
                    scheduled_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    clean_outbound_key,
                    int(order["user_id"]),
                    clean_outbound_body,
                    stamp,
                    stamp,
                    stamp,
                ),
            )

        with self._transaction() as connection:
            order = self._required(connection, "SELECT * FROM orders WHERE id = ?", (order_id,), "order")
            if order["product_type_snapshot"] != "manual":
                raise ValidationError("only manual-product orders can be completed manually")
            if order["status"] == "completed":
                if order["delivered_payload"] == delivered_payload:
                    queue_completion_notice(connection, order)
                    return dict(order)
                raise ConflictError("completed order delivery cannot be overwritten")
            if order["status"] != "processing":
                raise ValidationError("manual order can only be completed from processing")
            if not _has_customer_information(order["customer_info_json"]):
                raise ValidationError("manual order requires customer information before completion")
            subscription_ends = (
                _timestamp(
                    _parse_timestamp(stamp)
                    + timedelta(days=int(order["duration_days_snapshot"]))
                )
                if order["duration_days_snapshot"]
                else None
            )
            connection.execute(
                """
                UPDATE orders SET delivered_payload = ?, status = 'completed',
                    completed_at = COALESCE(completed_at, ?),
                    subscription_ends_at = COALESCE(subscription_ends_at, ?),
                    admin_note = ?, updated_at = ?
                WHERE id = ?
                """,
                (delivered_payload, stamp, subscription_ends, admin_note, stamp, order_id),
            )
            self._schedule_order_reminders(connection, order_id, stamp)
            completed = self._required(
                connection,
                "SELECT * FROM orders WHERE id = ?",
                (order_id,),
                "order",
            )
            queue_completion_notice(connection, completed)
            return dict(completed)

    # -- Immutable signed wallet ledger -------------------------------------

    @staticmethod
    def _wallet_balance(connection: sqlite3.Connection, user_id: int) -> int:
        return int(
            connection.execute(
                "SELECT COALESCE(SUM(amount_signed), 0) FROM wallet_entries WHERE user_id = ?",
                (user_id,),
            ).fetchone()[0]
        )

    def get_wallet_balance(self, user_id: int) -> int:
        with self._read() as connection:
            self._required(connection, "SELECT id FROM users WHERE id = ?", (user_id,), "user")
            return self._wallet_balance(connection, user_id)

    def get_wallet_entries(self, user_id: int, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._read() as connection:
            return _rows(
                connection.execute(
                    "SELECT * FROM wallet_entries WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                    (user_id, max(1, min(int(limit), 1_000))),
                ).fetchall()
            )

    def list_user_transactions(
        self, user_id: int, *, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        """Return wallet movements plus card/crypto order purchases.

        A successful wallet top-up already has a positive ledger entry, so it
        is deliberately not duplicated from the payments table.
        """

        bounded = max(1, min(int(limit), 1_000))
        start = max(0, int(offset))
        with self._read() as connection:
            self._required(
                connection, "SELECT id FROM users WHERE id = ?", (user_id,), "user"
            )
            return _rows(
                connection.execute(
                    """
                    SELECT * FROM (
                        SELECT 'wallet:' || we.id AS transaction_key,
                               we.created_at, we.amount_signed, we.entry_type,
                               we.reason, we.order_id, we.payment_id,
                               o.order_number, p.payment_number, NULL AS method
                        FROM wallet_entries we
                        LEFT JOIN orders o ON o.id = we.order_id
                        LEFT JOIN payments p ON p.id = we.payment_id
                        WHERE we.user_id = ?
                        UNION ALL
                        SELECT 'payment:' || p.id AS transaction_key,
                               COALESCE(p.confirmed_at, p.updated_at, p.created_at),
                               -p.base_amount, 'external_purchase',
                                CASE
                                    WHEN EXISTS (
                                        SELECT 1 FROM wallet_entries late_credit
                                        WHERE late_credit.payment_id = p.id
                                          AND late_credit.idempotency_key =
                                              'payment:' || p.id || ':provider-credit'
                                    ) THEN
                                        'دریافت دیررس پرداخت ارزی؛ سفارش قبلی فعال نشد'
                                    WHEN p.method = 'card' THEN 'پرداخت خرید با کارت'
                                    WHEN p.method = 'crypto' THEN 'پرداخت ارزی خرید'
                                    ELSE 'پرداخت خرید (' || p.method || ')'
                                END,
                               p.order_id, p.id, o.order_number,
                               p.payment_number, p.method
                        FROM payments p
                        JOIN orders o ON o.id = p.order_id
                        WHERE p.user_id = ? AND p.purpose = 'order'
                          AND p.status IN ('paid', 'refunded')
                    ) transactions
                    ORDER BY created_at DESC, transaction_key DESC
                    LIMIT ? OFFSET ?
                    """,
                    (user_id, user_id, bounded, start),
                ).fetchall()
            )

    def count_user_transactions(self, user_id: int) -> int:
        with self._read() as connection:
            return int(
                connection.execute(
                    """
                    SELECT (
                        SELECT COUNT(*) FROM wallet_entries WHERE user_id = ?
                    ) + (
                        SELECT COUNT(*) FROM payments
                        WHERE user_id = ? AND purpose = 'order'
                          AND status IN ('paid', 'refunded')
                    )
                    """,
                    (int(user_id), int(user_id)),
                ).fetchone()[0]
            )

    wallet_balance = get_wallet_balance
    list_wallet_entries = get_wallet_entries

    def adjust_wallet(
        self,
        user_id: int,
        amount_signed: int,
        *,
        reason: str,
        idempotency_key: str,
        actor_admin_id: int | None = None,
        entry_type: str = "admin_adjustment",
        order_id: int | None = None,
        payment_id: int | None = None,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        if amount_signed == 0:
            raise ValidationError("wallet adjustment cannot be zero")
        if not reason.strip():
            raise ValidationError("wallet adjustment reason is required")
        stamp = _timestamp(now)
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM wallet_entries WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing:
                mismatched = (
                    int(existing["user_id"]) != int(user_id)
                    or int(existing["amount_signed"]) != int(amount_signed)
                    or existing["entry_type"] != entry_type
                    or existing["reason"] != reason.strip()
                    or existing["actor_admin_id"] != actor_admin_id
                    or existing["order_id"] != order_id
                    or existing["payment_id"] != payment_id
                )
                if mismatched:
                    raise ConflictError("idempotency key belongs to a different wallet operation")
                result = dict(existing)
                result["balance"] = self._wallet_balance(connection, user_id)
                return result
            self._required(connection, "SELECT id FROM users WHERE id = ?", (user_id,), "user")
            if amount_signed < 0 and self._wallet_balance(connection, user_id) < -amount_signed:
                raise InsufficientFundsError("wallet balance is insufficient")
            cursor = connection.execute(
                """
                INSERT INTO wallet_entries(
                    user_id, order_id, payment_id, actor_admin_id, amount_signed,
                    entry_type, reason, idempotency_key, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    order_id,
                    payment_id,
                    actor_admin_id,
                    int(amount_signed),
                    entry_type,
                    reason.strip(),
                    idempotency_key,
                    stamp,
                ),
            )
            result = dict(
                self._required(connection, "SELECT * FROM wallet_entries WHERE id = ?", (cursor.lastrowid,), "wallet entry")
            )
            result["balance"] = self._wallet_balance(connection, user_id)
            return result

    def credit_wallet(
        self,
        user_id: int,
        amount: int,
        *,
        reason: str,
        idempotency_key: str,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        if amount <= 0:
            raise ValidationError("credit amount must be positive")
        return self.adjust_wallet(
            user_id,
            amount,
            reason=reason,
            idempotency_key=idempotency_key,
            entry_type="manual_credit",
            now=now,
        )

    def hold_wallet_funds(
        self,
        order_id: int,
        *,
        max_amount: int | None = None,
        idempotency_key: str,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        stamp = _timestamp(now)
        with self._transaction() as connection:
            prior = connection.execute(
                "SELECT * FROM wallet_entries WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if prior:
                if prior["order_id"] != order_id or prior["entry_type"] != "wallet_hold":
                    raise ConflictError("idempotency key belongs to another operation")
                return dict(self._required(connection, "SELECT * FROM orders WHERE id = ?", (order_id,), "order"))
            order = self._required(connection, "SELECT * FROM orders WHERE id = ?", (order_id,), "order")
            if order["status"] != "pending_payment":
                raise ValidationError("wallet can only be held for a pending order")
            if _parse_timestamp(order["expires_at"]) <= _parse_timestamp(stamp):
                raise ValidationError("order has expired")
            if order["wallet_refunded_amount"]:
                raise ValidationError("a refunded hold cannot be recreated on the same order")
            remaining = order["subtotal_amount"] - order["discount_amount"] - order["wallet_held_amount"]
            available = self._wallet_balance(connection, order["user_id"])
            requested = remaining if max_amount is None else min(remaining, max(0, int(max_amount)))
            held = min(available, requested)
            if held <= 0:
                raise InsufficientFundsError("wallet has no applicable balance")
            connection.execute(
                """
                INSERT INTO wallet_entries(
                    user_id, order_id, amount_signed, entry_type, reason,
                    idempotency_key, created_at
                ) VALUES (?, ?, ?, 'wallet_hold', ?, ?, ?)
                """,
                (order["user_id"], order_id, -held, "Wallet hold for order", idempotency_key, stamp),
            )
            new_held = order["wallet_held_amount"] + held
            payable = order["subtotal_amount"] - order["discount_amount"] - new_held
            paid = payable == 0
            paid_at = (
                order["paid_at"] or self._allocate_paid_timestamp(connection, stamp)
                if paid else order["paid_at"]
            )
            subscription_ends = order["subscription_ends_at"]
            connection.execute(
                """
                UPDATE orders
                SET wallet_held_amount = ?, payable_amount = ?,
                    wallet_captured_amount = CASE WHEN ? THEN ? ELSE wallet_captured_amount END,
                    status = CASE WHEN ? THEN 'paid' ELSE status END,
                    paid_at = CASE WHEN ? THEN ? ELSE paid_at END,
                    subscription_ends_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    new_held,
                    payable,
                    int(paid),
                    new_held,
                    int(paid),
                    int(paid),
                    paid_at,
                    subscription_ends,
                    stamp,
                    order_id,
                ),
            )
            return dict(self._required(connection, "SELECT * FROM orders WHERE id = ?", (order_id,), "order"))

    def _refund_wallet_hold(
        self,
        connection: sqlite3.Connection,
        order: sqlite3.Row,
        idempotency_key: str,
        stamp: str,
        amount: int | None = None,
    ) -> int:
        existing = connection.execute(
            "SELECT * FROM wallet_entries WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if existing:
            current_refundable = (
                int(order["wallet_held_amount"])
                - int(order["wallet_captured_amount"])
                - int(order["wallet_refunded_amount"])
            )
            original_refundable = current_refundable + int(existing["amount_signed"])
            expected_refund = (
                original_refundable
                if amount is None
                else min(original_refundable, max(0, int(amount)))
            )
            if (
                int(existing["user_id"]) != int(order["user_id"])
                or existing["order_id"] != order["id"]
                or existing["entry_type"] != "wallet_refund"
                or existing["reason"] != "Release/refund wallet hold"
                or existing["payment_id"] is not None
                or existing["actor_admin_id"] is not None
                or int(existing["amount_signed"]) != expected_refund
            ):
                raise ConflictError("idempotency key belongs to another wallet operation")
            return int(existing["amount_signed"])
        refundable = order["wallet_held_amount"] - order["wallet_captured_amount"] - order["wallet_refunded_amount"]
        refund = refundable if amount is None else min(refundable, max(0, int(amount)))
        if refund <= 0:
            return 0
        connection.execute(
            """
            INSERT INTO wallet_entries(
                user_id, order_id, amount_signed, entry_type, reason,
                idempotency_key, created_at
            ) VALUES (?, ?, ?, 'wallet_refund', ?, ?, ?)
            """,
            (order["user_id"], order["id"], refund, "Release/refund wallet hold", idempotency_key, stamp),
        )
        refunded = order["wallet_refunded_amount"] + refund
        effective_hold = order["wallet_held_amount"] - refunded
        payable = order["subtotal_amount"] - order["discount_amount"] - effective_hold
        connection.execute(
            """
            UPDATE orders
            SET wallet_refunded_amount = ?, payable_amount = ?, updated_at = ?
            WHERE id = ?
            """,
            (refunded, payable, stamp, order["id"]),
        )
        return refund

    def refund_wallet_hold(
        self,
        order_id: int,
        *,
        idempotency_key: str,
        amount: int | None = None,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        stamp = _timestamp(now)
        with self._transaction() as connection:
            order = self._required(connection, "SELECT * FROM orders WHERE id = ?", (order_id,), "order")
            refunded = self._refund_wallet_hold(connection, order, idempotency_key, stamp, amount)
            result = dict(self._required(connection, "SELECT * FROM orders WHERE id = ?", (order_id,), "order"))
            result["refunded_now"] = refunded
            return result

    # -- Discounts ----------------------------------------------------------

    def create_discount(
        self,
        code: str,
        *,
        discount_type: str,
        value: int,
        product_id: int | None = None,
        user_id: int | None = None,
        minimum_order_amount: int = 0,
        max_uses: int | None = None,
        per_user_limit: int | None = None,
        starts_at: datetime | str | None = None,
        ends_at: datetime | str | None = None,
        active: bool = True,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        clean_code = code.strip().upper()
        if not clean_code:
            raise ValidationError("discount code cannot be empty")
        if discount_type not in {"fixed", "percent"}:
            raise ValidationError("discount_type must be fixed or percent")
        if value <= 0 or (discount_type == "percent" and value > 100):
            raise ValidationError("invalid discount value")
        minimum_order_amount = int(minimum_order_amount)
        if minimum_order_amount < 0:
            raise ValidationError("minimum_order_amount cannot be negative")
        if max_uses is not None:
            max_uses = int(max_uses)
            if max_uses < 1:
                raise ValidationError("max_uses must be positive")
        if per_user_limit is not None:
            per_user_limit = int(per_user_limit)
            if per_user_limit < 1:
                raise ValidationError("per_user_limit must be positive")
        stamp = _timestamp(now)
        start_value = _timestamp(starts_at) if starts_at is not None else None
        end_value = _timestamp(ends_at) if ends_at is not None else None
        if start_value and end_value and start_value >= end_value:
            raise ValidationError("discount end must be after start")
        with self._transaction() as connection:
            if product_id is not None:
                self._required(
                    connection,
                    "SELECT id FROM products WHERE id = ?",
                    (product_id,),
                    "product",
                )
            if user_id is not None:
                self._required(
                    connection,
                    "SELECT id FROM users WHERE id = ?",
                    (user_id,),
                    "user",
                )
            existing = connection.execute("SELECT * FROM discounts WHERE code_key = ?", (clean_code.casefold(),)).fetchone()
            if existing:
                same = (
                    existing["discount_type"] == discount_type
                    and existing["value"] == int(value)
                    and existing["product_id"] == product_id
                    and existing["user_id"] == user_id
                    and existing["minimum_order_amount"] == minimum_order_amount
                    and existing["max_uses"] == max_uses
                    and existing["per_user_limit"] == per_user_limit
                    and existing["starts_at"] == start_value
                    and existing["ends_at"] == end_value
                )
                if not same:
                    raise ConflictError("discount code already exists with different terms")
                return dict(existing)
            cursor = connection.execute(
                """
                INSERT INTO discounts(
                    code, code_key, discount_type, value, product_id, user_id,
                    minimum_order_amount, max_uses, per_user_limit,
                    starts_at, ends_at, is_active, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    clean_code,
                    clean_code.casefold(),
                    discount_type,
                    int(value),
                    product_id,
                    user_id,
                    minimum_order_amount,
                    max_uses,
                    per_user_limit,
                    start_value,
                    end_value,
                    int(bool(active)),
                    stamp,
                    stamp,
                ),
            )
            return dict(self._required(connection, "SELECT * FROM discounts WHERE id = ?", (cursor.lastrowid,), "discount"))

    def list_discounts(self, *, active_only: bool = False, limit: int = 200) -> list[dict[str, Any]]:
        query = "SELECT * FROM discounts"
        if active_only:
            query += " WHERE is_active = 1"
        query += " ORDER BY id DESC LIMIT ?"
        with self._read() as connection:
            return _rows(connection.execute(query, (max(1, min(int(limit), 1_000)),)).fetchall())

    def toggle_discount(
        self,
        code: str,
        *,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        code_key = code.strip().upper().casefold()
        with self._transaction() as connection:
            discount = self._required(
                connection,
                "SELECT * FROM discounts WHERE code_key = ?",
                (code_key,),
                "discount",
            )
            connection.execute(
                "UPDATE discounts SET is_active = ?, updated_at = ? WHERE id = ?",
                (0 if discount["is_active"] else 1, _timestamp(now), discount["id"]),
            )
            return dict(self._required(connection, "SELECT * FROM discounts WHERE id = ?", (discount["id"],), "discount"))

    def set_discount_active(
        self,
        code: str,
        active: bool,
        *,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        """Set discount state explicitly so Telegram update replay is stable."""

        code_key = code.strip().upper().casefold()
        with self._transaction() as connection:
            discount = self._required(
                connection,
                "SELECT * FROM discounts WHERE code_key = ?",
                (code_key,),
                "discount",
            )
            connection.execute(
                "UPDATE discounts SET is_active = ?, updated_at = ? WHERE id = ?",
                (int(bool(active)), _timestamp(now), int(discount["id"])),
            )
            return dict(
                self._required(
                    connection,
                    "SELECT * FROM discounts WHERE id = ?",
                    (int(discount["id"]),),
                    "discount",
                )
            )

    def delete_discount(self, code: str) -> dict[str, Any]:
        """Delete an unused discount while preserving applied-order history."""

        code_key = code.strip().upper().casefold()
        if not code_key:
            raise ValidationError("discount code cannot be empty")
        with self._transaction() as connection:
            discount = self._required(
                connection,
                "SELECT * FROM discounts WHERE code_key = ?",
                (code_key,),
                "discount",
            )
            if connection.execute(
                "SELECT 1 FROM order_discounts WHERE discount_id = ? LIMIT 1",
                (discount["id"],),
            ).fetchone():
                raise ConflictError("an applied discount cannot be deleted; deactivate it instead")
            connection.execute("DELETE FROM discounts WHERE id = ?", (discount["id"],))
            return dict(discount)

    def apply_discount(
        self,
        order_id: int,
        code: str,
        *,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        stamp = _timestamp(now)
        code_key = code.strip().upper().casefold()
        with self._transaction() as connection:
            order = self._required(connection, "SELECT * FROM orders WHERE id = ?", (order_id,), "order")
            if order["status"] != "pending_payment":
                raise ValidationError("discounts can only be applied before payment")
            if _parse_timestamp(order["expires_at"]) <= _parse_timestamp(stamp):
                raise ValidationError("order has expired")
            if order["wallet_held_amount"] or order["external_paid_amount"]:
                raise ValidationError("apply discount before reserving funds")
            existing_application = connection.execute(
                """
                SELECT od.*, d.code_key FROM order_discounts od
                JOIN discounts d ON d.id = od.discount_id
                WHERE od.order_id = ? AND od.is_active = 1
                """,
                (order_id,),
            ).fetchone()
            if existing_application:
                if existing_application["code_key"] == code_key:
                    return dict(order)
                raise ConflictError("only one active discount is allowed per order")
            discount = self._required(
                connection,
                "SELECT * FROM discounts WHERE code_key = ?",
                (code_key,),
                "discount",
            )
            if not discount["is_active"]:
                raise ValidationError("discount is inactive")
            if discount["starts_at"] and stamp < discount["starts_at"]:
                raise ValidationError("discount is not active yet")
            if discount["ends_at"] and stamp >= discount["ends_at"]:
                raise ValidationError("discount has expired")
            if discount["product_id"] is not None and discount["product_id"] != order["product_id"]:
                raise ValidationError("discount is not valid for this product")
            if discount["user_id"] is not None and discount["user_id"] != order["user_id"]:
                raise ValidationError("discount is not valid for this user")
            if order["subtotal_amount"] < discount["minimum_order_amount"]:
                raise ValidationError("minimum order amount is not met")
            if discount["max_uses"] is not None and discount["used_count"] >= discount["max_uses"]:
                raise ValidationError("discount usage limit reached")
            if discount["per_user_limit"] is not None:
                user_uses = connection.execute(
                    """
                    SELECT COUNT(*) FROM order_discounts od
                    JOIN orders o ON o.id = od.order_id
                    WHERE od.discount_id = ? AND od.is_active = 1 AND o.user_id = ?
                    """,
                    (discount["id"], order["user_id"]),
                ).fetchone()[0]
                if user_uses >= discount["per_user_limit"]:
                    raise ValidationError("user discount limit reached")
            if discount["discount_type"] == "fixed":
                amount = min(order["subtotal_amount"], discount["value"])
            else:
                amount = min(
                    order["subtotal_amount"],
                    order["subtotal_amount"] * discount["value"] // 100,
                )
            connection.execute(
                """
                INSERT INTO order_discounts(order_id, discount_id, amount, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (order_id, discount["id"], amount, stamp),
            )
            connection.execute(
                "UPDATE discounts SET used_count = used_count + 1, updated_at = ? WHERE id = ?",
                (stamp, discount["id"]),
            )
            connection.execute(
                """
                UPDATE orders
                SET discount_amount = ?, payable_amount = subtotal_amount - ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (amount, amount, stamp, order_id),
            )
            return dict(self._required(connection, "SELECT * FROM orders WHERE id = ?", (order_id,), "order"))

    def confirm_zero_payable_order(
        self,
        order_id: int,
        user_id: int,
        *,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        """Confirm a zero-total summary without creating a financial payment."""
        stamp = _timestamp(now)
        with self._transaction() as connection:
            order = self._required(
                connection, "SELECT * FROM orders WHERE id = ?", (order_id,), "order"
            )
            if int(order["user_id"]) != int(user_id):
                raise NotFoundError("order not found")
            if (
                int(order["subtotal_amount"]) - int(order["discount_amount"]) != 0
                or int(order["wallet_held_amount"])
                or int(order["wallet_captured_amount"])
                or int(order["external_paid_amount"])
            ):
                raise ValidationError("only a zero-total order can be confirmed without payment")
            if order["status"] in {"paid", "processing", "awaiting_stock", "awaiting_info", "completed"}:
                return dict(order)
            if order["status"] != "pending_payment":
                raise ValidationError("order does not accept confirmation")
            if _parse_timestamp(order["expires_at"]) <= _parse_timestamp(stamp):
                raise ValidationError("order has expired")
            connection.execute(
                "UPDATE orders SET status = 'paid', paid_at = ?, updated_at = ? WHERE id = ?",
                (self._allocate_paid_timestamp(connection, stamp), stamp, order_id),
            )
            return dict(self._required(connection, "SELECT * FROM orders WHERE id = ?", (order_id,), "order"))

    @staticmethod
    def _release_active_discount(connection: sqlite3.Connection, order_id: int, stamp: str) -> None:
        application = connection.execute(
            "SELECT * FROM order_discounts WHERE order_id = ? AND is_active = 1",
            (order_id,),
        ).fetchone()
        if application is None:
            return
        connection.execute(
            "UPDATE order_discounts SET is_active = 0, released_at = ? WHERE id = ?",
            (stamp, application["id"]),
        )
        connection.execute(
            """
            UPDATE discounts
            SET used_count = CASE WHEN used_count > 0 THEN used_count - 1 ELSE 0 END,
                updated_at = ?
            WHERE id = ?
            """,
            (stamp, application["discount_id"]),
        )

    # -- Payment intents and order state ------------------------------------

    def _enforce_card_intent_rate(
        self,
        user_id: int,
        idempotency_key: str,
        stamp: str,
    ) -> None:
        """Persist a security event before rejecting abusive card churn."""

        current = _parse_timestamp(stamp)
        event_type: str | None = None
        details: dict[str, Any] = {}
        with self._transaction() as connection:
            if connection.execute(
                "SELECT 1 FROM payments WHERE idempotency_key = ?",
                (str(idempotency_key),),
            ).fetchone() is not None:
                return
            daily_cutoff = _timestamp(current - timedelta(days=1))
            daily_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM payments
                    WHERE user_id = ? AND method = 'card' AND created_at > ?
                    """,
                    (int(user_id), daily_cutoff),
                ).fetchone()[0]
            )
            cancel_cutoff = _timestamp(current - self.CARD_CANCEL_COOLDOWN)
            cancellation_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM card_payment_cancellations
                    WHERE user_id = ? AND created_at > ?
                    """,
                    (int(user_id), cancel_cutoff),
                ).fetchone()[0]
            )
            if cancellation_count >= int(self.CARD_CANCEL_BURST_LIMIT):
                event_type = "card_cancel_cooldown"
                details = {
                    "cancellations_in_window": cancellation_count,
                    "window_seconds": int(self.CARD_CANCEL_COOLDOWN.total_seconds()),
                }
            elif daily_count >= int(self.CARD_INTENT_DAILY_LIMIT):
                event_type = "card_daily_limit"
                details = {
                    "intents_in_24h": daily_count,
                    "limit": int(self.CARD_INTENT_DAILY_LIMIT),
                }
            if event_type is not None:
                hour_bucket = int(current.timestamp()) // 3_600
                connection.execute(
                    """
                    INSERT OR IGNORE INTO payment_security_events(
                        event_key, user_id, event_type, details_json, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        f"{event_type}:{int(user_id)}:{hour_bucket}",
                        int(user_id),
                        event_type,
                        _json_dump(details),
                        stamp,
                    ),
                )
        if event_type == "card_cancel_cooldown":
            raise ConflictError(
                "به‌دلیل لغوهای پیاپی، ساخت پرداخت کارت تا پایان دوره انتظار ممکن نیست"
            )
        if event_type == "card_daily_limit":
            raise ConflictError("سقف روزانه ساخت پرداخت کارت برای این حساب پر شده است")

    def list_payment_security_events(
        self, *, limit: int = 100, after_id: int = 0
    ) -> list[dict[str, Any]]:
        with self._read() as connection:
            return _rows(
                connection.execute(
                    """
                    SELECT event.*, user.chat_id, user.username
                    FROM payment_security_events event
                    JOIN users user ON user.id = event.user_id
                    WHERE event.id > ?
                    ORDER BY event.id LIMIT ?
                    """,
                    (
                        max(0, int(after_id)),
                        max(1, min(int(limit), 1_000)),
                    ),
                ).fetchall()
            )

    def _create_payment(
        self,
        connection: sqlite3.Connection,
        *,
        user_id: int,
        order_id: int | None,
        purpose: str,
        method: str,
        base_amount: int,
        currency: str,
        idempotency_key: str,
        provider_invoice_id: str | None,
        provider_invoice_url: str | None,
        receipt_file_id: str | None,
        expires_in_minutes: int,
        unique_amount_window: int,
        stamp: str,
    ) -> dict[str, Any]:
        if provider_invoice_url is not None and not is_safe_https_url(
            provider_invoice_url
        ):
            raise ValidationError(
                "provider_invoice_url must be a safe absolute HTTPS URL"
            )
        existing = connection.execute(
            "SELECT * FROM payments WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if existing:
            expected_currency = str(currency).strip().upper()
            mismatched = (
                int(existing["user_id"]) != int(user_id)
                or existing["order_id"] != order_id
                or existing["purpose"] != purpose
                or existing["method"] != method
                or int(existing["base_amount"]) != int(base_amount)
                or existing["currency"] != expected_currency
                or (
                    provider_invoice_id is not None
                    and existing["provider_invoice_id"] != provider_invoice_id
                )
                or (
                    provider_invoice_url is not None
                    and existing["provider_invoice_url"] != provider_invoice_url
                )
                or (
                    receipt_file_id is not None
                    and existing["receipt_file_id"] != receipt_file_id
                )
            )
            if mismatched:
                raise ConflictError("payment idempotency key belongs to another purchase")
            return dict(existing)
        self._required(connection, "SELECT id FROM users WHERE id = ?", (user_id,), "user")
        active_for_user = connection.execute(
            """
            SELECT COUNT(*) FROM payments
            WHERE user_id = ? AND method = ? AND status IN ('pending', 'verifying')
            """,
            (user_id, method),
        ).fetchone()[0]
        if int(active_for_user) >= 5:
            raise ConflictError("تعداد پرداخت‌های فعال شما برای این روش بیش از حد مجاز است")
        if method == "card":
            current = _parse_timestamp(stamp)
            daily_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM payments
                    WHERE user_id = ? AND method = 'card' AND created_at > ?
                    """,
                    (int(user_id), _timestamp(current - timedelta(days=1))),
                ).fetchone()[0]
            )
            cancellation_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM card_payment_cancellations
                    WHERE user_id = ? AND created_at > ?
                    """,
                    (
                        int(user_id),
                        _timestamp(current - self.CARD_CANCEL_COOLDOWN),
                    ),
                ).fetchone()[0]
            )
            if daily_count >= int(self.CARD_INTENT_DAILY_LIMIT):
                raise ConflictError("card payment daily creation limit exceeded")
            if cancellation_count >= int(self.CARD_CANCEL_BURST_LIMIT):
                raise ConflictError("card payment cancellation cooldown is active")
        payable = int(base_amount) if method != "card" else None
        reuse_cutoff = _parse_timestamp(stamp) - self.CARD_AMOUNT_REUSE_COOLDOWN
        for adjustment in range(max(0, int(unique_amount_window)) + 1):
            if method != "card":
                break
            candidate = int(base_amount) + adjustment
            prior_amounts = connection.execute(
                """
                SELECT status, expires_at, updated_at FROM payments
                WHERE method = 'card' AND currency = ? AND payable_amount = ?
                """,
                (currency, candidate),
            ).fetchall()
            collision = any(
                item["status"] in {"pending", "verifying"}
                or max(
                    _parse_timestamp(item["expires_at"]),
                    _parse_timestamp(item["updated_at"]),
                )
                > reuse_cutoff
                for item in prior_amounts
            )
            if not collision:
                payable = candidate
                break
        if payable is None:
            raise ConflictError("no unique payable amount is available in the requested window")
        expires_at = _timestamp(_parse_timestamp(stamp) + timedelta(minutes=expires_in_minutes))
        for _ in range(5):
            payment_number = f"PAY-{_parse_timestamp(stamp):%Y%m%d}-{uuid.uuid4().hex[:10].upper()}"
            try:
                cursor = connection.execute(
                    """
                    INSERT INTO payments(
                        payment_number, idempotency_key, order_id, user_id, purpose,
                        method, base_amount, payable_amount, currency, status,
                        provider_invoice_id, provider_invoice_url, receipt_file_id,
                        expires_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        payment_number,
                        idempotency_key,
                        order_id,
                        user_id,
                        purpose,
                        method,
                        int(base_amount),
                        payable,
                        currency,
                        provider_invoice_id,
                        provider_invoice_url,
                        receipt_file_id,
                        expires_at,
                        stamp,
                        stamp,
                    ),
                )
                return dict(
                    self._required(connection, "SELECT * FROM payments WHERE id = ?", (cursor.lastrowid,), "payment")
                )
            except sqlite3.IntegrityError as error:
                if "payment_number" not in str(error):
                    raise
        raise ConflictError("could not allocate a unique payment number")

    def create_order_payment(
        self,
        order_id: int,
        method: str,
        *,
        idempotency_key: str,
        requested_amount: int | None = None,
        provider_invoice_id: str | None = None,
        provider_invoice_url: str | None = None,
        receipt_file_id: str | None = None,
        expires_in_minutes: int = 30,
        unique_amount_window: int = 99,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        if expires_in_minutes <= 0:
            raise ValidationError("payment expiry must be positive")
        if provider_invoice_url is not None and not is_safe_https_url(
            provider_invoice_url
        ):
            raise ValidationError(
                "provider_invoice_url must be a safe absolute HTTPS URL"
            )
        stamp = _timestamp(now)
        if method == "card":
            with self._read() as connection:
                order_identity = connection.execute(
                    "SELECT user_id FROM orders WHERE id = ?", (int(order_id),)
                ).fetchone()
            if order_identity is None:
                raise NotFoundError("order not found")
            self._enforce_card_intent_rate(
                int(order_identity["user_id"]), idempotency_key, stamp
            )
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM payments WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing:
                if (
                    existing["order_id"] != int(order_id)
                    or existing["purpose"] != "order"
                    or existing["method"] != method
                    or (
                        requested_amount is not None
                        and int(existing["base_amount"]) != int(requested_amount)
                    )
                    or (
                        provider_invoice_id is not None
                        and existing["provider_invoice_id"] != provider_invoice_id
                    )
                    or (
                        provider_invoice_url is not None
                        and existing["provider_invoice_url"] != provider_invoice_url
                    )
                    or (
                        receipt_file_id is not None
                        and existing["receipt_file_id"] != receipt_file_id
                    )
                ):
                    raise ConflictError("payment idempotency key belongs to another purchase")
                return dict(existing)
            order = self._required(connection, "SELECT * FROM orders WHERE id = ?", (order_id,), "order")
            if order["status"] not in {"pending_payment", "awaiting_confirmation"}:
                raise ValidationError("order does not accept a payment")
            if _parse_timestamp(order["expires_at"]) <= _parse_timestamp(stamp):
                raise ValidationError("order has expired")
            remaining = order["payable_amount"] - order["external_paid_amount"]
            base_amount = remaining if requested_amount is None else int(requested_amount)
            if base_amount <= 0 or base_amount > remaining:
                raise ValidationError("payment amount must be within the remaining payable balance")
            # An issued provider/card intent represents a real external payment
            # instruction.  Keep exactly one such intent live for an order,
            # regardless of method, so switching methods cannot orphan an
            # invoice which may still be paid outside the bot.
            active = connection.execute(
                """
                SELECT * FROM payments
                WHERE order_id = ? AND status IN ('pending', 'verifying')
                ORDER BY id DESC LIMIT 1
                """,
                (order_id,),
            ).fetchone()
            if active:
                same_intent = (
                    active["method"] == method
                    and int(active["base_amount"]) == int(base_amount)
                    and (
                        provider_invoice_id is None
                        or active["provider_invoice_id"] == provider_invoice_id
                    )
                    and (
                        provider_invoice_url is None
                        or active["provider_invoice_url"] == provider_invoice_url
                    )
                    and (
                        receipt_file_id is None
                        or active["receipt_file_id"] == receipt_file_id
                    )
                )
                if not same_intent:
                    raise ConflictError(
                        "another external payment is already active for this order"
                    )
                connection.execute(
                    """
                    UPDATE orders
                    SET expires_at = CASE WHEN expires_at < ? THEN ? ELSE expires_at END,
                        status = 'awaiting_confirmation', updated_at = ?
                    WHERE id = ?
                    """,
                    (active["expires_at"], active["expires_at"], stamp, order_id),
                )
                return dict(active)
            payment = self._create_payment(
                connection,
                user_id=order["user_id"],
                order_id=order_id,
                purpose="order",
                method=method,
                base_amount=base_amount,
                currency=order["currency"],
                idempotency_key=idempotency_key,
                provider_invoice_id=provider_invoice_id,
                provider_invoice_url=provider_invoice_url,
                receipt_file_id=receipt_file_id,
                expires_in_minutes=expires_in_minutes,
                unique_amount_window=unique_amount_window,
                stamp=stamp,
            )
            connection.execute(
                """
                UPDATE orders
                SET status = 'awaiting_confirmation',
                    expires_at = CASE WHEN expires_at < ? THEN ? ELSE expires_at END,
                    updated_at = ?
                WHERE id = ?
                """,
                (payment["expires_at"], payment["expires_at"], stamp, order_id),
            )
            return payment

    def create_wallet_topup_payment(
        self,
        user_id: int,
        amount: int,
        method: str,
        *,
        idempotency_key: str,
        currency: str = "TOMAN",
        provider_invoice_id: str | None = None,
        provider_invoice_url: str | None = None,
        receipt_file_id: str | None = None,
        expires_in_minutes: int = 30,
        unique_amount_window: int = 99,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        if amount <= 0:
            raise ValidationError("top-up amount must be positive")
        if expires_in_minutes <= 0:
            raise ValidationError("payment expiry must be positive")
        if provider_invoice_url is not None and not is_safe_https_url(
            provider_invoice_url
        ):
            raise ValidationError(
                "provider_invoice_url must be a safe absolute HTTPS URL"
            )
        stamp = _timestamp(now)
        if method == "card":
            self._enforce_card_intent_rate(int(user_id), idempotency_key, stamp)
        normalized_currency = currency.strip().upper()
        if normalized_currency != "TOMAN":
            raise ValidationError("this shop supports TOMAN payments only")
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM payments WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing:
                if (
                    int(existing["user_id"]) != int(user_id)
                    or existing["order_id"] is not None
                    or existing["purpose"] != "wallet_topup"
                    or existing["method"] != method
                    or int(existing["base_amount"]) != int(amount)
                    or existing["currency"] != normalized_currency
                    or (
                        provider_invoice_id is not None
                        and existing["provider_invoice_id"] != provider_invoice_id
                    )
                    or (
                        provider_invoice_url is not None
                        and existing["provider_invoice_url"] != provider_invoice_url
                    )
                    or (
                        receipt_file_id is not None
                        and existing["receipt_file_id"] != receipt_file_id
                    )
                ):
                    raise ConflictError("payment idempotency key belongs to another operation")
                return dict(existing)

            # A top-up intent is a real external payment instruction. Keep at
            # most one live intent per user across methods so stale card and
            # crypto callbacks cannot create two independently payable bills.
            # Changing either method or amount requires explicit resolution of
            # the card intent; crypto remains tracked to a provider terminal.
            active = connection.execute(
                """
                SELECT * FROM payments
                WHERE user_id = ? AND purpose = 'wallet_topup'
                  AND status IN ('pending', 'verifying')
                ORDER BY id DESC LIMIT 1
                """,
                (user_id,),
            ).fetchone()
            if active:
                same_intent = (
                    active["method"] == method
                    and active["base_amount"] == int(amount)
                    and (
                        provider_invoice_id is None
                        or active["provider_invoice_id"] == provider_invoice_id
                    )
                    and (
                        provider_invoice_url is None
                        or active["provider_invoice_url"] == provider_invoice_url
                    )
                    and (
                        receipt_file_id is None
                        or active["receipt_file_id"] == receipt_file_id
                    )
                )
                if same_intent:
                    return dict(active)
                raise ConflictError(
                    "an active wallet top-up must be explicitly resolved first"
                )
            return self._create_payment(
                connection,
                user_id=user_id,
                order_id=None,
                purpose="wallet_topup",
                method=method,
                base_amount=int(amount),
                currency=normalized_currency,
                idempotency_key=idempotency_key,
                provider_invoice_id=provider_invoice_id,
                provider_invoice_url=provider_invoice_url,
                receipt_file_id=receipt_file_id,
                expires_in_minutes=expires_in_minutes,
                unique_amount_window=unique_amount_window,
                stamp=stamp,
            )

    def get_payment(self, payment_id: int) -> dict[str, Any] | None:
        with self._read() as connection:
            return _row(connection.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone())

    def get_payment_by_number(self, payment_number: str) -> dict[str, Any] | None:
        with self._read() as connection:
            return _row(
                connection.execute(
                    "SELECT * FROM payments WHERE payment_number = ?",
                    (payment_number,),
                ).fetchone()
            )

    def attach_crypto_invoice(
        self,
        payment_id: int,
        user_id: int,
        provider_invoice_id: str,
        provider_invoice_url: str,
        *,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        """Atomically attach an exact provider result to a provisional intent."""

        clean_provider_id = str(provider_invoice_id).strip()
        clean_url = str(provider_invoice_url).strip()
        if not clean_provider_id:
            raise ValidationError("provider invoice id cannot be empty")
        if not is_safe_https_url(clean_url):
            raise ValidationError(
                "provider_invoice_url must be a safe absolute HTTPS URL"
            )
        stamp = _timestamp(now)
        with self._transaction() as connection:
            payment = self._required(
                connection,
                "SELECT * FROM payments WHERE id = ? AND user_id = ?",
                (int(payment_id), int(user_id)),
                "payment",
            )
            if (
                payment["method"] != "crypto"
                or payment["status"] not in {"pending", "verifying"}
            ):
                raise ValidationError(
                    "only an active provisional crypto payment accepts an invoice"
                )
            existing_id = str(payment["provider_invoice_id"] or "")
            existing_url = str(payment["provider_invoice_url"] or "")
            if existing_id or existing_url:
                if existing_id == clean_provider_id and existing_url == clean_url:
                    return dict(payment)
                raise ConflictError(
                    "crypto top-up already belongs to another provider invoice"
                )
            collision = connection.execute(
                "SELECT id FROM payments WHERE provider_invoice_id = ? AND id <> ?",
                (clean_provider_id, int(payment_id)),
            ).fetchone()
            if collision is not None:
                raise ConflictError("provider invoice already belongs to another payment")
            connection.execute(
                """
                UPDATE payments
                SET provider_invoice_id = ?, provider_invoice_url = ?, updated_at = ?
                WHERE id = ?
                  AND provider_invoice_id IS NULL
                  AND provider_invoice_url IS NULL
                """,
                (clean_provider_id, clean_url, stamp, int(payment_id)),
            )
            return dict(
                self._required(
                    connection,
                    "SELECT * FROM payments WHERE id = ?",
                    (int(payment_id),),
                    "payment",
                )
            )

    def get_payment_by_external_reference(
        self, external_reference: str
    ) -> dict[str, Any] | None:
        value = str(external_reference).strip()
        if not value:
            return None
        with self._read() as connection:
            return _row(
                connection.execute(
                    "SELECT * FROM payments WHERE external_reference = ?",
                    (value,),
                ).fetchone()
            )

    def get_card_payment_event(self, reference: str) -> dict[str, Any] | None:
        value = str(reference).strip()
        if not value:
            return None
        with self._read() as connection:
            return _row(
                connection.execute(
                    "SELECT * FROM card_payment_events WHERE reference = ?",
                    (value,),
                ).fetchone()
            )

    def find_historical_card_payment_candidates(
        self,
        amount: int,
        occurred_at: datetime | str,
        *,
        currency: str = "TOMAN",
        limit: int = 2,
    ) -> list[dict[str, Any]]:
        event_time = _timestamp(occurred_at)
        with self._read() as connection:
            return _rows(
                connection.execute(
                    """
                    SELECT payment.*, user.chat_id AS user_chat_id,
                           user.username AS user_username
                    FROM payments payment
                    JOIN users user ON user.id = payment.user_id
                    WHERE payment.method = 'card'
                      AND payment.currency = ?
                      AND payment.payable_amount = ?
                      AND payment.created_at < ?
                      AND payment.expires_at >= ?
                    ORDER BY payment.id LIMIT ?
                    """,
                    (
                        str(currency).strip().upper(),
                        int(amount),
                        event_time,
                        event_time,
                        max(1, min(int(limit), 100)),
                    ),
                ).fetchall()
            )

    def record_card_payment_event(
        self,
        reference: str,
        amount: int,
        occurred_at: datetime | str,
        status: str,
        *,
        payment_id: int | None = None,
        raw_payload: Mapping[str, Any] | None = None,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        with self._transaction() as connection:
            return self._record_card_payment_event_in_transaction(
                connection,
                reference,
                amount,
                occurred_at,
                status,
                payment_id=payment_id,
                raw_payload=raw_payload,
                received_at=now,
            )

    def _record_card_payment_event_in_transaction(
        self,
        connection: sqlite3.Connection,
        reference: str,
        amount: int,
        occurred_at: datetime | str,
        status: str,
        *,
        payment_id: int | None = None,
        raw_payload: Mapping[str, Any] | None = None,
        received_at: datetime | str | None = None,
    ) -> dict[str, Any]:
        """Insert or validate one immutable card event on an existing transaction."""

        value = str(reference).strip()
        if not value:
            raise ValidationError("card event reference is required")
        event_amount = int(amount)
        if event_amount <= 0:
            raise ValidationError("card event amount must be positive")
        if status not in {"confirmed", "review"}:
            raise ValidationError("unsupported card event status")
        event_time = _timestamp(occurred_at)
        stamp = _timestamp(received_at)
        event_payment_id = int(payment_id) if payment_id is not None else None
        payload_json = _json_dump(dict(raw_payload or {}))

        linked_payment: sqlite3.Row | None = None
        if status == "confirmed":
            if event_payment_id is None:
                raise ValidationError("confirmed card event requires a payment")
            if raw_payload is None:
                raise ValidationError("confirmed card event requires its raw payload")
            linked_payment = self._required(
                connection,
                "SELECT * FROM payments WHERE id = ?",
                (event_payment_id,),
                "payment",
            )
            if (
                linked_payment["method"] != "card"
                or linked_payment["status"] not in {"paid", "refunded"}
                or linked_payment["external_reference"] != value
                or int(linked_payment["payable_amount"]) != event_amount
            ):
                raise ConflictError("confirmed card event does not match its payment")
            stored_payload = linked_payment["raw_payload_json"]
            if stored_payload is None or stored_payload != payload_json:
                raise ConflictError("card event payload does not match its payment")
        elif event_payment_id is not None:
            self._required(
                connection,
                "SELECT id FROM payments WHERE id = ?",
                (event_payment_id,),
                "payment",
            )

        existing = connection.execute(
            "SELECT * FROM card_payment_events WHERE reference = ?",
            (value,),
        ).fetchone()
        if existing:
            if (
                int(existing["amount"]) != event_amount
                or existing["occurred_at"] != event_time
                or existing["status"] != status
                or existing["payment_id"] != event_payment_id
                or existing["raw_payload_json"] != payload_json
            ):
                raise ConflictError("card event reference was reused with different terms")
            return dict(existing)

        cursor = connection.execute(
            """
            INSERT INTO card_payment_events(
                reference, amount, occurred_at, status, payment_id,
                raw_payload_json, received_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                value,
                event_amount,
                event_time,
                status,
                event_payment_id,
                payload_json,
                stamp,
            ),
        )
        return dict(
            self._required(
                connection,
                "SELECT * FROM card_payment_events WHERE id = ?",
                (cursor.lastrowid,),
                "card payment event",
            )
        )

    def list_card_payment_reviews(
        self, *, limit: int = 100, after_id: int = 0
    ) -> list[dict[str, Any]]:
        with self._read() as connection:
            return _rows(
                connection.execute(
                    """
                    SELECT event.*, payment.payment_number, payment.user_id,
                           payment.purpose, payment.order_id,
                           user.chat_id AS user_chat_id,
                           user.username AS user_username,
                           resolution.action AS resolution_action,
                           resolution.note AS resolution_note,
                           resolution.actor_admin_id AS resolution_actor_admin_id,
                           resolution.created_at AS resolved_at
                    FROM card_payment_events event
                    LEFT JOIN payments payment ON payment.id = event.payment_id
                    LEFT JOIN users user ON user.id = payment.user_id
                    LEFT JOIN card_payment_event_resolutions resolution
                      ON resolution.event_id = event.id
                    WHERE event.status = 'review' AND resolution.id IS NULL
                      AND event.id > ?
                    ORDER BY event.id LIMIT ?
                    """,
                    (
                        max(0, int(after_id)),
                        max(1, min(int(limit), 1_000)),
                    ),
                ).fetchall()
            )

    def resolve_card_payment_review(
        self,
        event_id: int,
        action: str,
        actor_admin_id: int,
        note: str,
        *,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        clean_action = str(action).strip().lower()
        clean_note = str(note).strip()
        if clean_action not in {"refund_confirmed", "dismiss"}:
            raise ValidationError("unsupported payment-review resolution")
        if not clean_note:
            raise ValidationError("payment-review resolution note is required")
        stamp = _timestamp(now)
        with self._transaction() as connection:
            self._required(
                connection,
                "SELECT id FROM admins WHERE id = ? AND role = 'owner' AND is_active = 1 "
                "AND identity_verified_at IS NOT NULL",
                (int(actor_admin_id),),
                "active owner",
            )
            event = self._required(
                connection,
                "SELECT * FROM card_payment_events WHERE id = ? AND status = 'review'",
                (int(event_id),),
                "card payment review",
            )
            existing = connection.execute(
                "SELECT * FROM card_payment_event_resolutions WHERE event_id = ?",
                (int(event_id),),
            ).fetchone()
            if existing is not None:
                if (
                    existing["action"] != clean_action
                    or int(existing["actor_admin_id"]) != int(actor_admin_id)
                    or existing["note"] != clean_note
                ):
                    raise ConflictError("card payment review was already resolved")
                return {
                    **dict(existing),
                    "reference": event["reference"],
                    "payment_id": event["payment_id"],
                }
            cursor = connection.execute(
                """
                INSERT INTO card_payment_event_resolutions(
                    event_id, action, actor_admin_id, note, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    int(event_id),
                    clean_action,
                    int(actor_admin_id),
                    clean_note,
                    stamp,
                ),
            )
            return {
                **dict(
                    self._required(
                        connection,
                        "SELECT * FROM card_payment_event_resolutions WHERE id = ?",
                        (int(cursor.lastrowid),),
                        "card payment review resolution",
                    )
                ),
                "reference": event["reference"],
                "payment_id": event["payment_id"],
            }

    def list_manual_card_review_resolutions(
        self, *, limit: int = 100, after_id: int = 0
    ) -> list[dict[str, Any]]:
        """Return linked bank-event decisions missing/requiring durable notice."""

        with self._read() as connection:
            return _rows(
                connection.execute(
                    """
                    SELECT resolution.*, event.payment_id, payment.user_id
                    FROM card_payment_event_resolutions resolution
                    JOIN card_payment_events event ON event.id = resolution.event_id
                    JOIN payments payment ON payment.id = event.payment_id
                    WHERE resolution.id > ?
                    ORDER BY resolution.id LIMIT ?
                    """,
                    (
                        max(0, int(after_id)),
                        max(1, min(int(limit), 1_000)),
                    ),
                ).fetchall()
            )

    def record_provider_payment_event(
        self,
        payment_id: int,
        provider: str,
        provider_reference: str,
        provider_status: str,
        raw_payload: Mapping[str, Any],
        *,
        received_amount: str | int | float | None,
        disposition: str,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        clean_provider = str(provider).strip().lower()
        clean_reference = str(provider_reference).strip()
        clean_status = str(provider_status).strip().lower()
        clean_disposition = str(disposition).strip().lower()
        if not clean_provider or not clean_reference or not clean_status:
            raise ValidationError(
                "provider, provider reference and provider status are required"
            )
        if clean_disposition not in {"completed", "failed", "review"}:
            raise ValidationError("unsupported provider-event disposition")
        if not isinstance(raw_payload, Mapping):
            raise ValidationError("provider event payload must be an object")

        normalized_received: str | None = None
        amount_evidence = "unknown"
        if received_amount is not None and not isinstance(received_amount, bool):
            try:
                parsed_amount = Decimal(str(received_amount).strip())
            except (InvalidOperation, ValueError):
                parsed_amount = Decimal("NaN")
            if parsed_amount.is_finite() and parsed_amount >= 0:
                normalized_received = format(parsed_amount, "f")
                amount_evidence = "zero" if parsed_amount == 0 else "nonzero"

        zero_fail_statuses = {"expired", "cancelled", "error"}
        if clean_disposition == "completed" and clean_status != "completed":
            raise ValidationError("only completed evidence can settle a provider payment")
        if clean_disposition == "failed" and not (
            clean_status in zero_fail_statuses and amount_evidence == "zero"
        ):
            raise ValidationError("provider-event disposition contradicts payment evidence")

        payload_json = _json_dump(dict(raw_payload))
        payload_sha256 = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        stamp = _timestamp(now)
        with self._transaction() as connection:
            payment = self._required(
                connection,
                "SELECT * FROM payments WHERE id = ?",
                (int(payment_id),),
                "payment",
            )
            if payment["method"] != "crypto":
                raise ValidationError("provider events are accepted only for crypto payments")
            if payment["provider_invoice_id"] != clean_reference:
                raise ConflictError("provider event reference does not match its payment")

            def supersede_open_reviews(
                resolving_event_id: int, action: str
            ) -> None:
                note = (
                    "A later live provider observation reported completed"
                    if action == "provider_completed"
                    else "A later live provider observation proved zero received amount"
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO provider_payment_event_resolutions(
                        event_id, action, actor_admin_id, resolving_event_id,
                        note, created_at
                    )
                    SELECT review.id, ?, NULL, ?, ?, ?
                    FROM provider_payment_events review
                    LEFT JOIN provider_payment_event_resolutions resolution
                      ON resolution.event_id = review.id
                    WHERE review.payment_id = ?
                      AND review.disposition = 'review'
                      AND resolution.id IS NULL
                    """,
                    (
                        action,
                        resolving_event_id,
                        note,
                        stamp,
                        int(payment_id),
                    ),
                )

            existing = connection.execute(
                """
                SELECT * FROM provider_payment_events
                WHERE provider = ? AND payment_id = ? AND raw_payload_sha256 = ?
                """,
                (clean_provider, int(payment_id), payload_sha256),
            ).fetchone()
            if existing is not None:
                if (
                    existing["provider_status"] != clean_status
                    or existing["provider_reference"] != clean_reference
                    or existing["received_amount"] != normalized_received
                    or existing["amount_evidence"] != amount_evidence
                    or existing["disposition"] != clean_disposition
                    or existing["raw_payload_json"] != payload_json
                ):
                    raise ConflictError("provider event hash belongs to different evidence")
                if clean_disposition == "review" and payment["status"] == "pending":
                    connection.execute(
                        """
                        UPDATE payments
                        SET status = 'verifying', raw_payload_json = ?, updated_at = ?
                        WHERE id = ? AND status = 'pending'
                        """,
                        (payload_json, stamp, int(payment_id)),
                    )
                elif clean_disposition == "completed":
                    supersede_open_reviews(
                        int(existing["id"]), "provider_completed"
                    )
                elif clean_disposition == "failed":
                    supersede_open_reviews(
                        int(existing["id"]), "provider_terminal_zero"
                    )
                    if payment["status"] in {"pending", "verifying"}:
                        connection.execute(
                            """
                            UPDATE payments
                            SET status = 'failed', raw_payload_json = ?, updated_at = ?
                            WHERE id = ? AND status IN ('pending', 'verifying')
                            """,
                            (payload_json, stamp, int(payment_id)),
                        )
                        self._reconcile_order_after_terminal_payment(
                            connection, payment, stamp
                        )
                return {**dict(existing), "is_new": False}
            cursor = connection.execute(
                """
                INSERT INTO provider_payment_events(
                    provider, payment_id, provider_reference, provider_status, received_amount,
                    amount_evidence, disposition, raw_payload_json,
                    raw_payload_sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    clean_provider,
                    int(payment_id),
                    clean_reference,
                    clean_status,
                    normalized_received,
                    amount_evidence,
                    clean_disposition,
                    payload_json,
                    payload_sha256,
                    stamp,
                ),
            )
            if clean_disposition == "review":
                if payment["status"] in {"pending", "verifying"}:
                    connection.execute(
                        """
                        UPDATE payments
                        SET status = 'verifying', raw_payload_json = ?, updated_at = ?
                        WHERE id = ? AND status = 'pending'
                        """,
                        (payload_json, stamp, int(payment_id)),
                    )
                elif payment["status"] in {"failed", "cancelled", "expired"}:
                    # A local terminal action cannot revoke a provider invoice.
                    # Later evidence is quarantined for owner review and never
                    # resurrects an order automatically.
                    pass
                else:
                    raise ConflictError(
                        "terminal payment cannot enter provider evidence review"
                    )
            elif clean_disposition == "completed":
                supersede_open_reviews(
                    int(cursor.lastrowid), "provider_completed"
                )
            elif clean_disposition == "failed":
                supersede_open_reviews(
                    int(cursor.lastrowid), "provider_terminal_zero"
                )
                if payment["status"] not in {
                    "pending",
                    "verifying",
                    "failed",
                    "cancelled",
                    "expired",
                }:
                    raise ConflictError(
                        "terminal payment cannot accept failed provider evidence"
                    )
                if payment["status"] in {"pending", "verifying"}:
                    connection.execute(
                        """
                        UPDATE payments
                        SET status = 'failed', raw_payload_json = ?, updated_at = ?
                        WHERE id = ? AND status IN ('pending', 'verifying')
                        """,
                        (payload_json, stamp, int(payment_id)),
                    )
                    self._reconcile_order_after_terminal_payment(
                        connection, payment, stamp
                    )
            return {
                **dict(
                    self._required(
                        connection,
                        "SELECT * FROM provider_payment_events WHERE id = ?",
                        (int(cursor.lastrowid),),
                        "provider payment event",
                    )
                ),
                "is_new": True,
            }

    def list_unapplied_completed_provider_events(
        self, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Return durable completed evidence whose payment is not yet settled."""

        with self._read() as connection:
            return _rows(
                connection.execute(
                    """
                    SELECT event.*, payment.provider_invoice_id,
                           payment.status AS payment_status
                    FROM provider_payment_events event
                    JOIN payments payment ON payment.id = event.payment_id
                    WHERE event.disposition = 'completed'
                      AND event.provider_status = 'completed'
                      AND event.provider_reference = payment.provider_invoice_id
                      AND payment.method = 'crypto'
                      AND payment.status IN ('pending', 'verifying')
                    ORDER BY event.id LIMIT ?
                    """,
                    (max(1, min(int(limit), 1_000)),),
                ).fetchall()
            )

    def get_provider_payment_event(self, event_id: int) -> dict[str, Any] | None:
        with self._read() as connection:
            return _row(
                connection.execute(
                    "SELECT * FROM provider_payment_events WHERE id = ?",
                    (int(event_id),),
                ).fetchone()
            )

    def list_provider_payment_reviews(
        self, *, limit: int = 100, after_id: int = 0
    ) -> list[dict[str, Any]]:
        with self._read() as connection:
            return _rows(
                connection.execute(
                    """
                    SELECT event.*, payment.payment_number, payment.user_id,
                           payment.order_id, payment.purpose, payment.base_amount,
                           payment.currency, payment.status AS payment_status,
                           resolution.action AS resolution_action,
                           resolution.note AS resolution_note,
                           resolution.actor_admin_id AS resolution_actor_admin_id,
                           resolution.created_at AS resolved_at
                    FROM provider_payment_events event
                    JOIN payments payment ON payment.id = event.payment_id
                    LEFT JOIN provider_payment_event_resolutions resolution
                      ON resolution.event_id = event.id
                    WHERE (
                            event.disposition = 'review'
                            OR (
                                event.disposition = 'completed'
                                AND payment.status IN ('failed', 'cancelled', 'expired')
                            )
                          )
                      AND resolution.id IS NULL
                      AND event.id > ?
                    ORDER BY event.id LIMIT ?
                    """,
                    (
                        max(0, int(after_id)),
                        max(1, min(int(limit), 1_000)),
                    ),
                ).fetchall()
            )

    def resolve_provider_payment_review(
        self,
        event_id: int,
        action: str,
        actor_admin_id: int,
        note: str,
        *,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        clean_action = str(action).strip().lower()
        clean_note = str(note).strip()
        if clean_action not in {
            "refund_confirmed",
            "dismiss",
            "credit_confirmed",
        }:
            raise ValidationError("unsupported payment-review resolution")
        if not clean_note:
            raise ValidationError("payment-review resolution note is required")
        stamp = _timestamp(now)
        with self._transaction() as connection:
            self._required(
                connection,
                "SELECT id FROM admins WHERE id = ? AND role = 'owner' AND is_active = 1 "
                "AND identity_verified_at IS NOT NULL",
                (int(actor_admin_id),),
                "active owner",
            )
            event = self._required(
                connection,
                """
                SELECT * FROM provider_payment_events
                WHERE id = ? AND disposition IN ('review', 'completed')
                """,
                (int(event_id),),
                "provider payment review",
            )
            payment = self._required(
                connection,
                "SELECT * FROM payments WHERE id = ?",
                (int(event["payment_id"]),),
                "payment",
            )
            existing = connection.execute(
                "SELECT * FROM provider_payment_event_resolutions WHERE event_id = ?",
                (int(event_id),),
            ).fetchone()
            if existing is not None:
                if existing["actor_admin_id"] is None:
                    raise ConflictError(
                        "provider payment review was superseded by provider evidence"
                    )
                if (
                    existing["action"] != clean_action
                    or int(existing["actor_admin_id"]) != int(actor_admin_id)
                    or existing["note"] != clean_note
                ):
                    raise ConflictError("provider payment review was already resolved")
                settlement = "closed_without_credit"
                if existing["action"] == "credit_confirmed":
                    settlement = (
                        "wallet_topup_credited"
                        if payment["purpose"] == "wallet_topup"
                        else "wallet_fallback_credited"
                    )
                return {
                    **dict(existing),
                    "payment_id": int(payment["id"]),
                    "settlement": settlement,
                }
            if payment["method"] != "crypto" or payment["status"] not in {
                "verifying",
                "failed",
                "cancelled",
                "expired",
            }:
                raise ConflictError("provider payment review is no longer open")
            if event["disposition"] == "completed" and payment["status"] not in {
                "failed",
                "cancelled",
                "expired",
            }:
                raise ConflictError(
                    "unapplied completed evidence must use automatic reconciliation"
                )
            if clean_action == "credit_confirmed":
                try:
                    evidence = _json_load(event["raw_payload_json"], {})
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise ConflictError("completed provider evidence is malformed") from exc
                if not isinstance(evidence, Mapping) or (
                    event["provider_status"] != "completed"
                    or str(evidence.get("status") or "").strip().lower()
                    != "completed"
                    or str(evidence.get("id") or "").strip()
                    != str(payment["provider_invoice_id"] or "")
                    or str(evidence.get("type") or "").strip().lower()
                    != "invoice"
                ):
                    raise ConflictError(
                        "credit requires exact live completed provider evidence"
                    )
                if payment["status"] not in {"failed", "cancelled", "expired"}:
                    raise ConflictError(
                        "late provider credit requires a previously resolved payment"
                    )
            cursor = connection.execute(
                """
                INSERT INTO provider_payment_event_resolutions(
                    event_id, action, actor_admin_id, note, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    int(event_id),
                    clean_action,
                    int(actor_admin_id),
                    clean_note,
                    stamp,
                ),
            )
            connection.execute(
                """
                UPDATE payments
                SET status = CASE
                        WHEN ? = 'credit_confirmed' THEN 'paid'
                        WHEN status = 'verifying' THEN 'failed'
                        ELSE status
                    END,
                    external_reference = CASE
                        WHEN ? = 'credit_confirmed' THEN provider_invoice_id
                        ELSE external_reference
                    END,
                    raw_payload_json = CASE
                        WHEN ? = 'credit_confirmed' THEN ?
                        ELSE raw_payload_json
                    END,
                    confirmed_at = CASE
                        WHEN ? = 'credit_confirmed' THEN COALESCE(confirmed_at, ?)
                        ELSE confirmed_at
                    END,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    clean_action,
                    clean_action,
                    clean_action,
                    event["raw_payload_json"],
                    clean_action,
                    stamp,
                    stamp,
                    int(payment["id"]),
                ),
            )
            settlement = "closed_without_credit"
            if clean_action != "credit_confirmed":
                if payment["status"] == "verifying":
                    self._reconcile_order_after_terminal_payment(
                        connection, payment, stamp
                    )
            elif payment["purpose"] == "wallet_topup":
                self._ensure_wallet_topup_credit(connection, payment, stamp)
                settlement = "wallet_topup_credited"
            else:
                # A review resolution may already have released wallet holds,
                # discounts, or stock. Never resurrect that terminal order.
                # Credit the now-proven external amount to the user's wallet
                # with a payment-level key so multiple completed observations
                # of the same invoice cannot create multiple credits.
                existing_credit = connection.execute(
                    """
                    SELECT * FROM wallet_entries
                    WHERE idempotency_key = ? OR payment_id = ?
                    ORDER BY id
                    """,
                    (
                        f"payment:{int(payment['id'])}:provider-credit",
                        int(payment["id"]),
                    ),
                ).fetchall()
                if existing_credit:
                    exact = (
                        len(existing_credit) == 1
                        and int(existing_credit[0]["user_id"])
                        == int(payment["user_id"])
                        and existing_credit[0]["order_id"] is None
                        and existing_credit[0]["payment_id"]
                        == int(payment["id"])
                        and existing_credit[0]["actor_admin_id"]
                        == int(actor_admin_id)
                        and int(existing_credit[0]["amount_signed"])
                        == int(payment["base_amount"])
                        and existing_credit[0]["entry_type"] == "manual_credit"
                        and existing_credit[0]["reason"]
                        == "اعتبار جبرانی پرداخت ارزی دیررس"
                        and existing_credit[0]["idempotency_key"]
                        == f"payment:{int(payment['id'])}:provider-credit"
                    )
                    if not exact:
                        raise ConflictError(
                            "late provider credit conflicts with wallet ledger"
                        )
                else:
                    connection.execute(
                        """
                        INSERT INTO wallet_entries(
                            user_id, payment_id, actor_admin_id, amount_signed,
                            entry_type, reason, idempotency_key, created_at
                        ) VALUES (?, ?, ?, ?, 'manual_credit', ?, ?, ?)
                        """,
                        (
                            int(payment["user_id"]),
                            int(payment["id"]),
                            int(actor_admin_id),
                            int(payment["base_amount"]),
                            "اعتبار جبرانی پرداخت ارزی دیررس",
                            f"payment:{int(payment['id'])}:provider-credit",
                            stamp,
                        ),
                    )
                settlement = "wallet_fallback_credited"
            if clean_action == "credit_confirmed":
                connection.execute(
                    """
                    INSERT OR IGNORE INTO provider_payment_event_resolutions(
                        event_id, action, actor_admin_id, note, created_at
                    )
                    SELECT other.id, 'credit_confirmed', ?, ?, ?
                    FROM provider_payment_events other
                    LEFT JOIN provider_payment_event_resolutions resolved
                      ON resolved.event_id = other.id
                    WHERE other.payment_id = ?
                      AND other.provider_status = 'completed'
                      AND json_valid(other.raw_payload_json)
                      AND json_extract(other.raw_payload_json, '$.id') = ?
                      AND lower(json_extract(other.raw_payload_json, '$.type')) = 'invoice'
                      AND resolved.id IS NULL
                    """,
                    (
                        int(actor_admin_id),
                        f"Covered by credit-confirmed resolution {int(event_id)}: {clean_note}",
                        stamp,
                        int(payment["id"]),
                        str(payment["provider_invoice_id"]),
                    ),
                )
            return {
                **dict(
                    self._required(
                        connection,
                        "SELECT * FROM provider_payment_event_resolutions WHERE id = ?",
                        (int(cursor.lastrowid),),
                        "provider payment review resolution",
                    )
                ),
                "payment_id": int(payment["id"]),
                "settlement": settlement,
            }

    def latest_order_payment(self, order_id: int) -> dict[str, Any] | None:
        with self._read() as connection:
            return _row(
                connection.execute(
                    "SELECT * FROM payments WHERE order_id = ? ORDER BY id DESC LIMIT 1",
                    (order_id,),
                ).fetchone()
            )

    def find_active_order_payment(self, order_id: int) -> dict[str, Any] | None:
        """Return the one live external intent for an order, if present."""

        with self._read() as connection:
            return _row(
                connection.execute(
                    """
                    SELECT * FROM payments
                    WHERE order_id = ? AND status IN ('pending', 'verifying')
                    ORDER BY id LIMIT 1
                    """,
                    (int(order_id),),
                ).fetchone()
            )

    def list_active_wallet_topup_payments(
        self, user_id: int
    ) -> list[dict[str, Any]]:
        """Return live top-ups, including multiple rows from older releases."""

        with self._read() as connection:
            return _rows(
                connection.execute(
                    """
                    SELECT * FROM payments
                    WHERE user_id = ? AND purpose = 'wallet_topup'
                      AND status IN ('pending', 'verifying')
                    ORDER BY id
                    """,
                    (int(user_id),),
                ).fetchall()
            )

    def find_active_payment(
        self,
        *,
        user_id: int,
        purpose: str,
        method: str,
        order_id: int | None = None,
        base_amount: int | None = None,
    ) -> dict[str, Any] | None:
        if purpose not in {"order", "wallet_topup"}:
            raise ValidationError("unsupported payment purpose")
        clauses = [
            "user_id = ?",
            "purpose = ?",
            "method = ?",
            "status IN ('pending', 'verifying')",
        ]
        parameters: list[Any] = [int(user_id), purpose, str(method)]
        if order_id is not None:
            clauses.append("order_id = ?")
            parameters.append(int(order_id))
        if base_amount is not None:
            clauses.append("base_amount = ?")
            parameters.append(int(base_amount))
        with self._read() as connection:
            return _row(
                connection.execute(
                    f"SELECT * FROM payments WHERE {' AND '.join(clauses)} "
                    "ORDER BY id DESC LIMIT 1",
                    parameters,
                ).fetchone()
            )

    def find_pending_payment_by_amount(
        self,
        amount: int,
        *,
        method: str = "card",
        currency: str | None = None,
    ) -> dict[str, Any] | None:
        clauses = ["payable_amount = ?", "method = ?", "status IN ('pending', 'verifying')"]
        parameters: list[Any] = [int(amount), method]
        if currency is not None:
            clauses.append("currency = ?")
            parameters.append(currency.strip().upper())
        with self._read() as connection:
            return _row(
                connection.execute(
                    f"SELECT * FROM payments WHERE {' AND '.join(clauses)} ORDER BY id LIMIT 1",
                    parameters,
                ).fetchone()
            )

    def list_pending_provider_payments(
        self,
        *,
        method: str = "crypto",
        limit: int = 100,
        after_id: int = 0,
    ) -> list[dict[str, Any]]:
        with self._read() as connection:
            return _rows(
                connection.execute(
                    """
                    SELECT * FROM payments
                    WHERE method = ? AND id > ?
                      AND provider_invoice_id IS NOT NULL AND (
                        status IN ('pending', 'verifying')
                        OR (
                            status IN ('failed', 'cancelled', 'expired')
                            AND provider_invoice_id IS NOT NULL
                            AND NOT EXISTS (
                                SELECT 1 FROM provider_payment_events completed_review
                                LEFT JOIN provider_payment_event_resolutions completed_resolution
                                  ON completed_resolution.event_id = completed_review.id
                                WHERE completed_review.payment_id = payments.id
                                  AND completed_review.provider_status = 'completed'
                                  AND json_valid(completed_review.raw_payload_json)
                                  AND json_extract(
                                      completed_review.raw_payload_json, '$.id'
                                  ) = payments.provider_invoice_id
                                   AND lower(json_extract(
                                       completed_review.raw_payload_json, '$.type'
                                   )) = 'invoice'
                                  AND (
                                      completed_resolution.id IS NULL
                                      OR completed_resolution.action = 'credit_confirmed'
                                  )
                            )
                        )
                    )
                    ORDER BY id LIMIT ?
                    """,
                    (
                        method,
                        max(0, int(after_id)),
                        max(1, min(int(limit), 1_000)),
                    ),
                ).fetchall()
            )

    def list_manual_provider_review_resolutions(
        self, *, limit: int = 100, after_id: int = 0
    ) -> list[dict[str, Any]]:
        """Return owner decisions whose user notice can be reconstructed."""

        with self._read() as connection:
            return _rows(
                connection.execute(
                    """
                    SELECT resolution.*, event.payment_id, payment.user_id,
                           payment.purpose, payment.base_amount, payment.currency,
                           CASE
                               WHEN resolution.action = 'credit_confirmed'
                                    AND payment.purpose = 'wallet_topup'
                                   THEN 'wallet_topup_credited'
                               WHEN resolution.action = 'credit_confirmed'
                                   THEN 'wallet_fallback_credited'
                               ELSE 'closed_without_credit'
                           END AS settlement
                    FROM provider_payment_event_resolutions resolution
                    JOIN provider_payment_events event
                      ON event.id = resolution.event_id
                    JOIN payments payment ON payment.id = event.payment_id
                    WHERE resolution.actor_admin_id IS NOT NULL
                      AND resolution.id > ?
                      AND (
                          resolution.action <> 'credit_confirmed'
                          OR resolution.id = (
                              SELECT MIN(other_resolution.id)
                              FROM provider_payment_event_resolutions other_resolution
                              JOIN provider_payment_events other_event
                                ON other_event.id = other_resolution.event_id
                              WHERE other_event.payment_id = event.payment_id
                                AND other_resolution.action = 'credit_confirmed'
                          )
                      )
                    ORDER BY resolution.id LIMIT ?
                    """,
                    (
                        max(0, int(after_id)),
                        max(1, min(int(limit), 1_000)),
                    ),
                ).fetchall()
            )

    def list_paid_payments_missing_notice(
        self, *, limit: int = 100, after_id: int = 0
    ) -> list[dict[str, Any]]:
        with self._read() as connection:
            return _rows(
                connection.execute(
                    """
                    SELECT payment.*,
                           EXISTS (
                               SELECT 1 FROM wallet_entries credit
                               WHERE credit.payment_id = payment.id
                                 AND credit.entry_type = 'manual_credit'
                                 AND credit.idempotency_key =
                                     'payment:' || payment.id || ':provider-credit'
                           ) AS provider_wallet_credit
                    FROM payments payment
                    WHERE payment.status = 'paid' AND payment.id > ?
                      AND NOT EXISTS (
                          SELECT 1 FROM outbound_messages outbound
                          WHERE outbound.idempotency_key =
                              'payment:' || payment.id ||
                              CASE payment.purpose
                                  WHEN 'wallet_topup' THEN ':topup-confirmed'
                                  ELSE CASE WHEN EXISTS (
                                      SELECT 1 FROM wallet_entries credit
                                      WHERE credit.payment_id = payment.id
                                        AND credit.entry_type = 'manual_credit'
                                        AND credit.idempotency_key =
                                            'payment:' || payment.id || ':provider-credit'
                                  ) THEN ':provider-wallet-credit'
                                  ELSE ':order-confirmed' END
                              END
                            AND outbound.status IN ('sent', 'failed', 'cancelled')
                      )
                    ORDER BY payment.id LIMIT ?
                    """,
                    (
                        max(0, int(after_id)),
                        max(1, min(int(limit), 1_000)),
                    ),
                ).fetchall()
            )

    def list_expired_wallet_topups_missing_notice(
        self, *, limit: int = 100, after_id: int = 0
    ) -> list[dict[str, Any]]:
        """Recover expiry warnings even when the expiry sweep already committed."""
        with self._read() as connection:
            return _rows(
                connection.execute(
                    """
                    SELECT payment.* FROM payments payment
                    WHERE payment.purpose = 'wallet_topup'
                      AND payment.status = 'expired' AND payment.id > ?
                      AND NOT EXISTS (
                          SELECT 1 FROM outbound_messages outbound
                          WHERE outbound.idempotency_key =
                              'payment:' || payment.id || ':topup-expired'
                            AND outbound.status IN ('sent', 'failed', 'cancelled')
                      )
                    ORDER BY payment.id LIMIT ?
                    """,
                    (max(0, int(after_id)), max(1, min(int(limit), 1_000))),
                ).fetchall()
            )

    def list_verifying_card_receipts(
        self, *, limit: int = 100, after_id: int = 0
    ) -> list[dict[str, Any]]:
        """Return submitted card receipts for durable admin-alert reconciliation."""

        with self._read() as connection:
            return _rows(
                connection.execute(
                    """
                    SELECT payment.*, attachment.file_kind AS receipt_file_kind,
                           user.chat_id AS user_chat_id,
                           user.username AS user_username
                    FROM payments payment
                    JOIN users user ON user.id = payment.user_id
                    LEFT JOIN payment_receipt_attachments attachment
                      ON attachment.payment_id = payment.id
                    WHERE payment.method = 'card'
                      AND payment.status = 'verifying'
                      AND payment.receipt_file_id IS NOT NULL
                      AND payment.id > ?
                    ORDER BY payment.id LIMIT ?
                    """,
                    (
                        max(0, int(after_id)),
                        max(1, min(int(limit), 1_000)),
                    ),
                ).fetchall()
            )

    def list_manual_orders_with_customer_info(
        self, *, limit: int = 100, after_id: int = 0
    ) -> list[dict[str, Any]]:
        """Return actionable manual-order submissions for alert recovery."""

        with self._read() as connection:
            return _rows(
                connection.execute(
                    """
                    SELECT orders.*, users.chat_id AS user_chat_id,
                           users.username AS user_username
                    FROM orders
                    JOIN users ON users.id = orders.user_id
                    WHERE orders.product_type_snapshot = 'manual'
                      AND orders.status IN ('awaiting_info', 'processing')
                      AND orders.customer_info_json IS NOT NULL
                      AND orders.customer_info_json <> ''
                      AND orders.id > ?
                    ORDER BY orders.id LIMIT ?
                    """,
                    (
                        max(0, int(after_id)),
                        max(1, min(int(limit), 1_000)),
                    ),
                ).fetchall()
            )

    def get_payment_receipt_attachment(
        self, payment_id: int
    ) -> dict[str, Any] | None:
        with self._read() as connection:
            return _row(
                connection.execute(
                    "SELECT * FROM payment_receipt_attachments WHERE payment_id = ?",
                    (int(payment_id),),
                ).fetchone()
            )

    def attach_payment_receipt(
        self,
        payment_id: int,
        file_id: str,
        *,
        file_kind: str | None = None,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        return self.submit_payment_receipt(
            payment_id, file_id, file_kind=file_kind, now=now
        )

    def submit_payment_receipt(
        self,
        payment_id: int,
        file_id: str,
        *,
        file_kind: str | None = None,
        now: datetime | str | None = None,
        review_grace_days: int | None = None,
    ) -> dict[str, Any]:
        """Persist a receipt and enter manual review until an admin decides.

        review_grace_days is accepted for compatibility only; a submitted
        receipt must never become terminal solely because an admin is delayed.
        """

        clean_file_id = str(file_id).strip()
        if not clean_file_id:
            raise ValidationError("receipt file id cannot be empty")
        clean_file_kind = str(file_kind or "document").strip().lower()
        if clean_file_kind not in {"photo", "document"}:
            raise ValidationError("receipt file kind must be photo or document")
        stamp = _timestamp(now)
        with self._transaction() as connection:
            payment = self._required(
                connection,
                "SELECT * FROM payments WHERE id = ?",
                (int(payment_id),),
                "payment",
            )
            if payment["status"] not in {"pending", "verifying"}:
                raise ValidationError("payment no longer accepts a receipt")
            if payment["method"] != "card":
                raise ValidationError("receipts are accepted only for card payments")
            deadline = _parse_timestamp(payment["expires_at"])
            received_at = _parse_timestamp(stamp)
            if payment["receipt_file_id"] is None and received_at >= deadline:
                raise ValidationError("the first receipt arrived after payment expiry")
            connection.execute(
                """
                UPDATE payments
                SET receipt_file_id = ?, status = 'verifying', updated_at = ?
                WHERE id = ?
                """,
                (clean_file_id, stamp, int(payment_id)),
            )
            connection.execute(
                """
                INSERT INTO payment_receipt_attachments(
                    payment_id, file_id, file_kind, submitted_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(payment_id) DO UPDATE SET
                    file_id = excluded.file_id,
                    file_kind = excluded.file_kind,
                    updated_at = excluded.updated_at
                """,
                (
                    int(payment_id),
                    clean_file_id,
                    clean_file_kind,
                    stamp,
                    stamp,
                ),
            )
            return dict(
                self._required(
                    connection,
                    "SELECT * FROM payments WHERE id = ?",
                    (int(payment_id),),
                    "payment",
                )
            )

    def cancel_pending_payment(
        self,
        payment_id: int,
        user_id: int,
        *,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        """Atomically cancel only an unsubmitted user-owned payment intent."""

        stamp = _timestamp(now)
        with self._transaction() as connection:
            payment = self._required(
                connection,
                "SELECT * FROM payments WHERE id = ? AND user_id = ?",
                (int(payment_id), int(user_id)),
                "payment",
            )
            if payment["status"] != "pending" or payment["receipt_file_id"] is not None:
                raise ValidationError("a submitted or reviewed payment cannot be cancelled")
            if payment["method"] == "crypto":
                raise ValidationError(
                    "an issued crypto invoice cannot be cancelled before provider expiry"
                )
            connection.execute(
                "UPDATE payments SET status = 'cancelled', updated_at = ? WHERE id = ?",
                (stamp, int(payment_id)),
            )
            if payment["method"] == "card":
                connection.execute(
                    """
                    INSERT OR IGNORE INTO card_payment_cancellations(
                        payment_id, user_id, created_at
                    ) VALUES (?, ?, ?)
                    """,
                    (int(payment_id), int(user_id), stamp),
                )
            if payment["order_id"] is not None:
                order = self._required(
                    connection,
                    "SELECT * FROM orders WHERE id = ?",
                    (payment["order_id"],),
                    "order",
                )
                another_active_payment = connection.execute(
                    """
                    SELECT 1 FROM payments
                    WHERE order_id = ? AND id <> ?
                      AND status IN ('pending', 'verifying')
                    LIMIT 1
                    """,
                    (order["id"], int(payment_id)),
                ).fetchone()
                if (
                    another_active_payment is None
                    and order["status"] in {"pending_payment", "awaiting_confirmation"}
                ):
                    self._refund_wallet_hold(
                        connection,
                        order,
                        f"order:{order['id']}:cancelled:wallet-release",
                        stamp,
                    )
                    self._release_active_discount(connection, order["id"], stamp)
                    connection.execute(
                        """
                        UPDATE reminders SET status = 'cancelled', updated_at = ?
                        WHERE order_id = ?
                          AND status IN ('pending', 'processing', 'failed')
                        """,
                        (stamp, order["id"]),
                    )
                    connection.execute(
                        """
                        UPDATE reservations SET status = 'cancelled'
                        WHERE order_id = ? AND status = 'queued'
                        """,
                        (order["id"],),
                    )
                    connection.execute(
                        "UPDATE orders SET status = 'cancelled', updated_at = ? WHERE id = ?",
                        (stamp, order["id"]),
                    )
            return dict(
                self._required(
                    connection,
                    "SELECT * FROM payments WHERE id = ?",
                    (int(payment_id),),
                    "payment",
                )
            )

    def _ensure_wallet_topup_credit(
        self,
        connection: sqlite3.Connection,
        payment: sqlite3.Row,
        stamp: str,
    ) -> None:
        payment_id = int(payment["id"])
        user_id = int(payment["user_id"])
        amount = int(payment["base_amount"])
        reason = "Confirmed wallet top-up"
        idempotency_key = f"payment:{payment_id}:wallet-credit"
        existing = connection.execute(
            """
            SELECT * FROM wallet_entries
            WHERE idempotency_key = ? OR payment_id = ?
            ORDER BY id
            """,
            (idempotency_key, payment_id),
        ).fetchall()
        if existing:
            exact_match = (
                len(existing) == 1
                and int(existing[0]["user_id"]) == user_id
                and existing[0]["order_id"] is None
                and existing[0]["payment_id"] == payment_id
                and existing[0]["actor_admin_id"] is None
                and int(existing[0]["amount_signed"]) == amount
                and existing[0]["entry_type"] == "topup"
                and existing[0]["reason"] == reason
                and existing[0]["idempotency_key"] == idempotency_key
            )
            if not exact_match:
                raise ConflictError(
                    "wallet top-up credit conflicts with an existing ledger operation"
                )
            return
        connection.execute(
            """
            INSERT INTO wallet_entries(
                user_id, payment_id, amount_signed, entry_type, reason,
                idempotency_key, created_at
            ) VALUES (?, ?, ?, 'topup', ?, ?, ?)
            """,
            (user_id, payment_id, amount, reason, idempotency_key, stamp),
        )

    def _reconcile_order_after_terminal_payment(
        self,
        connection: sqlite3.Connection,
        payment: sqlite3.Row,
        stamp: str,
    ) -> None:
        """Release or reopen the parent after its last external intent closes."""

        if payment["order_id"] is None:
            return
        order = self._required(
            connection,
            "SELECT * FROM orders WHERE id = ?",
            (payment["order_id"],),
            "order",
        )
        active_payment = connection.execute(
            """
            SELECT 1 FROM payments
            WHERE order_id = ? AND status IN ('pending', 'verifying')
            LIMIT 1
            """,
            (order["id"],),
        ).fetchone()
        if order["status"] != "awaiting_confirmation" or active_payment is not None:
            return
        if _parse_timestamp(order["expires_at"]) <= _parse_timestamp(stamp):
            self._refund_wallet_hold(
                connection,
                order,
                f"order:{order['id']}:payment-failure-expiry-wallet-release",
                stamp,
            )
            self._release_active_discount(connection, order["id"], stamp)
            connection.execute(
                "UPDATE orders SET status = 'expired', updated_at = ? WHERE id = ?",
                (stamp, order["id"]),
            )
            connection.execute(
                """
                UPDATE reminders SET status = 'cancelled', updated_at = ?
                WHERE order_id = ?
                  AND status IN ('pending', 'processing', 'failed')
                """,
                (stamp, order["id"]),
            )
            connection.execute(
                """
                UPDATE reservations SET status = 'cancelled'
                WHERE order_id = ? AND status = 'queued'
                """,
                (order["id"],),
            )
            return
        # Expose the method chooser after the last active intent reaches a
        # terminal result while the order itself remains inside its deadline.
        connection.execute(
            """
            UPDATE orders SET status = 'pending_payment', updated_at = ?
            WHERE id = ?
            """,
            (stamp, order["id"]),
        )

    def _queue_user_message_in_transaction(
        self,
        connection: sqlite3.Connection,
        user_id: int,
        body: str,
        idempotency_key: str,
        stamp: str,
        *,
        reply_markup: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean_body = str(body).strip()
        clean_key = str(idempotency_key).strip()
        if not clean_body or not clean_key:
            raise ValidationError("outbound body and idempotency key cannot be empty")
        if len(clean_body) > self.TELEGRAM_SAFE_MESSAGE_LENGTH:
            raise ValidationError("message body exceeds Telegram limit")
        expected_markup = (
            _json_dump(dict(reply_markup)) if reply_markup is not None else None
        )
        existing = connection.execute(
            "SELECT * FROM outbound_messages WHERE idempotency_key = ?",
            (clean_key,),
        ).fetchone()
        if existing is not None:
            if (
                existing["recipient_user_id"] != int(user_id)
                or existing["audience_json"] is not None
                or existing["body"] != clean_body
                or existing["reply_markup_json"] != expected_markup
            ):
                raise ConflictError(
                    "outbound idempotency key belongs to another message"
                )
            return dict(existing)
        self._required(
            connection,
            "SELECT id FROM users WHERE id = ?",
            (int(user_id),),
            "user",
        )
        cursor = connection.execute(
            """
            INSERT INTO outbound_messages(
                idempotency_key, recipient_user_id, body, reply_markup_json,
                scheduled_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                clean_key,
                int(user_id),
                clean_body,
                expected_markup,
                stamp,
                stamp,
                stamp,
            ),
        )
        return dict(
            self._required(
                connection,
                "SELECT * FROM outbound_messages WHERE id = ?",
                (int(cursor.lastrowid),),
                "outbound message",
            )
        )

    def set_payment_status(
        self,
        payment_id: int,
        status: str,
        *,
        external_reference: str | None = None,
        raw_payload: Mapping[str, Any] | None = None,
        card_event_amount: int | None = None,
        card_event_occurred_at: datetime | str | None = None,
        outbound_body: str | None = None,
        outbound_idempotency_key: str | None = None,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        if status not in self.PAYMENT_TRANSITIONS:
            raise ValidationError(f"unsupported payment status: {status}")
        if status == "refunded":
            raise ValidationError(
                "refund status requires a separate proven financial workflow"
            )
        if (outbound_body is None) != (outbound_idempotency_key is None):
            raise ValidationError(
                "payment outbox body and idempotency key are required together"
            )
        if outbound_body is not None and len(str(outbound_body).strip()) > (
            self.TELEGRAM_SAFE_MESSAGE_LENGTH
        ):
            raise ValidationError("message body exceeds Telegram limit")
        card_event_requested = (
            card_event_amount is not None or card_event_occurred_at is not None
        )
        if card_event_requested:
            if card_event_amount is None or card_event_occurred_at is None:
                raise ValidationError("card event amount and timestamp must be provided together")
            if status != "paid":
                raise ValidationError("a card event can only confirm a paid payment")
            if raw_payload is None:
                raise ValidationError("a confirmed card event requires its raw payload")
            card_reference = str(external_reference or "").strip()
            if not card_reference:
                raise ValidationError("a confirmed card event requires an external reference")
            external_reference = card_reference
        stamp = _timestamp(now)
        with self._transaction() as connection:
            payment = self._required(connection, "SELECT * FROM payments WHERE id = ?", (payment_id,), "payment")
            if payment["status"] == status:
                if (
                    status == "paid"
                    and external_reference is not None
                    and payment["external_reference"] != external_reference
                ):
                    raise ConflictError("paid payment belongs to a different external reference")
                if card_event_requested:
                    self._record_card_payment_event_in_transaction(
                        connection,
                        external_reference,
                        card_event_amount,
                        card_event_occurred_at,
                        "confirmed",
                        payment_id=payment_id,
                        raw_payload=raw_payload,
                        received_at=stamp,
                    )
                    payment = self._required(
                        connection,
                        "SELECT * FROM payments WHERE id = ?",
                        (payment_id,),
                        "payment",
                    )
                if status == "paid" and payment["purpose"] == "wallet_topup":
                    self._ensure_wallet_topup_credit(connection, payment, stamp)
                if outbound_body is not None:
                    self._queue_user_message_in_transaction(
                        connection,
                        int(payment["user_id"]),
                        outbound_body,
                        str(outbound_idempotency_key),
                        stamp,
                    )
                return dict(payment)
            if status not in self.PAYMENT_TRANSITIONS[payment["status"]]:
                raise ValidationError(f"invalid payment transition: {payment['status']} -> {status}")
            if payment["purpose"] == "wallet_topup" and status == "refunded":
                raise ValidationError(
                    "wallet top-up refunds require a coordinated wallet reversal"
                )
            try:
                connection.execute(
                    """
                    UPDATE payments
                    SET status = ?, external_reference = COALESCE(?, external_reference),
                        raw_payload_json = COALESCE(?, raw_payload_json),
                        confirmed_at = CASE WHEN ? = 'paid' THEN ? ELSE confirmed_at END,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        status,
                        external_reference,
                        _json_dump(dict(raw_payload)) if raw_payload is not None else None,
                        status,
                        stamp,
                        stamp,
                        payment_id,
                    ),
                )
            except sqlite3.IntegrityError as error:
                if external_reference:
                    raise ConflictError("external payment reference was already processed") from error
                raise
            if status == "paid":
                if payment["purpose"] == "wallet_topup":
                    self._ensure_wallet_topup_credit(connection, payment, stamp)
                else:
                    order = self._required(
                        connection,
                        "SELECT * FROM orders WHERE id = ?",
                        (payment["order_id"],),
                        "order",
                    )
                    external_paid = order["external_paid_amount"] + payment["base_amount"]
                    committed_wallet = order["wallet_held_amount"] - order["wallet_refunded_amount"]
                    total_due = order["subtotal_amount"] - order["discount_amount"]
                    fully_paid = external_paid + committed_wallet >= total_due
                    paid_at = (
                        order["paid_at"] or self._allocate_paid_timestamp(connection, stamp)
                        if fully_paid else order["paid_at"]
                    )
                    connection.execute(
                        """
                        UPDATE orders
                        SET external_paid_amount = ?,
                            wallet_captured_amount = CASE WHEN ? THEN ? ELSE wallet_captured_amount END,
                            status = CASE WHEN ? THEN 'paid' ELSE status END,
                            paid_at = CASE WHEN ? THEN ? ELSE paid_at END,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            external_paid,
                            int(fully_paid),
                            committed_wallet,
                            int(fully_paid),
                            int(fully_paid),
                            paid_at,
                            stamp,
                            order["id"],
                        ),
                    )
                    if fully_paid:
                        connection.execute(
                            """
                            UPDATE payments SET status = 'cancelled', updated_at = ?
                            WHERE order_id = ? AND id <> ? AND status IN ('pending', 'verifying')
                            """,
                            (stamp, order["id"], payment_id),
                        )
            elif status in {"failed", "cancelled", "expired"}:
                self._reconcile_order_after_terminal_payment(
                    connection, payment, stamp
                )
            if card_event_requested:
                self._record_card_payment_event_in_transaction(
                    connection,
                    external_reference,
                    card_event_amount,
                    card_event_occurred_at,
                    "confirmed",
                    payment_id=payment_id,
                    raw_payload=raw_payload,
                    received_at=stamp,
                )
            if outbound_body is not None:
                self._queue_user_message_in_transaction(
                    connection,
                    int(payment["user_id"]),
                    outbound_body,
                    str(outbound_idempotency_key),
                    stamp,
                )
            return dict(self._required(connection, "SELECT * FROM payments WHERE id = ?", (payment_id,), "payment"))

    def mark_payment_paid(
        self,
        payment_id: int,
        *,
        external_reference: str | None = None,
        raw_payload: Mapping[str, Any] | None = None,
        card_event_amount: int | None = None,
        card_event_occurred_at: datetime | str | None = None,
        outbound_body: str | None = None,
        outbound_idempotency_key: str | None = None,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        return self.set_payment_status(
            payment_id,
            "paid",
            external_reference=external_reference,
            raw_payload=raw_payload,
            card_event_amount=card_event_amount,
            card_event_occurred_at=card_event_occurred_at,
            outbound_body=outbound_body,
            outbound_idempotency_key=outbound_idempotency_key,
            now=now,
        )

    def _refund_captured_wallet(self, connection: sqlite3.Connection, order: sqlite3.Row, stamp: str) -> int:
        captured_refunded = int(
            connection.execute(
                """
                SELECT COALESCE(SUM(amount_signed), 0) FROM wallet_entries
                WHERE order_id = ? AND entry_type = 'order_refund'
                """,
                (order["id"],),
            ).fetchone()[0]
        )
        refundable = int(order["wallet_captured_amount"]) - captured_refunded
        if refundable <= 0:
            return 0
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO wallet_entries(
                user_id, order_id, amount_signed, entry_type, reason,
                idempotency_key, created_at
            ) VALUES (?, ?, ?, 'order_refund', ?, ?, ?)
            """,
            (
                order["user_id"],
                order["id"],
                refundable,
                "Refund captured wallet payment",
                f"order:{order['id']}:captured-wallet-refund",
                stamp,
            ),
        )
        if cursor.rowcount != 1:
            return 0
        connection.execute(
            "UPDATE orders SET wallet_refunded_amount = wallet_refunded_amount + ?, updated_at = ? WHERE id = ?",
            (refundable, stamp, order["id"]),
        )
        return refundable

    def update_order_status(
        self,
        order_id: int,
        status: str,
        *,
        admin_note: str | None = None,
        outbound_body: str | None = None,
        outbound_idempotency_key: str | None = None,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        if status not in self.ORDER_TRANSITIONS:
            raise ValidationError(f"unsupported order status: {status}")
        if status in {"awaiting_confirmation", "paid", "completed", "refunded"}:
            raise ValidationError(
                "financial and completion states require their dedicated workflow"
            )
        if (outbound_body is None) != (outbound_idempotency_key is None):
            raise ValidationError(
                "order outbox body and idempotency key are required together"
            )
        if outbound_body is not None and len(str(outbound_body).strip()) > (
            self.TELEGRAM_SAFE_MESSAGE_LENGTH
        ):
            raise ValidationError("message body exceeds Telegram limit")
        stamp = _timestamp(now)
        with self._transaction() as connection:
            order = self._required(connection, "SELECT * FROM orders WHERE id = ?", (order_id,), "order")
            if status == "awaiting_info" and order["product_type_snapshot"] != "manual":
                raise ValidationError("only manual-product orders can request more information")
            if status == "awaiting_stock":
                if order["product_type_snapshot"] != "ready":
                    raise ValidationError("only ready-product orders can await stock")
                queued = connection.execute(
                    "SELECT 1 FROM reservations WHERE order_id = ? AND status = 'queued'",
                    (order_id,),
                ).fetchone()
                if queued is None:
                    raise ValidationError("awaiting-stock order requires a queued reservation")
            if (
                status == "processing"
                and order["product_type_snapshot"] == "manual"
                and not _has_customer_information(order["customer_info_json"])
            ):
                raise ValidationError("manual order requires customer information before processing")
            if (
                status == "processing"
                and order["product_type_snapshot"] == "ready"
            ):
                raise ValidationError(
                    "ready-product processing is controlled by inventory fulfillment"
                )
            if order["status"] == status:
                if admin_note is None or order["admin_note"] == admin_note:
                    if outbound_body is not None:
                        self._queue_user_message_in_transaction(
                            connection,
                            int(order["user_id"]),
                            outbound_body,
                            str(outbound_idempotency_key),
                            stamp,
                        )
                    return dict(order)
                connection.execute(
                    "UPDATE orders SET admin_note = ?, updated_at = ? WHERE id = ?",
                    (admin_note, stamp, order_id),
                )
                if outbound_body is not None:
                    self._queue_user_message_in_transaction(
                        connection,
                        int(order["user_id"]),
                        outbound_body,
                        str(outbound_idempotency_key),
                        stamp,
                    )
                return dict(
                    self._required(
                        connection,
                        "SELECT * FROM orders WHERE id = ?",
                        (order_id,),
                        "order",
                    )
                )
            if status not in self.ORDER_TRANSITIONS[order["status"]]:
                raise ValidationError(f"invalid order transition: {order['status']} -> {status}")
            if status in {"expired", "cancelled", "rejected"}:
                active_payment = connection.execute(
                    """
                    SELECT method, receipt_file_id FROM payments
                    WHERE order_id = ? AND status IN ('pending', 'verifying')
                    ORDER BY id LIMIT 1
                    """,
                    (int(order_id),),
                ).fetchone()
                if active_payment is not None:
                    if active_payment["method"] == "crypto":
                        raise ConflictError(
                            "an active crypto invoice must reach a provider terminal result"
                        )
                    if active_payment["receipt_file_id"] is not None:
                        raise ConflictError(
                            "a submitted card receipt must use the payment rejection workflow"
                        )
                    raise ConflictError(
                        "an active external payment must use its dedicated cancellation workflow"
                    )
                self._refund_wallet_hold(
                    connection,
                    order,
                    f"order:{order_id}:{status}:wallet-release",
                    stamp,
                )
                self._release_active_discount(connection, order_id, stamp)
                connection.execute(
                    """
                    UPDATE payments SET status = ?, updated_at = ?
                    WHERE order_id = ? AND status IN ('pending', 'verifying')
                    """,
                    ("expired" if status == "expired" else "cancelled", stamp, order_id),
                )
            if status in {"cancelled", "expired", "rejected"}:
                connection.execute(
                    """
                    UPDATE reminders SET status = 'cancelled', updated_at = ?
                    WHERE order_id = ? AND status IN ('pending', 'processing', 'failed')
                    """,
                    (stamp, order_id),
                )
                connection.execute(
                    """
                    UPDATE reservations SET status = 'cancelled'
                    WHERE order_id = ? AND status = 'queued'
                    """,
                    (order_id,),
                )
            connection.execute(
                """
                UPDATE orders SET status = ?, admin_note = COALESCE(?, admin_note),
                    completed_at = CASE WHEN ? = 'completed' THEN ? ELSE completed_at END,
                    updated_at = ? WHERE id = ?
                """,
                (status, admin_note, status, stamp, stamp, order_id),
            )
            if outbound_body is not None:
                self._queue_user_message_in_transaction(
                    connection,
                    int(order["user_id"]),
                    outbound_body,
                    str(outbound_idempotency_key),
                    stamp,
                )
            return dict(self._required(connection, "SELECT * FROM orders WHERE id = ?", (order_id,), "order"))

    def mark_ready_order_processing(
        self,
        order_id: int,
        *,
        admin_note: str | None = None,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        """Enter the no-stock recovery state for a non-reserved ready order."""

        stamp = _timestamp(now)
        with self._transaction() as connection:
            order = self._required(
                connection,
                """
                SELECT orders.*, products.reserve_enabled
                FROM orders
                JOIN products ON products.id = orders.product_id
                WHERE orders.id = ?
                """,
                (int(order_id),),
                "order",
            )
            if order["product_type_snapshot"] != "ready":
                raise ValidationError("only ready-product orders use stock recovery")
            if bool(order["reserve_enabled"]):
                raise ValidationError(
                    "reserved ready orders must use the reservation workflow"
                )
            if order["status"] not in {"paid", "processing"}:
                raise ValidationError(
                    "stock recovery requires a paid ready-product order"
                )
            connection.execute(
                """
                UPDATE orders
                SET status = 'processing', admin_note = COALESCE(?, admin_note),
                    updated_at = ?
                WHERE id = ?
                """,
                (admin_note, stamp, int(order_id)),
            )
            return dict(
                self._required(
                    connection,
                    "SELECT * FROM orders WHERE id = ?",
                    (int(order_id),),
                    "order",
                )
            )

    def expire_unpaid_orders(
        self,
        *,
        now: datetime | str | None = None,
        limit: int = 500,
        receipt_review_grace_days: int | None = None,
    ) -> list[int]:
        stamp = _timestamp(now)
        expired_ids: list[int] = []
        with self._transaction() as connection:
            candidates = connection.execute(
                """
                SELECT o.* FROM orders o
                WHERE o.status IN ('pending_payment', 'awaiting_confirmation')
                  AND o.expires_at <= ?
                  AND NOT EXISTS (
                      SELECT 1 FROM payments crypto_payment
                      WHERE crypto_payment.order_id = o.id
                        AND crypto_payment.method = 'crypto'
                        AND crypto_payment.status IN ('pending', 'verifying')
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM payments p
                      WHERE p.order_id = o.id
                        AND p.status IN ('pending', 'verifying')
                        AND p.receipt_file_id IS NOT NULL
                  )
                ORDER BY o.expires_at LIMIT ?
                """,
                (stamp, max(1, min(int(limit), 5_000))),
            ).fetchall()
            for order in candidates:
                self._refund_wallet_hold(
                    connection,
                    order,
                    f"order:{order['id']}:expiry-wallet-release",
                    stamp,
                )
                self._release_active_discount(connection, order["id"], stamp)
                connection.execute(
                    "UPDATE orders SET status = 'expired', updated_at = ? WHERE id = ?",
                    (stamp, order["id"]),
                )
                connection.execute(
                    """
                    UPDATE payments SET status = 'expired', updated_at = ?
                    WHERE order_id = ? AND status IN ('pending', 'verifying')
                    """,
                    (stamp, order["id"]),
                )
                expired_ids.append(int(order["id"]))
        return expired_ids

    def expire_pending_payments(
        self,
        *,
        now: datetime | str | None = None,
        limit: int = 500,
        receipt_review_grace_days: int | None = None,
    ) -> list[int]:
        stamp = _timestamp(now)
        with self._transaction() as connection:
            rows = connection.execute(
                """
                SELECT id, order_id FROM payments
                WHERE method <> 'crypto'
                  AND status IN ('pending', 'verifying')
                  AND receipt_file_id IS NULL AND expires_at <= ?
                ORDER BY expires_at LIMIT ?
                """,
                (stamp, max(1, min(int(limit), 5_000))),
            ).fetchall()
            ids = [int(row["id"]) for row in rows]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                connection.execute(
                    f"UPDATE payments SET status = 'expired', updated_at = ? WHERE id IN ({placeholders})",
                    (stamp, *ids),
                )
                affected_order_ids = {
                    int(row["order_id"])
                    for row in rows
                    if row["order_id"] is not None
                }
                for order_id in affected_order_ids:
                    order = connection.execute(
                        "SELECT * FROM orders WHERE id = ?", (order_id,)
                    ).fetchone()
                    if order is None or order["status"] != "awaiting_confirmation":
                        continue
                    active_payment = connection.execute(
                        """
                        SELECT 1 FROM payments
                        WHERE order_id = ? AND status IN ('pending', 'verifying')
                        LIMIT 1
                        """,
                        (order_id,),
                    ).fetchone()
                    if active_payment is not None:
                        continue
                    if _parse_timestamp(order["expires_at"]) > _parse_timestamp(stamp):
                        connection.execute(
                            "UPDATE orders SET status = 'pending_payment', updated_at = ? "
                            "WHERE id = ?",
                            (stamp, order_id),
                        )
                        continue
                    self._refund_wallet_hold(
                        connection,
                        order,
                        f"order:{order_id}:expiry-wallet-release",
                        stamp,
                    )
                    self._release_active_discount(connection, order_id, stamp)
                    connection.execute(
                        "UPDATE orders SET status = 'expired', updated_at = ? WHERE id = ?",
                        (stamp, order_id),
                    )
                    connection.execute(
                        """
                        UPDATE reminders SET status = 'cancelled', updated_at = ?
                        WHERE order_id = ?
                          AND status IN ('pending', 'processing', 'failed')
                        """,
                        (stamp, order_id),
                    )
                    connection.execute(
                        """
                        UPDATE reservations SET status = 'cancelled'
                        WHERE order_id = ? AND status = 'queued'
                        """,
                        (order_id,),
                    )
            return ids

    # -- FAQs, support tickets and outbound messaging -----------------------

    def create_faq_category(
        self,
        name: str,
        *,
        active: bool = True,
        sort_order: int = 0,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        clean_name = name.strip()
        if not clean_name:
            raise ValidationError("FAQ category name cannot be empty")
        stamp = _timestamp(now)
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM faq_categories WHERE name = ? COLLATE NOCASE",
                (clean_name,),
            ).fetchone()
            if existing:
                return dict(existing)
            cursor = connection.execute(
                """
                INSERT INTO faq_categories(name, is_active, sort_order, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (clean_name, int(bool(active)), int(sort_order), stamp, stamp),
            )
            return dict(
                self._required(connection, "SELECT * FROM faq_categories WHERE id = ?", (cursor.lastrowid,), "FAQ category")
            )

    def list_faq_categories(self, *, active_only: bool = True) -> list[dict[str, Any]]:
        query = "SELECT * FROM faq_categories"
        if active_only:
            query += " WHERE is_active = 1"
        query += " ORDER BY sort_order, id"
        with self._read() as connection:
            return _rows(connection.execute(query).fetchall())

    def get_faq_category(self, category_id: int) -> dict[str, Any] | None:
        with self._read() as connection:
            return _row(
                connection.execute(
                    "SELECT * FROM faq_categories WHERE id = ?",
                    (category_id,),
                ).fetchone()
            )

    def update_faq_category(
        self,
        category_id: int,
        *,
        now: datetime | str | None = None,
        **changes: Any,
    ) -> dict[str, Any]:
        allowed = {"name", "sort_order"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValidationError(f"unsupported FAQ category fields: {sorted(unknown)}")
        if not changes:
            category = self.get_faq_category(category_id)
            if category is None:
                raise NotFoundError("FAQ category not found")
            return category
        if "name" in changes:
            changes["name"] = str(changes["name"]).strip()
            if not changes["name"]:
                raise ValidationError("FAQ category name cannot be empty")
        if "sort_order" in changes:
            changes["sort_order"] = int(changes["sort_order"])
        with self._transaction() as connection:
            self._required(
                connection,
                "SELECT id FROM faq_categories WHERE id = ?",
                (category_id,),
                "FAQ category",
            )
            if "name" in changes and connection.execute(
                "SELECT 1 FROM faq_categories WHERE id <> ? AND name = ? COLLATE NOCASE",
                (category_id, changes["name"]),
            ).fetchone():
                raise ConflictError("an FAQ category with this name already exists")
            assignments = [f"{field} = ?" for field in changes]
            connection.execute(
                f"UPDATE faq_categories SET {', '.join(assignments)}, updated_at = ? WHERE id = ?",
                (*changes.values(), _timestamp(now), category_id),
            )
            return dict(
                self._required(
                    connection,
                    "SELECT * FROM faq_categories WHERE id = ?",
                    (category_id,),
                    "FAQ category",
                )
            )

    def set_faq_category_active(
        self,
        category_id: int,
        active: bool,
        *,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE faq_categories SET is_active = ?, updated_at = ? WHERE id = ?",
                (int(bool(active)), _timestamp(now), category_id),
            )
            if cursor.rowcount != 1:
                raise NotFoundError("FAQ category not found")
            return dict(
                self._required(
                    connection,
                    "SELECT * FROM faq_categories WHERE id = ?",
                    (category_id,),
                    "FAQ category",
                )
            )

    def delete_faq_category(self, category_id: int) -> bool:
        with self._transaction() as connection:
            self._required(
                connection,
                "SELECT id FROM faq_categories WHERE id = ?",
                (category_id,),
                "FAQ category",
            )
            if connection.execute(
                "SELECT 1 FROM faqs WHERE category_id = ? LIMIT 1",
                (category_id,),
            ).fetchone():
                raise ConflictError("FAQ category still contains questions")
            return connection.execute(
                "DELETE FROM faq_categories WHERE id = ?",
                (category_id,),
            ).rowcount == 1

    def create_faq(
        self,
        question: str,
        answer: str,
        *,
        category_id: int | None = None,
        active: bool = True,
        sort_order: int = 0,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        question = question.strip()
        answer = answer.strip()
        if not question or not answer:
            raise ValidationError("FAQ question and answer are required")
        stamp = _timestamp(now)
        with self._transaction() as connection:
            if category_id is not None:
                self._required(
                    connection,
                    "SELECT id FROM faq_categories WHERE id = ?",
                    (category_id,),
                    "FAQ category",
                )
            existing = connection.execute(
                """
                SELECT * FROM faqs
                WHERE question = ? AND ((category_id = ?) OR (category_id IS NULL AND ? IS NULL))
                """,
                (question, category_id, category_id),
            ).fetchone()
            if existing:
                return dict(existing)
            cursor = connection.execute(
                """
                INSERT INTO faqs(category_id, question, answer, is_active, sort_order, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (category_id, question, answer, int(bool(active)), int(sort_order), stamp, stamp),
            )
            return dict(self._required(connection, "SELECT * FROM faqs WHERE id = ?", (cursor.lastrowid,), "FAQ"))

    def get_faq(self, faq_id: int) -> dict[str, Any] | None:
        with self._read() as connection:
            return _row(connection.execute("SELECT * FROM faqs WHERE id = ?", (faq_id,)).fetchone())

    def list_faqs(
        self,
        *,
        category_id: int | None = None,
        active_only: bool = True,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if category_id is not None:
            clauses.append("category_id = ?")
            parameters.append(category_id)
        if active_only:
            clauses.append("is_active = 1")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._read() as connection:
            return _rows(connection.execute(f"SELECT * FROM faqs{where} ORDER BY sort_order, id", parameters).fetchall())

    def update_faq(
        self,
        faq_id: int,
        *,
        now: datetime | str | None = None,
        **changes: Any,
    ) -> dict[str, Any]:
        allowed = {"question", "answer", "category_id", "sort_order"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValidationError(f"unsupported FAQ fields: {sorted(unknown)}")
        if not changes:
            faq = self.get_faq(faq_id)
            if faq is None:
                raise NotFoundError("FAQ not found")
            return faq
        for field in ("question", "answer"):
            if field in changes:
                changes[field] = str(changes[field]).strip()
                if not changes[field]:
                    raise ValidationError(f"FAQ {field} cannot be empty")
        if "category_id" in changes and changes["category_id"] is not None:
            changes["category_id"] = int(changes["category_id"])
            if changes["category_id"] < 1:
                raise ValidationError("FAQ category id must be positive")
        if "sort_order" in changes:
            changes["sort_order"] = int(changes["sort_order"])
        with self._transaction() as connection:
            current = self._required(
                connection,
                "SELECT * FROM faqs WHERE id = ?",
                (faq_id,),
                "FAQ",
            )
            if changes.get("category_id") is not None:
                self._required(
                    connection,
                    "SELECT id FROM faq_categories WHERE id = ?",
                    (changes["category_id"],),
                    "FAQ category",
                )
            effective_question = changes.get("question", current["question"])
            effective_category = changes.get("category_id", current["category_id"])
            if connection.execute(
                """
                SELECT 1 FROM faqs
                WHERE id <> ? AND question = ?
                  AND ((category_id = ?) OR (category_id IS NULL AND ? IS NULL))
                LIMIT 1
                """,
                (faq_id, effective_question, effective_category, effective_category),
            ).fetchone():
                raise ConflictError("this FAQ question already exists in the selected category")
            assignments = [f"{field} = ?" for field in changes]
            connection.execute(
                f"UPDATE faqs SET {', '.join(assignments)}, updated_at = ? WHERE id = ?",
                (*changes.values(), _timestamp(now), faq_id),
            )
            return dict(
                self._required(
                    connection,
                    "SELECT * FROM faqs WHERE id = ?",
                    (faq_id,),
                    "FAQ",
                )
            )

    def set_faq_active(
        self,
        faq_id: int,
        active: bool,
        *,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE faqs SET is_active = ?, updated_at = ? WHERE id = ?",
                (int(bool(active)), _timestamp(now), faq_id),
            )
            if cursor.rowcount != 1:
                raise NotFoundError("FAQ not found")
            return dict(
                self._required(
                    connection,
                    "SELECT * FROM faqs WHERE id = ?",
                    (faq_id,),
                    "FAQ",
                )
            )

    def delete_faq(self, faq_id: int) -> dict[str, Any]:
        with self._transaction() as connection:
            faq = self._required(
                connection,
                "SELECT * FROM faqs WHERE id = ?",
                (faq_id,),
                "FAQ",
            )
            connection.execute("DELETE FROM faqs WHERE id = ?", (faq_id,))
            return dict(faq)

    def create_ticket(
        self,
        user_id: int,
        subject: str,
        body: str | None = None,
        *,
        idempotency_key: str,
        attachment_file_id: str | None = None,
        attachment_kind: str | None = None,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        if not subject.strip():
            raise ValidationError("ticket subject is required")
        resolved_attachment_kind = self._ticket_attachment_kind(
            attachment_file_id, attachment_kind
        )
        stamp = _timestamp(now)
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM tickets WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing:
                initial = connection.execute(
                    "SELECT * FROM ticket_messages WHERE idempotency_key = ?",
                    (f"{idempotency_key}:initial",),
                ).fetchone()
                expected_body = body.strip() if body is not None else None
                initial_mismatch = (
                    (expected_body is None and initial is not None)
                    or (
                        expected_body is not None
                        and (
                            initial is None
                            or int(initial["ticket_id"]) != int(existing["id"])
                            or initial["sender_type"] != "user"
                            or int(initial["sender_user_id"]) != int(user_id)
                            or initial["body"] != expected_body
                            or initial["attachment_file_id"] != attachment_file_id
                            or initial["attachment_kind"] != resolved_attachment_kind
                        )
                    )
                )
                if (
                    int(existing["user_id"]) != int(user_id)
                    or existing["subject"] != subject.strip()
                    or initial_mismatch
                ):
                    raise ConflictError("ticket idempotency key belongs to another request")
                return dict(existing)
            self._required(connection, "SELECT id FROM users WHERE id = ?", (user_id,), "user")
            for _ in range(5):
                ticket_number = f"TKT-{_parse_timestamp(stamp):%Y%m%d}-{uuid.uuid4().hex[:8].upper()}"
                try:
                    cursor = connection.execute(
                        """
                        INSERT INTO tickets(
                            ticket_number, idempotency_key, user_id, subject,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (ticket_number, idempotency_key, user_id, subject.strip(), stamp, stamp),
                    )
                    ticket_id = int(cursor.lastrowid)
                    break
                except sqlite3.IntegrityError as error:
                    if "ticket_number" not in str(error):
                        raise
            else:
                raise ConflictError("could not allocate a unique ticket number")
            if body is not None:
                if not body.strip():
                    raise ValidationError("initial ticket message cannot be empty")
                connection.execute(
                    """
                    INSERT INTO ticket_messages(
                        ticket_id, sender_type, sender_user_id, body,
                        attachment_file_id, attachment_kind,
                        idempotency_key, created_at
                    ) VALUES (?, 'user', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ticket_id,
                        user_id,
                        body.strip(),
                        attachment_file_id,
                        resolved_attachment_kind,
                        f"{idempotency_key}:initial",
                        stamp,
                    ),
                )
            return dict(self._required(connection, "SELECT * FROM tickets WHERE id = ?", (ticket_id,), "ticket"))

    def add_ticket_message(
        self,
        ticket_id: int,
        body: str,
        *,
        sender_type: str,
        sender_id: int | None = None,
        sender_user_id: int | None = None,
        sender_admin_id: int | None = None,
        idempotency_key: str,
        attachment_file_id: str | None = None,
        attachment_kind: str | None = None,
        outbound_body: str | None = None,
        outbound_idempotency_key: str | None = None,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        if sender_type not in {"user", "admin"}:
            raise ValidationError("sender_type must be user or admin")
        if not body.strip():
            raise ValidationError("ticket message cannot be empty")
        resolved_attachment_kind = self._ticket_attachment_kind(
            attachment_file_id, attachment_kind
        )
        resolved_sender = sender_id
        if resolved_sender is None:
            resolved_sender = sender_user_id if sender_type == "user" else sender_admin_id
        if resolved_sender is None:
            raise ValidationError("sender id is required")
        if (outbound_body is None) != (outbound_idempotency_key is None):
            raise ValidationError(
                "ticket message outbox body and idempotency key are required together"
            )
        if outbound_body is not None and len(str(outbound_body).strip()) > (
            self.TELEGRAM_SAFE_MESSAGE_LENGTH
        ):
            raise ValidationError("ticket message notification exceeds Telegram limit")
        resolved_user_id = resolved_sender if sender_type == "user" else None
        resolved_admin_id = resolved_sender if sender_type == "admin" else None
        clean_body = body.strip()
        stamp = _timestamp(now)
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM ticket_messages WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing:
                if (
                    int(existing["ticket_id"]) != int(ticket_id)
                    or existing["sender_type"] != sender_type
                    or existing["sender_user_id"] != resolved_user_id
                    or existing["sender_admin_id"] != resolved_admin_id
                    or existing["body"] != clean_body
                    or existing["attachment_file_id"] != attachment_file_id
                    or existing["attachment_kind"] != resolved_attachment_kind
                ):
                    raise ConflictError(
                        "ticket message idempotency key belongs to another request"
                    )
                if outbound_body is not None:
                    ticket = self._required(
                        connection,
                        "SELECT * FROM tickets WHERE id = ?",
                        (int(existing["ticket_id"]),),
                        "ticket",
                    )
                    self._queue_user_message_in_transaction(
                        connection,
                        int(ticket["user_id"]),
                        outbound_body,
                        str(outbound_idempotency_key),
                        stamp,
                    )
                return dict(existing)
            ticket = self._required(connection, "SELECT * FROM tickets WHERE id = ?", (ticket_id,), "ticket")
            if ticket["status"] == "closed":
                raise ValidationError("closed ticket cannot receive messages")
            if sender_type == "user" and resolved_sender != ticket["user_id"]:
                raise ValidationError("user does not own this ticket")
            entity_table = "users" if sender_type == "user" else "admins"
            self._required(connection, f"SELECT id FROM {entity_table} WHERE id = ?", (resolved_sender,), sender_type)
            cursor = connection.execute(
                """
                INSERT INTO ticket_messages(
                    ticket_id, sender_type, sender_user_id, sender_admin_id,
                    body, attachment_file_id, attachment_kind,
                    idempotency_key, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ticket_id,
                    sender_type,
                    resolved_user_id,
                    resolved_admin_id,
                    clean_body,
                    attachment_file_id,
                    resolved_attachment_kind,
                    idempotency_key,
                    stamp,
                ),
            )
            connection.execute(
                """
                UPDATE tickets
                SET status = ?, assigned_admin_id = COALESCE(?, assigned_admin_id), updated_at = ?
                WHERE id = ?
                """,
                ("answered" if sender_type == "admin" else "open", resolved_admin_id, stamp, ticket_id),
            )
            if outbound_body is not None:
                self._queue_user_message_in_transaction(
                    connection,
                    int(ticket["user_id"]),
                    outbound_body,
                    str(outbound_idempotency_key),
                    stamp,
                )
            return dict(
                self._required(connection, "SELECT * FROM ticket_messages WHERE id = ?", (cursor.lastrowid,), "ticket message")
            )

    @staticmethod
    def _ticket_attachment_kind(
        attachment_file_id: str | None,
        attachment_kind: str | None,
    ) -> str | None:
        if attachment_file_id is None:
            if attachment_kind is not None:
                raise ValidationError("attachment kind requires a file id")
            return None
        resolved = (attachment_kind or "document").strip().lower()
        if resolved not in {"photo", "document"}:
            raise ValidationError("attachment kind must be photo or document")
        return resolved

    def get_ticket_message(self, message_id: int) -> dict[str, Any] | None:
        with self._read() as connection:
            return _row(
                connection.execute(
                    """
                    SELECT tm.*, t.user_id AS ticket_user_id
                    FROM ticket_messages tm
                    JOIN tickets t ON t.id = tm.ticket_id
                    WHERE tm.id = ?
                    """,
                    (int(message_id),),
                ).fetchone()
            )

    def get_ticket(self, ticket_id: int | str) -> dict[str, Any] | None:
        with self._read() as connection:
            if isinstance(ticket_id, str) and not ticket_id.isdigit():
                result = connection.execute("SELECT * FROM tickets WHERE ticket_number = ?", (ticket_id,)).fetchone()
            else:
                result = connection.execute("SELECT * FROM tickets WHERE id = ?", (int(ticket_id),)).fetchone()
            return _row(result)

    def list_tickets(
        self,
        *,
        user_id: int | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if user_id is not None:
            clauses.append("user_id = ?")
            parameters.append(user_id)
        if status is not None:
            clauses.append("status = ?")
            parameters.append(status)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.extend(
            (max(1, min(int(limit), 1_000)), max(0, int(offset)))
        )
        with self._read() as connection:
            return _rows(
                connection.execute(
                    f"SELECT * FROM tickets{where} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                    parameters,
                ).fetchall()
            )

    def count_tickets(
        self,
        *,
        user_id: int | None = None,
        status: str | None = None,
    ) -> int:
        clauses: list[str] = []
        parameters: list[Any] = []
        if user_id is not None:
            clauses.append("user_id = ?")
            parameters.append(int(user_id))
        if status is not None:
            clauses.append("status = ?")
            parameters.append(status)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._read() as connection:
            return int(
                connection.execute(
                    f"SELECT COUNT(*) FROM tickets{where}", parameters
                ).fetchone()[0]
            )

    def list_ticket_messages(
        self,
        ticket_id: int,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        start = max(0, int(offset))
        with self._read() as connection:
            if limit is None:
                rows = connection.execute(
                    "SELECT * FROM ticket_messages WHERE ticket_id = ? ORDER BY id LIMIT -1 OFFSET ?",
                    (ticket_id, start),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM ticket_messages WHERE ticket_id = ? ORDER BY id LIMIT ? OFFSET ?",
                    (ticket_id, max(1, min(int(limit), 10_000)), start),
                ).fetchall()
            return _rows(rows)

    def list_user_ticket_messages(
        self,
        *,
        limit: int = 100,
        after_id: int = 0,
    ) -> list[dict[str, Any]]:
        """Return a bounded keyset page of user-authored ticket messages."""

        with self._read() as connection:
            return _rows(
                connection.execute(
                    """
                    SELECT tm.*, t.ticket_number, t.subject,
                           t.user_id AS ticket_user_id,
                           u.chat_id AS user_chat_id,
                           u.username AS user_username
                    FROM ticket_messages tm
                    JOIN tickets t ON t.id = tm.ticket_id
                    JOIN users u ON u.id = t.user_id
                    WHERE tm.sender_type = 'user' AND tm.id > ?
                    ORDER BY tm.id
                    LIMIT ?
                    """,
                    (
                        max(0, int(after_id)),
                        max(1, min(int(limit), 1_000)),
                    ),
                ).fetchall()
            )

    def count_ticket_messages(self, ticket_id: int) -> int:
        with self._read() as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM ticket_messages WHERE ticket_id = ?",
                    (int(ticket_id),),
                ).fetchone()[0]
            )

    def close_ticket(
        self,
        ticket_id: int,
        *,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        stamp = _timestamp(now)
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE tickets SET status = 'closed', closed_at = ?, updated_at = ? WHERE id = ?",
                (stamp, stamp, ticket_id),
            )
            if cursor.rowcount != 1:
                raise NotFoundError("ticket not found")
            return dict(self._required(connection, "SELECT * FROM tickets WHERE id = ?", (ticket_id,), "ticket"))

    def set_ticket_status(
        self,
        ticket_id: int,
        status: str,
        *,
        assigned_admin_id: int | None = None,
        outbound_body: str | None = None,
        outbound_idempotency_key: str | None = None,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        if status not in {"open", "answered", "closed"}:
            raise ValidationError("unsupported ticket status")
        if (outbound_body is None) != (outbound_idempotency_key is None):
            raise ValidationError(
                "ticket status outbox body and idempotency key are required together"
            )
        if outbound_body is not None and len(str(outbound_body).strip()) > (
            self.TELEGRAM_SAFE_MESSAGE_LENGTH
        ):
            raise ValidationError("ticket status notification exceeds Telegram limit")
        stamp = _timestamp(now)
        with self._transaction() as connection:
            ticket = self._required(
                connection,
                "SELECT * FROM tickets WHERE id = ?",
                (int(ticket_id),),
                "ticket",
            )
            if assigned_admin_id is not None:
                self._required(
                    connection,
                    "SELECT id FROM admins WHERE id = ? AND is_active = 1",
                    (int(assigned_admin_id),),
                    "admin",
                )
            connection.execute(
                """
                UPDATE tickets
                SET status = ?,
                    assigned_admin_id = COALESCE(?, assigned_admin_id),
                    closed_at = CASE WHEN ? = 'closed' THEN COALESCE(closed_at, ?) ELSE NULL END,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    assigned_admin_id,
                    status,
                    stamp,
                    stamp,
                    int(ticket_id),
                ),
            )
            if outbound_body is not None:
                self._queue_user_message_in_transaction(
                    connection,
                    int(ticket["user_id"]),
                    outbound_body,
                    str(outbound_idempotency_key),
                    stamp,
                )
            return dict(
                self._required(
                    connection,
                    "SELECT * FROM tickets WHERE id = ?",
                    (int(ticket_id),),
                    "ticket",
                )
            )

    def queue_outbound_message(
        self,
        body: str,
        *,
        idempotency_key: str,
        recipient_user_id: int | None = None,
        audience: Mapping[str, Any] | None = None,
        reply_markup: Mapping[str, Any] | None = None,
        scheduled_at: datetime | str | None = None,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        clean_body = body.strip()
        if not clean_body:
            raise ValidationError("message body cannot be empty")
        if len(clean_body) > self.TELEGRAM_SAFE_MESSAGE_LENGTH:
            raise ValidationError("message body exceeds Telegram limit")
        if recipient_user_id is None and audience is None:
            raise ValidationError("recipient_user_id or audience is required")
        stamp = _timestamp(now)
        due = _timestamp(scheduled_at) if scheduled_at is not None else stamp
        expected_recipient = (
            int(recipient_user_id) if recipient_user_id is not None else None
        )
        expected_audience = _json_dump(dict(audience)) if audience is not None else None
        expected_markup = (
            _json_dump(dict(reply_markup)) if reply_markup is not None else None
        )
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM outbound_messages WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing:
                if (
                    existing["recipient_user_id"] != expected_recipient
                    or existing["audience_json"] != expected_audience
                    or existing["body"] != clean_body
                    or existing["reply_markup_json"] != expected_markup
                    or (
                        scheduled_at is not None
                        and existing["scheduled_at"] != due
                    )
                ):
                    raise ConflictError(
                        "outbound idempotency key belongs to another message"
                    )
                return dict(existing)
            if expected_recipient is not None:
                self._required(connection, "SELECT id FROM users WHERE id = ?", (expected_recipient,), "user")
            cursor = connection.execute(
                """
                INSERT INTO outbound_messages(
                    idempotency_key, recipient_user_id, audience_json, body,
                    reply_markup_json, scheduled_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    idempotency_key,
                    expected_recipient,
                    expected_audience,
                    clean_body,
                    expected_markup,
                    due,
                    stamp,
                    stamp,
                ),
            )
            return dict(
                self._required(connection, "SELECT * FROM outbound_messages WHERE id = ?", (cursor.lastrowid,), "message")
            )

    queue_message = queue_outbound_message

    def get_outbound_message_by_idempotency_key(
        self, idempotency_key: str
    ) -> dict[str, Any] | None:
        """Return the durable outbox row for an idempotent notification."""

        with self._read() as connection:
            return _row(
                connection.execute(
                    "SELECT * FROM outbound_messages WHERE idempotency_key = ?",
                    (str(idempotency_key),),
                ).fetchone()
            )

    def claim_outbound_messages(
        self,
        *,
        limit: int = 100,
        now: datetime | str | None = None,
    ) -> list[dict[str, Any]]:
        stamp = _timestamp(now)
        stale_before = _timestamp(_parse_timestamp(stamp) - timedelta(minutes=5))
        with self._transaction() as connection:
            connection.execute(
                """
                UPDATE outbound_messages
                SET status = 'queued', error_text = 'recovered stale send claim',
                    updated_at = ?
                WHERE status = 'sending' AND updated_at <= ?
                """,
                (stamp, stale_before),
            )
            items = connection.execute(
                """
                SELECT * FROM outbound_messages
                WHERE status = 'queued' AND scheduled_at <= ?
                ORDER BY scheduled_at, id LIMIT ?
                """,
                (stamp, max(1, min(int(limit), 1_000))),
            ).fetchall()
            ids = [item["id"] for item in items]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                connection.execute(
                    f"UPDATE outbound_messages SET status = 'sending', updated_at = ? WHERE id IN ({placeholders})",
                    (stamp, *ids),
                )
            return [dict(item) | {"status": "sending"} for item in items]

    def claim_outbound_message(
        self,
        message_id: int,
        *,
        now: datetime | str | None = None,
    ) -> dict[str, Any] | None:
        """Atomically claim one queued notification for immediate delivery."""

        stamp = _timestamp(now)
        with self._transaction() as connection:
            changed = connection.execute(
                """
                UPDATE outbound_messages SET status = 'sending', updated_at = ?
                WHERE id = ? AND status = 'queued' AND scheduled_at <= ?
                """,
                (stamp, int(message_id), stamp),
            ).rowcount
            if changed != 1:
                return None
            return dict(
                self._required(
                    connection,
                    "SELECT * FROM outbound_messages WHERE id = ?",
                    (int(message_id),),
                    "outbound message",
                )
            )

    def mark_outbound_message(
        self,
        message_id: int,
        status: str | None = None,
        *,
        success: bool | None = None,
        telegram_message_id: int | None = None,
        error_text: str | None = None,
        error: str | None = None,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        if status is None and success is not None:
            status = "sent" if success else "failed"
        if status is None:
            raise ValidationError("outbound message status is required")
        if status not in {"sent", "failed", "cancelled", "queued"}:
            raise ValidationError("unsupported outbound message status")
        if error_text is None:
            error_text = error
        stamp = _timestamp(now)
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE outbound_messages
                SET status = ?, telegram_message_id = COALESCE(?, telegram_message_id),
                    error_text = ?, sent_at = CASE WHEN ? = 'sent' THEN ? ELSE sent_at END,
                    updated_at = ? WHERE id = ?
                """,
                (status, telegram_message_id, error_text, status, stamp, stamp, message_id),
            )
            if cursor.rowcount != 1:
                raise NotFoundError("outbound message not found")
            return dict(
                self._required(connection, "SELECT * FROM outbound_messages WHERE id = ?", (message_id,), "message")
            )

    def schedule_outbound_retry(
        self,
        message_id: int,
        error: str,
        *,
        permanent: bool = False,
        max_attempts: int = 12,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        """Retry transient sends with bounded exponential backoff."""

        if max_attempts < 1:
            raise ValidationError("max attempts must be positive")
        stamp = _timestamp(now)
        current = _parse_timestamp(stamp)
        safe_error = str(error)[:1_000]
        with self._transaction() as connection:
            message = self._required(
                connection,
                "SELECT * FROM outbound_messages WHERE id = ?",
                (int(message_id),),
                "outbound message",
            )
            if message["status"] != "sending":
                raise ConflictError(
                    "only a currently claimed outbound message can be retried"
                )
            previous = connection.execute(
                "SELECT attempt_count FROM outbound_message_attempts WHERE message_id = ?",
                (int(message_id),),
            ).fetchone()
            attempt_count = int(previous["attempt_count"] if previous else 0) + 1
            connection.execute(
                """
                INSERT INTO outbound_message_attempts(
                    message_id, attempt_count, last_attempt_at, last_error
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(message_id) DO UPDATE SET
                    attempt_count = excluded.attempt_count,
                    last_attempt_at = excluded.last_attempt_at,
                    last_error = excluded.last_error
                """,
                (int(message_id), attempt_count, stamp, safe_error),
            )
            terminal = bool(permanent) or attempt_count >= int(max_attempts)
            retry_at = _timestamp(
                current + timedelta(seconds=min(1_800, 5 * (2 ** (attempt_count - 1))))
            )
            connection.execute(
                """
                UPDATE outbound_messages
                SET status = ?, scheduled_at = CASE WHEN ? THEN scheduled_at ELSE ? END,
                    error_text = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    "failed" if terminal else "queued",
                    int(terminal),
                    retry_at,
                    safe_error,
                    stamp,
                    int(message_id),
                ),
            )
            result = dict(
                self._required(
                    connection,
                    "SELECT * FROM outbound_messages WHERE id = ?",
                    (int(message_id),),
                    "outbound message",
                )
            )
            result["attempt_count"] = attempt_count
            return result

    def create_broadcast_batch(
        self,
        batch_id: str,
        *,
        actor_admin_id: int,
        actor_user_id: int,
        target_count: int,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        value = str(batch_id).strip()
        if not value:
            raise ValidationError("broadcast batch id is required")
        if int(target_count) < 0:
            raise ValidationError("broadcast target count cannot be negative")
        stamp = _timestamp(now)
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM broadcast_batches WHERE id = ?", (value,)
            ).fetchone()
            if existing:
                if (
                    int(existing["actor_admin_id"]) != int(actor_admin_id)
                    or int(existing["actor_user_id"]) != int(actor_user_id)
                    or int(existing["target_count"]) != int(target_count)
                ):
                    raise ConflictError("broadcast batch id belongs to another request")
                return dict(existing)
            self._required(
                connection,
                "SELECT id FROM admins WHERE id = ?",
                (int(actor_admin_id),),
                "admin",
            )
            self._required(
                connection,
                "SELECT id FROM users WHERE id = ?",
                (int(actor_user_id),),
                "user",
            )
            connection.execute(
                """
                INSERT INTO broadcast_batches(
                    id, actor_admin_id, actor_user_id, target_count, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (value, int(actor_admin_id), int(actor_user_id), int(target_count), stamp),
            )
            return dict(
                self._required(
                    connection,
                    "SELECT * FROM broadcast_batches WHERE id = ?",
                    (value,),
                    "broadcast batch",
                )
            )

    def link_broadcast_message(self, batch_id: str, message_id: int) -> None:
        with self._transaction() as connection:
            self._required(
                connection,
                "SELECT id FROM broadcast_batches WHERE id = ?",
                (str(batch_id),),
                "broadcast batch",
            )
            self._required(
                connection,
                "SELECT id FROM outbound_messages WHERE id = ?",
                (int(message_id),),
                "outbound message",
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO broadcast_batch_messages(batch_id, message_id)
                VALUES (?, ?)
                """,
                (str(batch_id), int(message_id)),
            )

    def queue_broadcast_batch(
        self,
        batch_id: str,
        *,
        actor_admin_id: int,
        actor_user_id: int,
        recipient_user_ids: Sequence[int],
        body: str,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        """Atomically create an idempotent broadcast and all recipient messages."""

        value = str(batch_id).strip()
        clean_body = str(body).strip()
        if not value:
            raise ValidationError("broadcast batch id is required")
        if not clean_body:
            raise ValidationError("message body cannot be empty")
        recipients = list(dict.fromkeys(int(item) for item in recipient_user_ids))
        stamp = _timestamp(now)
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM broadcast_batches WHERE id = ?", (value,)
            ).fetchone()
            if existing:
                if (
                    int(existing["actor_admin_id"]) != int(actor_admin_id)
                    or int(existing["actor_user_id"]) != int(actor_user_id)
                ):
                    raise ConflictError("broadcast batch id belongs to another request")
                linked = connection.execute(
                    """
                    SELECT COUNT(*) AS count, MIN(message_id) AS first_message_id
                    FROM broadcast_batch_messages WHERE batch_id = ?
                    """,
                    (value,),
                ).fetchone()
                if int(linked["count"] or 0) != int(existing["target_count"]):
                    raise ConflictError("broadcast batch is incomplete")
                return {
                    **dict(existing),
                    "first_message_id": linked["first_message_id"],
                    "queued_count": int(existing["target_count"]),
                }

            self._required(
                connection,
                "SELECT id FROM admins WHERE id = ?",
                (int(actor_admin_id),),
                "admin",
            )
            self._required(
                connection,
                "SELECT id FROM users WHERE id = ?",
                (int(actor_user_id),),
                "user",
            )
            if recipients:
                placeholders = ",".join("?" for _ in recipients)
                found = connection.execute(
                    f"SELECT COUNT(*) AS count FROM users WHERE id IN ({placeholders})",
                    recipients,
                ).fetchone()
                if int(found["count"] or 0) != len(recipients):
                    raise NotFoundError("one or more broadcast recipients were not found")
            connection.execute(
                """
                INSERT INTO broadcast_batches(
                    id, actor_admin_id, actor_user_id, target_count, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (value, int(actor_admin_id), int(actor_user_id), len(recipients), stamp),
            )
            first_message_id: int | None = None
            for recipient_user_id in recipients:
                cursor = connection.execute(
                    """
                    INSERT INTO outbound_messages(
                        idempotency_key, recipient_user_id, body,
                        scheduled_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"{value}:{recipient_user_id}",
                        recipient_user_id,
                        clean_body,
                        stamp,
                        stamp,
                        stamp,
                    ),
                )
                message_id = int(cursor.lastrowid)
                if first_message_id is None:
                    first_message_id = message_id
                connection.execute(
                    """
                    INSERT INTO broadcast_batch_messages(batch_id, message_id)
                    VALUES (?, ?)
                    """,
                    (value, message_id),
                )
            return {
                "id": value,
                "actor_admin_id": int(actor_admin_id),
                "actor_user_id": int(actor_user_id),
                "target_count": len(recipients),
                "created_at": stamp,
                "completed_at": None,
                "notified_at": None,
                "first_message_id": first_message_id,
                "queued_count": len(recipients),
            }

    def list_ready_broadcast_summaries(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """Return completed batches whose actual result was not yet reported."""

        with self._read() as connection:
            return _rows(
                connection.execute(
                    """
                    SELECT b.*,
                           summary.status AS summary_status,
                           COUNT(bm.message_id) AS linked_count,
                           COALESCE(SUM(CASE WHEN om.status = 'sent' THEN 1 ELSE 0 END), 0)
                               AS sent_count,
                           COALESCE(SUM(CASE WHEN om.status IN ('failed', 'cancelled') THEN 1 ELSE 0 END), 0)
                               AS failed_count
                    FROM broadcast_batches b
                    LEFT JOIN broadcast_batch_messages bm ON bm.batch_id = b.id
                    LEFT JOIN outbound_messages om ON om.id = bm.message_id
                    LEFT JOIN outbound_messages summary
                      ON summary.idempotency_key = 'broadcast:' || b.id || ':summary'
                    WHERE b.notified_at IS NULL
                      AND (
                          summary.id IS NULL
                          OR summary.status IN ('sent', 'failed', 'cancelled')
                      )
                    GROUP BY b.id
                    HAVING COUNT(bm.message_id) = b.target_count
                       AND COALESCE(SUM(CASE WHEN om.status IN ('queued', 'sending') THEN 1 ELSE 0 END), 0) = 0
                    ORDER BY b.created_at
                    LIMIT ?
                    """,
                    (max(1, min(int(limit), 500)),),
                ).fetchall()
            )

    def mark_broadcast_notified(
        self,
        batch_id: str,
        *,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        stamp = _timestamp(now)
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE broadcast_batches
                SET completed_at = COALESCE(completed_at, ?), notified_at = ?
                WHERE id = ?
                """,
                (stamp, stamp, str(batch_id)),
            )
            if cursor.rowcount != 1:
                raise NotFoundError("broadcast batch not found")
            return dict(
                self._required(
                    connection,
                    "SELECT * FROM broadcast_batches WHERE id = ?",
                    (str(batch_id),),
                    "broadcast batch",
                )
            )

    # -- Referrals and reward rules -----------------------------------------

    def record_referral(
        self,
        inviter_user_id: int,
        invitee_user_id: int,
        *,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        if inviter_user_id == invitee_user_id:
            raise ValidationError("a user cannot refer themselves")
        stamp = _timestamp(now)
        with self._transaction() as connection:
            self._required(connection, "SELECT id FROM users WHERE id = ?", (inviter_user_id,), "inviter")
            self._required(connection, "SELECT id FROM users WHERE id = ?", (invitee_user_id,), "invitee")
            existing = connection.execute(
                "SELECT * FROM referrals WHERE invitee_user_id = ?",
                (invitee_user_id,),
            ).fetchone()
            if existing:
                if existing["inviter_user_id"] != inviter_user_id:
                    raise ConflictError("invitee already belongs to another inviter")
                return dict(existing)
            cursor = connection.execute(
                """
                INSERT INTO referrals(inviter_user_id, invitee_user_id, created_at)
                VALUES (?, ?, ?)
                """,
                (inviter_user_id, invitee_user_id, stamp),
            )
            return dict(self._required(connection, "SELECT * FROM referrals WHERE id = ?", (cursor.lastrowid,), "referral"))

    def get_referral_by_invitee(self, invitee_user_id: int) -> dict[str, Any] | None:
        with self._read() as connection:
            return _row(
                connection.execute(
                    "SELECT * FROM referrals WHERE invitee_user_id = ?",
                    (int(invitee_user_id),),
                ).fetchone()
            )

    def create_reward_rule(
        self,
        rule_key: str,
        *,
        event_type: str,
        amount: int,
        product_id: int | None = None,
        conditions: Mapping[str, Any] | None = None,
        starts_at: datetime | str | None = None,
        ends_at: datetime | str | None = None,
        active: bool = True,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        if event_type not in {"start", "first_purchase", "product_purchase", "combined"}:
            raise ValidationError("unsupported reward event type")
        if event_type == "start" and product_id is not None:
            raise ValidationError("start rewards cannot be product-specific")
        if amount <= 0:
            raise ValidationError("reward amount must be positive")
        normalized_conditions = self._validate_reward_conditions(event_type, conditions)
        key = rule_key.strip()
        if not key:
            raise ValidationError("reward rule key cannot be empty")
        stamp = _timestamp(now)
        start_value = _timestamp(starts_at) if starts_at is not None else None
        end_value = _timestamp(ends_at) if ends_at is not None else None
        if start_value is not None and end_value is not None and end_value <= start_value:
            raise ValidationError("reward end must be later than reward start")
        with self._transaction() as connection:
            if product_id is not None:
                self._required(
                    connection,
                    "SELECT id FROM products WHERE id = ?",
                    (int(product_id),),
                    "product",
                )
            product_ids = normalized_conditions.get("product_ids", [])
            if product_ids:
                placeholders = ",".join("?" for _ in product_ids)
                existing_count = int(
                    connection.execute(
                        f"SELECT COUNT(*) FROM products WHERE id IN ({placeholders})",
                        tuple(product_ids),
                    ).fetchone()[0]
                )
                if existing_count != len(product_ids):
                    raise ValidationError("one or more reward product_ids do not exist")
                if product_id is not None and int(product_id) not in product_ids:
                    raise ValidationError(
                        "reward product_id conflicts with conditions product_ids"
                    )
            existing = connection.execute("SELECT * FROM reward_rules WHERE rule_key = ?", (key,)).fetchone()
            if existing:
                if (
                    existing["event_type"] != event_type
                    or existing["product_id"] != product_id
                    or int(existing["amount"]) != int(amount)
                    or existing["conditions_json"] != _json_dump(normalized_conditions)
                    or existing["starts_at"] != start_value
                    or existing["ends_at"] != end_value
                ):
                    raise ConflictError(
                        "reward rule key already exists with different terms"
                    )
                return dict(existing)
            cursor = connection.execute(
                """
                INSERT INTO reward_rules(
                    rule_key, event_type, product_id, amount, conditions_json,
                    starts_at, ends_at, is_active, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key,
                    event_type,
                    product_id,
                    int(amount),
                    _json_dump(normalized_conditions),
                    start_value,
                    end_value,
                    int(bool(active)),
                    stamp,
                    stamp,
                ),
            )
            return dict(self._required(connection, "SELECT * FROM reward_rules WHERE id = ?", (cursor.lastrowid,), "reward rule"))

    def list_reward_rules(
        self, *, active_only: bool = False, limit: int = 200, offset: int = 0
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM reward_rules"
        if active_only:
            query += " WHERE is_active = 1"
        query += " ORDER BY id DESC LIMIT ? OFFSET ?"
        with self._read() as connection:
            return _rows(
                connection.execute(
                    query, (max(1, min(int(limit), 1_000)), max(0, int(offset)))
                ).fetchall()
            )

    def set_reward_rule_active(
        self,
        rule_id: int,
        active: bool | None = None,
        *,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        stamp = _timestamp(now)
        with self._transaction() as connection:
            rule = self._required(
                connection,
                "SELECT * FROM reward_rules WHERE id = ?",
                (int(rule_id),),
                "reward rule",
            )
            next_active = not bool(rule["is_active"]) if active is None else bool(active)
            connection.execute(
                "UPDATE reward_rules SET is_active = ?, updated_at = ? WHERE id = ?",
                (int(next_active), stamp, int(rule_id)),
            )
            return dict(
                self._required(
                    connection,
                    "SELECT * FROM reward_rules WHERE id = ?",
                    (int(rule_id),),
                    "reward rule",
                )
            )

    @staticmethod
    def _validate_reward_conditions(
        event_type: str,
        conditions: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        raw = dict(conditions or {})
        if event_type != "combined":
            if raw:
                raise ValidationError("conditions are supported only for combined rewards")
            return {}
        if not raw:
            raise ValidationError("combined rewards require at least one condition")

        allowed = {
            "minimum_successful_purchases",
            "first_purchase",
            "minimum_referrals",
            "minimum_qualified_referrals",
            "product_ids",
            "minimum_order_amount",
        }
        unknown = sorted(set(raw) - allowed)
        if unknown:
            raise ValidationError(
                "unsupported combined reward condition: " + ", ".join(unknown)
            )

        normalized: dict[str, Any] = {}
        effective = False
        integer_fields = (
            "minimum_successful_purchases",
            "minimum_referrals",
            "minimum_qualified_referrals",
            "minimum_order_amount",
        )
        for field in integer_fields:
            if field not in raw:
                continue
            value = raw[field]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValidationError(f"{field} must be a non-negative integer")
            normalized[field] = value
            effective = effective or value > 0

        if "first_purchase" in raw:
            if not isinstance(raw["first_purchase"], bool):
                raise ValidationError("first_purchase must be a boolean")
            normalized["first_purchase"] = raw["first_purchase"]
            effective = effective or raw["first_purchase"]

        if "product_ids" in raw:
            values = raw["product_ids"]
            if not isinstance(values, list) or not values:
                raise ValidationError("product_ids must be a non-empty list")
            if any(
                isinstance(value, bool) or not isinstance(value, int) or value <= 0
                for value in values
            ):
                raise ValidationError("product_ids must contain positive integers")
            normalized["product_ids"] = list(dict.fromkeys(values))
            effective = True

        if normalized.get("first_purchase") and normalized.get(
            "minimum_successful_purchases", 0
        ) > 1:
            raise ValidationError(
                "first_purchase conflicts with minimum_successful_purchases greater than one"
            )
        if not effective:
            raise ValidationError("combined reward conditions cannot all be disabled or zero")
        return normalized

    def grant_referral_reward(
        self,
        invitee_user_id: int,
        event_type: str,
        event_key: str,
        *,
        product_id: int | None = None,
        source_order_id: int | None = None,
        now: datetime | str | None = None,
    ) -> list[dict[str, Any]]:
        """Credit all matching rules once for a stable caller-provided event key."""

        if not event_key.strip():
            raise ValidationError("event_key is required")
        stamp = _timestamp(now)
        with self._transaction() as connection:
            referral = connection.execute(
                "SELECT * FROM referrals WHERE invitee_user_id = ?",
                (invitee_user_id,),
            ).fetchone()
            # Most customers arrive without a referral link. Reward processing
            # is therefore a normal no-op, not an exceptional condition.
            if referral is None:
                return []
            rules = connection.execute(
                """
                SELECT * FROM reward_rules
                WHERE event_type = ? AND is_active = 1
                  AND (product_id IS NULL OR product_id = ?)
                  AND created_at <= ?
                  AND (starts_at IS NULL OR starts_at <= ?)
                  AND (ends_at IS NULL OR ends_at > ?)
                ORDER BY id
                """,
                (event_type, product_id, stamp, stamp, stamp),
            ).fetchall()
            granted: list[dict[str, Any]] = []
            for rule in rules:
                if rule["event_type"] == "combined" and not self._combined_reward_matches(
                    connection,
                    rule,
                    invitee_user_id=int(invitee_user_id),
                    product_id=product_id,
                    source_order_id=source_order_id,
                ):
                    continue
                prior = connection.execute(
                    """
                    SELECT re.* FROM reward_events re
                    WHERE reward_rule_id = ? AND referral_id = ? AND event_key = ?
                    """,
                    (rule["id"], referral["id"], event_key),
                ).fetchone()
                if prior:
                    prior_result = dict(prior)
                    prior_result["user_id"] = referral["inviter_user_id"]
                    prior_result["is_new"] = False
                    granted.append(prior_result)
                    continue
                wallet_key = f"reward:{rule['id']}:{referral['id']}:{event_key}"
                wallet_cursor = connection.execute(
                    """
                    INSERT INTO wallet_entries(
                        user_id, order_id, amount_signed, entry_type, reason,
                        idempotency_key, created_at
                    ) VALUES (?, ?, ?, 'referral_reward', ?, ?, ?)
                    """,
                    (
                        referral["inviter_user_id"],
                        source_order_id,
                        rule["amount"],
                        f"Referral reward: {rule['rule_key']}",
                        wallet_key,
                        stamp,
                    ),
                )
                reward_cursor = connection.execute(
                    """
                    INSERT INTO reward_events(
                        reward_rule_id, referral_id, event_key, source_order_id,
                        amount, wallet_entry_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        rule["id"],
                        referral["id"],
                        event_key,
                        source_order_id,
                        rule["amount"],
                        wallet_cursor.lastrowid,
                        stamp,
                    ),
                )
                reward_result = dict(
                    self._required(
                        connection,
                        "SELECT * FROM reward_events WHERE id = ?",
                        (reward_cursor.lastrowid,),
                        "reward event",
                    )
                )
                reward_result["user_id"] = referral["inviter_user_id"]
                reward_result["is_new"] = True
                granted.append(reward_result)
            if granted:
                connection.execute(
                    """
                    UPDATE referrals SET status = 'qualified', qualified_at = COALESCE(qualified_at, ?)
                    WHERE id = ?
                    """,
                    (stamp, referral["id"]),
                )
            return granted

    @staticmethod
    def _combined_reward_matches(
        connection: sqlite3.Connection,
        rule: sqlite3.Row,
        *,
        invitee_user_id: int,
        product_id: int | None,
        source_order_id: int | None,
    ) -> bool:
        """Require every configured condition of a combined reward."""

        try:
            conditions = _json_load(rule["conditions_json"], {})
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
        if not isinstance(conditions, Mapping) or not conditions:
            return False
        source_order = None
        source_event_at: str | None = None
        if source_order_id is not None:
            source_order = connection.execute(
                """
                SELECT *, COALESCE(paid_at, created_at) AS reward_event_at
                FROM orders WHERE id = ?
                """,
                (int(source_order_id),),
            ).fetchone()
            if source_order is None or int(source_order["user_id"]) != int(
                invitee_user_id
            ) or source_order["order_origin"] != "customer" or int(
                source_order["subtotal_amount"]
            ) <= 0:
                return False
            source_event_at = str(source_order["reward_event_at"])
        successful = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM orders
                WHERE user_id = ? AND status IN (
                    'paid','awaiting_stock','awaiting_info','processing','completed'
                )
                  AND subtotal_amount > 0
                  AND order_origin = 'customer'
                  AND (
                      ? IS NULL
                      OR COALESCE(paid_at, created_at) < ?
                      OR (COALESCE(paid_at, created_at) = ? AND id <= ?)
                  )
                """,
                (
                    invitee_user_id,
                    source_event_at,
                    source_event_at,
                    source_event_at,
                    source_order_id,
                ),
            ).fetchone()[0]
        )
        minimum_purchases = int(conditions.get("minimum_successful_purchases", 0) or 0)
        if successful < minimum_purchases:
            return False
        if bool(conditions.get("first_purchase")) and successful != 1:
            return False
        minimum_referrals = int(conditions.get("minimum_referrals", 0) or 0)
        if minimum_referrals:
            referrals = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM referrals
                    WHERE inviter_user_id = ?
                      AND (? IS NULL OR created_at <= ?)
                    """,
                    (invitee_user_id, source_event_at, source_event_at),
                ).fetchone()[0]
            )
            if referrals < minimum_referrals:
                return False
        minimum_qualified = int(
            conditions.get("minimum_qualified_referrals", 0) or 0
        )
        if minimum_qualified:
            qualified = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM referrals
                    WHERE inviter_user_id = ? AND status = 'qualified'
                      AND (? IS NULL OR qualified_at <= ?)
                    """,
                    (invitee_user_id, source_event_at, source_event_at),
                ).fetchone()[0]
            )
            if qualified < minimum_qualified:
                return False
        product_ids = conditions.get("product_ids")
        if product_ids is not None:
            if not isinstance(product_ids, list):
                return False
            if product_id is None or int(product_id) not in {
                int(value) for value in product_ids
            }:
                return False
        minimum_amount = int(conditions.get("minimum_order_amount", 0) or 0)
        if minimum_amount:
            if source_order is None:
                return False
            if int(source_order["subtotal_amount"]) - int(
                source_order["discount_amount"]
            ) < minimum_amount:
                return False
        return True

    def list_user_referrals(
        self,
        user_id: int,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List one inviter's invitees with their aggregate reward result."""

        bounded = max(1, min(int(limit), 1_000))
        start = max(0, int(offset))
        with self._read() as connection:
            self._required(
                connection, "SELECT id FROM users WHERE id = ?", (user_id,), "user"
            )
            return _rows(
                connection.execute(
                    """
                    SELECT r.*,
                           u.chat_id AS invitee_chat_id,
                           u.username AS invitee_username,
                           u.first_name AS invitee_first_name,
                           u.last_name AS invitee_last_name,
                           COUNT(re.id) AS reward_count,
                           COALESCE(SUM(re.amount), 0) AS reward_total
                    FROM referrals r
                    JOIN users u ON u.id = r.invitee_user_id
                    LEFT JOIN reward_events re ON re.referral_id = r.id
                    WHERE r.inviter_user_id = ?
                    GROUP BY r.id
                    ORDER BY r.id DESC
                    LIMIT ? OFFSET ?
                    """,
                    (int(user_id), bounded, start),
                ).fetchall()
            )

    def count_user_referrals(self, user_id: int) -> int:
        with self._read() as connection:
            self._required(
                connection, "SELECT id FROM users WHERE id = ?", (user_id,), "user"
            )
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM referrals WHERE inviter_user_id = ?",
                    (int(user_id),),
                ).fetchone()[0]
            )

    def list_user_reward_events(
        self,
        user_id: int,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List detailed referral rewards credited to one inviter."""

        bounded = max(1, min(int(limit), 1_000))
        start = max(0, int(offset))
        with self._read() as connection:
            self._required(
                connection, "SELECT id FROM users WHERE id = ?", (user_id,), "user"
            )
            return _rows(
                connection.execute(
                    """
                    SELECT re.*,
                           rr.rule_key,
                           rr.event_type,
                           r.invitee_user_id,
                           u.chat_id AS invitee_chat_id,
                           u.username AS invitee_username,
                           o.order_number
                    FROM reward_events re
                    JOIN reward_rules rr ON rr.id = re.reward_rule_id
                    JOIN referrals r ON r.id = re.referral_id
                    JOIN users u ON u.id = r.invitee_user_id
                    LEFT JOIN orders o ON o.id = re.source_order_id
                    WHERE r.inviter_user_id = ?
                    ORDER BY re.id DESC
                    LIMIT ? OFFSET ?
                    """,
                    (int(user_id), bounded, start),
                ).fetchall()
            )

    def count_user_reward_events(self, user_id: int) -> int:
        with self._read() as connection:
            self._required(
                connection, "SELECT id FROM users WHERE id = ?", (user_id,), "user"
            )
            return int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM reward_events re
                    JOIN referrals r ON r.id = re.referral_id
                    WHERE r.inviter_user_id = ?
                    """,
                    (int(user_id),),
                ).fetchone()[0]
            )

    def referral_summary(self, user_id: int) -> dict[str, Any]:
        with self._read() as connection:
            self._required(connection, "SELECT id FROM users WHERE id = ?", (user_id,), "user")
            stats = connection.execute(
                """
                SELECT COUNT(*) AS invited_count,
                       COALESCE(SUM(CASE WHEN status = 'qualified' THEN 1 ELSE 0 END), 0)
                           AS qualified_count
                FROM referrals WHERE inviter_user_id = ?
                """,
                (user_id,),
            ).fetchone()
            rewards = connection.execute(
                """
                SELECT COALESCE(SUM(re.amount), 0)
                FROM reward_events re
                JOIN referrals r ON r.id = re.referral_id
                WHERE r.inviter_user_id = ?
                """,
                (user_id,),
            ).fetchone()[0]
            inviter = connection.execute(
                """
                SELECT u.* FROM referrals r JOIN users u ON u.id = r.inviter_user_id
                WHERE r.invitee_user_id = ?
                """,
                (user_id,),
            ).fetchone()
            return {
                **dict(stats),
                "reward_total": int(rewards),
                "invited": int(stats["invited_count"]),
                "rewards": int(rewards),
                "inviter": _row(inviter),
            }

    def grant_start_rewards(
        self,
        referral_id: int,
        *,
        now: datetime | str | None = None,
    ) -> list[dict[str, Any]]:
        with self._read() as connection:
            referral = self._required(connection, "SELECT * FROM referrals WHERE id = ?", (referral_id,), "referral")
        return self.grant_referral_reward(
            referral["invitee_user_id"],
            "start",
            f"referral:{referral_id}:start",
            now=now,
        )

    def list_reward_events_missing_notice(
        self,
        *,
        limit: int = 100,
        after_id: int = 0,
    ) -> list[dict[str, Any]]:
        """Return reward credits whose stable recipient notice is absent."""

        with self._read() as connection:
            return _rows(
                connection.execute(
                    """
                    SELECT re.*, r.inviter_user_id AS user_id
                    FROM reward_events re
                    JOIN referrals r ON r.id = re.referral_id
                    WHERE re.id > ?
                      AND NOT EXISTS (
                          SELECT 1 FROM outbound_messages om
                          WHERE om.idempotency_key =
                                'reward:' || re.id || ':notice'
                      )
                    ORDER BY re.id
                    LIMIT ?
                    """,
                    (
                        max(0, int(after_id)),
                        max(1, min(int(limit), 5_000)),
                    ),
                ).fetchall()
            )

    def grant_purchase_rewards(
        self,
        order_id: int,
        *,
        now: datetime | str | None = None,
    ) -> list[dict[str, Any]]:
        with self._read() as connection:
            order = self._required(connection, "SELECT * FROM orders WHERE id = ?", (order_id,), "order")
            if order["status"] not in {
                "paid",
                "awaiting_stock",
                "awaiting_info",
                "processing",
                "completed",
            }:
                raise ValidationError("purchase rewards require a successfully paid order")
            if order["order_origin"] != "customer" or int(
                order["subtotal_amount"]
            ) <= 0:
                return []
            first_successful = connection.execute(
                """
                SELECT id FROM orders
                WHERE user_id = ? AND status IN (
                    'paid','awaiting_stock','awaiting_info','processing','completed'
                )
                  AND subtotal_amount > 0
                  AND order_origin = 'customer'
                ORDER BY COALESCE(paid_at, created_at), id
                LIMIT 1
                """,
                (order["user_id"],),
            ).fetchone()
            # Purchase rewards belong to the purchase instant, not to a later
            # maintenance/recovery run. This also prevents newly-created rules
            # from being applied retroactively to historical orders.
            event_at = order["paid_at"] or order["created_at"]
        rewards = self.grant_referral_reward(
            order["user_id"],
            "product_purchase",
            f"order:{order_id}:product-purchase",
            product_id=order["product_id"],
            source_order_id=order_id,
            now=event_at,
        )
        if first_successful is not None and int(first_successful["id"]) == int(order_id):
            rewards.extend(
                self.grant_referral_reward(
                    order["user_id"],
                    "first_purchase",
                    f"order:{order_id}:first-purchase",
                    product_id=order["product_id"],
                    source_order_id=order_id,
                    now=event_at,
                )
            )
        rewards.extend(
            self.grant_referral_reward(
                order["user_id"],
                "combined",
                f"order:{order_id}:combined",
                product_id=order["product_id"],
                source_order_id=order_id,
                now=event_at,
            )
        )
        return rewards

    def mark_order_rewards_processed(
        self, order_id: int, *, now: datetime | str | None = None
    ) -> dict[str, Any]:
        """Mark rewards complete only after their durable notices are queued."""

        stamp = _timestamp(now)
        with self._transaction() as connection:
            order = self._required(
                connection,
                "SELECT * FROM orders WHERE id = ?",
                (int(order_id),),
                "order",
            )
            if order["status"] not in {
                "paid",
                "awaiting_stock",
                "awaiting_info",
                "processing",
                "completed",
            }:
                raise ValidationError("cannot complete rewards for an unpaid order")
            connection.execute(
                """
                UPDATE orders
                SET reward_processed_at = COALESCE(reward_processed_at, ?),
                    updated_at = ?
                WHERE id = ?
                """,
                (stamp, stamp, int(order_id)),
            )
            return dict(
                self._required(
                    connection,
                    "SELECT * FROM orders WHERE id = ?",
                    (int(order_id),),
                    "order",
                )
            )

    # -- Subscription reminders --------------------------------------------

    @staticmethod
    def _normalize_reminder_days(days: Sequence[int]) -> list[int]:
        if any(type(day) is not int or day < 0 for day in days):
            raise ValidationError("reminder days must be non-negative integers")
        return sorted(set(days), reverse=True)

    def _schedule_order_reminders(
        self,
        connection: sqlite3.Connection,
        order_id: int,
        stamp: str,
        days_before: Sequence[int] | None = None,
    ) -> list[int]:
        order = self._required(
            connection,
            """
            SELECT o.*, p.reminder_days_json FROM orders o
            JOIN products p ON p.id = o.product_id WHERE o.id = ?
            """,
            (order_id,),
            "order",
        )
        if not order["subscription_ends_at"]:
            return []
        selected = list(days_before) if days_before is not None else _json_load(order["reminder_days_json"], [7, 3, 1])
        end_at = _parse_timestamp(order["subscription_ends_at"])
        current = _parse_timestamp(stamp)
        ids: list[int] = []
        for day in self._normalize_reminder_days(selected):
            if day == 0:
                # "Same day" means the beginning of the local expiry date,
                # not the expiry instant. If scheduled during that date it is
                # due now, but an already expired subscription is never sent.
                day_start = end_at.astimezone(self.reminder_timezone).replace(
                    hour=0, minute=0, second=0, microsecond=0,
                ).astimezone(timezone.utc)
                remind_at = max(day_start, current)
                if remind_at >= end_at:
                    continue
            else:
                remind_at = end_at - timedelta(days=day)
                if remind_at <= current:
                    continue
            connection.execute(
                """
                INSERT OR IGNORE INTO reminders(
                    order_id, user_id, remind_at, days_before,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (order_id, order["user_id"], _timestamp(remind_at), day, stamp, stamp),
            )
            reminder = connection.execute(
                "SELECT id FROM reminders WHERE order_id = ? AND days_before = ?",
                (order_id, day),
            ).fetchone()
            ids.append(int(reminder["id"]))
        return ids

    def schedule_order_reminders(
        self,
        order_id: int,
        *,
        days_before: Sequence[int] | None = None,
        now: datetime | str | None = None,
    ) -> list[dict[str, Any]]:
        if days_before is not None:
            self._normalize_reminder_days(days_before)
        stamp = _timestamp(now)
        with self._transaction() as connection:
            ids = self._schedule_order_reminders(connection, order_id, stamp, days_before)
            if not ids:
                return []
            placeholders = ",".join("?" for _ in ids)
            return _rows(
                connection.execute(
                    f"SELECT * FROM reminders WHERE id IN ({placeholders}) ORDER BY remind_at",
                    ids,
                ).fetchall()
            )

    def get_reminder(self, reminder_id: int) -> dict[str, Any] | None:
        with self._read() as connection:
            return _row(connection.execute(
                "SELECT * FROM reminders WHERE id = ?", (int(reminder_id),),
            ).fetchone())

    def claim_due_reminders(
        self,
        *,
        limit: int = 100,
        now: datetime | str | None = None,
    ) -> list[dict[str, Any]]:
        stamp = _timestamp(now)
        stale_before = _timestamp(_parse_timestamp(stamp) - timedelta(minutes=5))
        with self._transaction() as connection:
            connection.execute(
                """
                UPDATE reminders
                SET status = 'pending', error_text = 'recovered stale delivery claim',
                    updated_at = ?
                WHERE status = 'processing' AND updated_at <= ?
                """,
                (stamp, stale_before),
            )
            items = connection.execute(
                """
                SELECT * FROM reminders
                WHERE status = 'pending' AND remind_at <= ?
                ORDER BY remind_at, id LIMIT ?
                """,
                (stamp, max(1, min(int(limit), 1_000))),
            ).fetchall()
            ids = [item["id"] for item in items]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                connection.execute(
                    f"UPDATE reminders SET status = 'processing', updated_at = ? WHERE id IN ({placeholders})",
                    (stamp, *ids),
                )
            return [dict(item) | {"status": "processing"} for item in items]

    def mark_reminder(
        self,
        reminder_id: int,
        status: str,
        *,
        telegram_message_id: int | None = None,
        error_text: str | None = None,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        if status not in {"sent", "failed", "cancelled", "pending"}:
            raise ValidationError("unsupported reminder status")
        stamp = _timestamp(now)
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE reminders
                SET status = ?, telegram_message_id = COALESCE(?, telegram_message_id),
                    error_text = ?, sent_at = CASE WHEN ? = 'sent' THEN ? ELSE sent_at END,
                    updated_at = ? WHERE id = ?
                """,
                (status, telegram_message_id, error_text, status, stamp, stamp, reminder_id),
            )
            if cursor.rowcount != 1:
                raise NotFoundError("reminder not found")
            return dict(self._required(connection, "SELECT * FROM reminders WHERE id = ?", (reminder_id,), "reminder"))

    def mark_reminder_sent(
        self,
        reminder_id: int,
        telegram_message_id: int | None = None,
        *,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        return self.mark_reminder(
            reminder_id,
            "sent",
            telegram_message_id=telegram_message_id,
            now=now,
        )

    def mark_reminder_failed(
        self,
        reminder_id: int,
        error: str,
        *,
        permanent: bool = False,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        return self.mark_reminder(
            reminder_id,
            "failed" if permanent else "pending",
            error_text=error,
            now=now,
        )

    def release_reminder_for_retry(
        self,
        reminder_id: int,
        error: str,
        *,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        """Return a transient delivery failure to the durable retry path."""

        return self.mark_reminder(
            reminder_id,
            "pending",
            error_text=str(error)[:1_000],
            now=now,
        )

    # -- Backups and summary reporting --------------------------------------

    def create_backup(
        self,
        destination: str | Path,
        *,
        overwrite: bool = False,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        destination_path = Path(destination).expanduser().resolve()
        directory_destination = destination_path.is_dir() or (
            not destination_path.exists() and destination_path.suffix == ""
        )
        if directory_destination:
            _ensure_backup_directory(destination_path, restrict_destination=True)
            label = _parse_timestamp(_timestamp(now)).strftime("%Y%m%dT%H%M%SZ")
            destination_path = destination_path / f"bot-backup-{label}-{uuid.uuid4().hex[:6]}.sqlite3"
        if destination_path == self.path:
            raise ValidationError("backup destination must differ from the live database")
        if destination_path.exists() and not overwrite:
            raise ConflictError("backup destination already exists")
        _ensure_backup_directory(
            destination_path.parent,
            restrict_destination=False,
        )
        stamp = _timestamp(now)
        with self._transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO backups(path, status, created_at) VALUES (?, 'running', ?)",
                (str(destination_path), stamp),
            )
            backup_id = int(cursor.lastrowid)
        try:
            _prepare_backup_file(destination_path, overwrite=overwrite)
            source: sqlite3.Connection | None = None
            target: sqlite3.Connection | None = None
            try:
                source = self._connect()
                target = sqlite3.connect(destination_path)
                source.backup(target)
            finally:
                if target is not None:
                    target.close()
                if source is not None:
                    source.close()
                if os.name == "posix" and destination_path.exists():
                    destination_path.chmod(0o600)
            digest = hashlib.sha256(destination_path.read_bytes()).hexdigest()
            size_bytes = destination_path.stat().st_size
            with self._transaction() as connection:
                connection.execute(
                    """
                    UPDATE backups
                    SET status = 'completed', sha256 = ?, size_bytes = ?, completed_at = ?
                    WHERE id = ?
                    """,
                    (digest, size_bytes, _timestamp(), backup_id),
                )
                return dict(self._required(connection, "SELECT * FROM backups WHERE id = ?", (backup_id,), "backup"))
        except Exception as error:
            with self._transaction() as connection:
                connection.execute(
                    """
                    UPDATE backups
                    SET status = 'failed', error_text = ?, completed_at = ?
                    WHERE id = ?
                    """,
                    (str(error)[:1_000], _timestamp(), backup_id),
                )
            raise

    def list_backups(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._read() as connection:
            return _rows(
                connection.execute(
                    "SELECT * FROM backups ORDER BY id DESC LIMIT ?",
                    (max(1, min(int(limit), 1_000)),),
                ).fetchall()
            )

    def summary_report(
        self,
        *,
        start: datetime | str | None = None,
        end: datetime | str | None = None,
    ) -> dict[str, Any]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if start is not None:
            clauses.append("created_at >= ?")
            parameters.append(_timestamp(start))
        if end is not None:
            clauses.append("created_at < ?")
            parameters.append(_timestamp(end))
        order_where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        user_clauses = [clause.replace("created_at", "joined_at") for clause in clauses]
        user_where = f" WHERE {' AND '.join(user_clauses)}" if user_clauses else ""
        with self._read() as connection:
            orders = connection.execute(
                f"""
                SELECT COUNT(*) AS order_count,
                       COALESCE(SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END), 0)
                           AS completed_order_count,
                       COALESCE(SUM(CASE WHEN status IN (
                           'paid','awaiting_stock','awaiting_info','processing','completed'
                       )
                                         AND order_origin = 'customer'
                                         AND subtotal_amount > 0
                                         THEN subtotal_amount - discount_amount ELSE 0 END), 0)
                           AS gross_revenue,
                       COALESCE(SUM(CASE
                           WHEN order_origin = 'customer' AND subtotal_amount > 0
                           THEN external_paid_amount ELSE 0 END), 0) AS external_paid,
                       COALESCE(SUM(CASE WHEN order_origin = 'customer'
                                                  AND subtotal_amount > 0
                                                  AND wallet_captured_amount > 0 THEN MAX(
                           0,
                           wallet_captured_amount - COALESCE((
                               SELECT SUM(we.amount_signed)
                               FROM wallet_entries we
                               WHERE we.order_id = orders.id
                                 AND we.entry_type = 'order_refund'
                           ), 0)
                       ) ELSE 0 END), 0)
                           AS net_wallet_paid
                FROM orders{order_where}
                """,
                parameters,
            ).fetchone()
            users = connection.execute(
                f"SELECT COUNT(*) FROM users{user_where}", parameters
            ).fetchone()[0]
            active_prefix = " WHERE " if not user_clauses else " AND "
            active_users = connection.execute(
                f"SELECT COUNT(*) FROM users{user_where}{active_prefix}is_blocked = 0",
                parameters,
            ).fetchone()[0]
            wallet_total = connection.execute("SELECT COALESCE(SUM(amount_signed), 0) FROM wallet_entries").fetchone()[0]
            open_tickets = connection.execute("SELECT COUNT(*) FROM tickets WHERE status <> 'closed'").fetchone()[0]
            pending_orders = connection.execute(
                """
                SELECT COUNT(*) FROM orders
                WHERE status IN ('pending_payment','awaiting_confirmation','awaiting_stock','awaiting_info')
                """
            ).fetchone()[0]
            return {
                **dict(orders),
                "user_count": int(users),
                "active_user_count": int(active_users),
                "wallet_ledger_total": int(wallet_total),
                "open_ticket_count": int(open_tickets),
                "pending_order_count": int(pending_orders),
            }
