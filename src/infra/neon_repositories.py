from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Sequence, Tuple

from psycopg import errors, sql

from ..domain.models import ContextMessage, StoredMessage
from ..domain.ports import MessageRepositoryPort
from .neon_client import NeonClient

BOT_JOIN_MARKER = "__bot_join__"
GROUP_LANG_MARKER = "__group_lang__"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _MessageRow:
    sender_name: str
    text: str
    timestamp: datetime


class NeonMessageRepository(MessageRepositoryPort):
    """Neon(PostgreSQL) への永続化を担うリポジトリ実装。"""

    def __init__(self, client: NeonClient, max_group_languages: int = 5) -> None:
        self._client = client
        self._max_group_languages = max_group_languages

    def ensure_group_member(self, group_id: str, user_id: str) -> None:
        query = sql.SQL(
            """
            INSERT INTO group_members (group_id, user_id)
            VALUES (%s, %s)
            ON CONFLICT (group_id, user_id)
            DO UPDATE SET joined_at = NOW()
            """
        )
        with self._client.cursor() as cur:
            cur.execute(query, (group_id, user_id))

    def fetch_group_languages(self, group_id: str) -> List[str]:
        query = sql.SQL(
            """
            SELECT lang_code
            FROM group_languages
            WHERE group_id = %s
            ORDER BY lang_code
            """
        )
        with self._client.cursor() as cur:
            cur.execute(query, (group_id,))
            rows = cur.fetchall()
        return [row[0] for row in rows]

    def fetch_recent_messages(self, group_id: str, limit: int) -> List[ContextMessage]:
        query = sql.SQL(
            """
            SELECT sender_name, text, timestamp
            FROM messages
            WHERE group_id = %s
            ORDER BY timestamp DESC
            LIMIT %s
            """
        )
        with self._client.cursor() as cur:
            cur.execute(query, (group_id, limit))
            rows = cur.fetchall()
        messages = [
            ContextMessage(sender_name=row[0], text=row[1], timestamp=row[2]) for row in rows
        ]
        messages.reverse()
        return messages

    def insert_message(self, message: StoredMessage) -> None:
        query = sql.SQL(
            """
            INSERT INTO messages (group_id, user_id, sender_name, text, timestamp)
            VALUES (%s, %s, %s, %s, %s)
            """
        )
        ts = message.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        with self._client.cursor() as cur:
            cur.execute(
                query,
                (message.group_id, message.user_id, message.sender_name, message.text, ts),
            )

    def record_language_prompt(self, group_id: str) -> None:
        query = sql.SQL(
            """
            INSERT INTO group_members (group_id, user_id, last_prompted_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (group_id, user_id)
            DO UPDATE SET last_prompted_at = NOW(), last_completed_at = NULL
            """
        )
        with self._client.cursor() as cur:
            cur.execute(query, (group_id, GROUP_LANG_MARKER))

    def try_complete_group_languages(
        self,
        group_id: str,
        languages: Sequence[Tuple[str, str]],
    ) -> bool:
        normalized = self._normalize_languages(languages)
        limited = normalized[: self._max_group_languages]
        with self._client.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO group_members (group_id, user_id)
                    VALUES (%s, %s)
                    ON CONFLICT (group_id, user_id) DO NOTHING
                    """,
                    (group_id, GROUP_LANG_MARKER),
                )
                cur.execute(
                    """
                    SELECT last_completed_at
                    FROM group_members
                    WHERE group_id = %s AND user_id = %s
                    FOR UPDATE
                    """,
                    (group_id, GROUP_LANG_MARKER),
                )
                row = cur.fetchone()
                if row and row[0]:
                    return False

                cur.execute("DELETE FROM group_languages WHERE group_id = %s", (group_id,))
                if limited:
                    cur.executemany(
                        """
                        INSERT INTO group_languages (group_id, lang_code, lang_name)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (group_id, lang_code) DO UPDATE SET lang_name = EXCLUDED.lang_name
                        """,
                        [(group_id, code, name) for code, name in limited],
                    )

                cur.execute(
                    """
                    UPDATE group_members
                    SET last_completed_at = NOW()
                    WHERE group_id = %s AND user_id = %s
                    """,
                    (group_id, GROUP_LANG_MARKER),
                )
        return True

    def try_cancel_language_prompt(self, group_id: str) -> bool:
        with self._client.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO group_members (group_id, user_id)
                    VALUES (%s, %s)
                    ON CONFLICT (group_id, user_id) DO NOTHING
                    """,
                    (group_id, GROUP_LANG_MARKER),
                )
                cur.execute(
                    """
                    SELECT last_completed_at
                    FROM group_members
                    WHERE group_id = %s AND user_id = %s
                    FOR UPDATE
                    """,
                    (group_id, GROUP_LANG_MARKER),
                )
                row = cur.fetchone()
                if row and row[0]:
                    return False
                cur.execute(
                    """
                    UPDATE group_members
                    SET last_completed_at = NOW()
                    WHERE group_id = %s AND user_id = %s
                    """,
                    (group_id, GROUP_LANG_MARKER),
                )
        return True

    def add_group_languages(self, group_id: str, languages: Sequence[Tuple[str, str]]) -> None:
        if not languages:
            return
        existing = self.fetch_group_languages(group_id)
        remaining = max(self._max_group_languages - len(existing), 0)
        normalized = self._normalize_languages(languages, existing)
        limited = normalized[:remaining]
        if not limited:
            logger.info(
                "Language add skipped due to limit",
                extra={"group_id": group_id, "requested": [code for code, _ in languages]},
            )
            return
        with self._client.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO group_languages (group_id, lang_code, lang_name)
                VALUES (%s, %s, %s)
                ON CONFLICT (group_id, lang_code) DO UPDATE SET lang_name = EXCLUDED.lang_name
                """,
                [(group_id, code, name) for code, name in limited],
            )

    def remove_group_languages(self, group_id: str, lang_codes: Sequence[str]) -> None:
        if not lang_codes:
            return
        with self._client.cursor() as cur:
            cur.execute(
                "DELETE FROM group_languages WHERE group_id = %s AND lang_code = ANY(%s)",
                (group_id, list({code.lower() for code in lang_codes})),
            )

    def set_translation_enabled(self, group_id: str, enabled: bool) -> None:
        try:
            with self._client.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO group_settings (group_id, translation_enabled)
                    VALUES (%s, %s)
                    ON CONFLICT (group_id)
                    DO UPDATE SET translation_enabled = EXCLUDED.translation_enabled, updated_at = NOW()
                    """,
                    (group_id, enabled),
            )
        except errors.UndefinedTable:
            # 後方互換: group_settings が未作成でも致命的エラーにしない
            logger.warning(
                "group_settings table missing; skip persisting translation_enabled",
                extra={"group_id": group_id},
            )
            return

    # === Stripe usage/subscription helpers ===
    def increment_usage(self, group_id: str, period_key: str, increment: int = 1) -> int:
        query = sql.SQL(
            """
            INSERT INTO group_usage_counters (group_id, period_key, translation_count, created_at, updated_at)
            VALUES (%s, %s, %s, NOW(), NOW())
            ON CONFLICT (group_id, period_key)
            DO UPDATE SET translation_count = group_usage_counters.translation_count + EXCLUDED.translation_count,
                          updated_at = NOW()
            RETURNING translation_count
            """
        )
        try:
            with self._client.cursor() as cur:
                cur.execute(query, (group_id, period_key, increment))
                row = cur.fetchone()
                return int(row[0]) if row else 0
        except errors.UndefinedTable:
            logger.warning("group_usage_counters table missing; usage not tracked", extra={"group_id": group_id})
            return 0

    def get_usage(self, group_id: str, period_key: str) -> int:
        try:
            with self._client.cursor() as cur:
                cur.execute(
                    "SELECT translation_count FROM group_usage_counters WHERE group_id = %s AND period_key = %s",
                    (group_id, period_key),
                )
                row = cur.fetchone()
                return int(row[0]) if row else 0
        except errors.UndefinedTable:
            logger.warning("group_usage_counters table missing; usage not tracked", extra={"group_id": group_id})
            return 0

    def get_limit_notice_plan(self, group_id: str, period_key: str) -> Optional[str]:
        try:
            with self._client.cursor() as cur:
                cur.execute(
                    "SELECT limit_notice_plan FROM group_usage_counters WHERE group_id = %s AND period_key = %s",
                    (group_id, period_key),
                )
                row = cur.fetchone()
                return row[0] if row else None
        except errors.UndefinedColumn:
            logger.warning("limit_notice_plan column missing; treating as no notice", extra={"group_id": group_id})
            return None
        except errors.UndefinedTable:
            logger.warning("group_usage_counters table missing; treating as no notice", extra={"group_id": group_id})
            return None

    def set_limit_notice_plan(self, group_id: str, period_key: str, plan: str) -> None:
        try:
            with self._client.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO group_usage_counters (group_id, period_key, translation_count, limit_notice_plan, created_at, updated_at)
                    VALUES (%s, %s, 0, %s, NOW(), NOW())
                    ON CONFLICT (group_id, period_key)
                    DO UPDATE SET limit_notice_plan = EXCLUDED.limit_notice_plan, updated_at = NOW()
                    """,
                    (group_id, period_key, plan),
                )
        except errors.UndefinedColumn:
            logger.warning("limit_notice_plan column missing; skip setting notice plan", extra={"group_id": group_id})
        except errors.UndefinedTable:
            logger.warning("group_usage_counters table missing; skip setting notice plan", extra={"group_id": group_id})

    def get_subscription_status(self, group_id: str) -> Optional[str]:
        try:
            with self._client.cursor() as cur:
                cur.execute(
                    "SELECT status FROM group_subscriptions WHERE group_id = %s",
                    (group_id,),
                )
                row = cur.fetchone()
                return row[0] if row else None
        except errors.UndefinedTable:
            logger.warning("group_subscriptions table missing; subscription not tracked", extra={"group_id": group_id})
            return None

    def get_subscription_period(
        self, group_id: str
    ) -> Tuple[Optional[str], Optional[datetime], Optional[datetime]]:
        """サブスクのステータスと当期の開始/終了を取得する。未課金の場合は (None, None, None)。"""
        try:
            with self._client.cursor() as cur:
                cur.execute(
                    """
                    SELECT status, current_period_start, current_period_end
                    FROM group_subscriptions
                    WHERE group_id = %s
                    """,
                    (group_id,),
                )
                row = cur.fetchone()
                if not row:
                    return (None, None, None)
                status, period_start, period_end = row
                return (status, period_start, period_end)
        except errors.UndefinedColumn:
            # current_period_start 未導入環境の後方互換
            try:
                with self._client.cursor() as cur:
                    cur.execute(
                        "SELECT status, NULL::timestamptz AS period_start, current_period_end FROM group_subscriptions WHERE group_id = %s",
                        (group_id,),
                    )
                    row = cur.fetchone()
                    if not row:
                        return (None, None, None)
                    status, period_start, period_end = row
                    return (status, period_start, period_end)
            except Exception:  # pylint: disable=broad-except
                logger.warning(
                    "group_subscriptions table missing columns; subscription period unavailable", extra={"group_id": group_id}
                )
                return (None, None, None)
        except errors.UndefinedTable:
            logger.warning("group_subscriptions table missing; subscription not tracked", extra={"group_id": group_id})
            return (None, None, None)

    def upsert_subscription(
        self,
        group_id: str,
        stripe_customer_id: str,
        stripe_subscription_id: str,
        status: str,
        current_period_start: Optional[datetime],
        current_period_end: Optional[datetime],
    ) -> None:
        try:
            with self._client.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO group_subscriptions (
                        group_id, stripe_customer_id, stripe_subscription_id, status, current_period_start, current_period_end, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW())
                    ON CONFLICT (group_id)
                    DO UPDATE SET
                        stripe_customer_id = EXCLUDED.stripe_customer_id,
                        stripe_subscription_id = EXCLUDED.stripe_subscription_id,
                        status = EXCLUDED.status,
                        current_period_start = EXCLUDED.current_period_start,
                        current_period_end = EXCLUDED.current_period_end,
                        updated_at = NOW()
                    """,
                    (
                        group_id,
                        stripe_customer_id,
                        stripe_subscription_id,
                        status,
                        current_period_start,
                        current_period_end,
                    ),
                )
        except errors.UndefinedTable:
            logger.warning("group_subscriptions table missing; cannot upsert", extra={"group_id": group_id})

    def update_subscription_status(
        self, group_id: str, status: str, current_period_end: Optional[datetime]
    ) -> None:
        try:
            with self._client.cursor() as cur:
                cur.execute(
                    """
                    UPDATE group_subscriptions
                    SET status = %s, current_period_end = %s, updated_at = NOW()
                    WHERE group_id = %s
                    """,
                    (status, current_period_end, group_id),
                )
        except errors.UndefinedTable:
            logger.warning("group_subscriptions table missing; cannot update status", extra={"group_id": group_id})
        except Exception:
            logger.exception("Failed to set translation_enabled", extra={"group_id": group_id})
            raise

    def is_translation_enabled(self, group_id: str) -> bool:
        try:
            with self._client.cursor() as cur:
                cur.execute(
                    "SELECT translation_enabled FROM group_settings WHERE group_id = %s",
                    (group_id,),
                )
                row = cur.fetchone()
            if row is None:
                return True
            return bool(row[0])
        except errors.UndefinedTable:
            logger.warning(
                "group_settings table missing; defaulting translation_enabled=True",
                extra={"group_id": group_id},
            )
            return True
        except Exception:
            logger.exception("Failed to fetch translation_enabled", extra={"group_id": group_id})
            raise

    def reset_group_language_settings(self, group_id: str) -> None:
        with self._client.cursor() as cur:
            cur.execute("DELETE FROM group_languages WHERE group_id = %s", (group_id,))
            cur.execute(
                "UPDATE group_members SET last_prompted_at = NULL, last_completed_at = NULL WHERE group_id = %s",
                (group_id,),
            )

    def _normalize_languages(
        self, languages: Sequence[Tuple[str, str]], existing: Optional[Sequence[str]] = None
    ) -> List[Tuple[str, str]]:
        """Lowercase + 重複除去 + 空コード除去。既存言語を除外するオプション付き。"""
        existing_set = {code.lower() for code in existing or [] if code}
        seen = set(existing_set)
        normalized: List[Tuple[str, str]] = []
        for code, name in languages:
            lowered = (code or "").lower()
            if not lowered or lowered in seen:
                continue
            seen.add(lowered)
            normalized.append((lowered, name))
        return normalized

    def record_bot_joined_at(self, group_id: str, joined_at: datetime) -> None:
        ts = joined_at
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        query = sql.SQL(
            """
            INSERT INTO group_members (group_id, user_id, joined_at)
            VALUES (%s, %s, %s)
            ON CONFLICT (group_id, user_id)
            DO UPDATE SET joined_at = EXCLUDED.joined_at
            """
        )
        with self._client.cursor() as cur:
            cur.execute(query, (group_id, BOT_JOIN_MARKER, ts))

    def fetch_bot_joined_at(self, group_id: str) -> Optional[datetime]:
        with self._client.cursor() as cur:
            cur.execute(
                "SELECT joined_at FROM group_members WHERE group_id = %s AND user_id = %s",
                (group_id, BOT_JOIN_MARKER),
            )
            row = cur.fetchone()
        return row[0] if row else None
