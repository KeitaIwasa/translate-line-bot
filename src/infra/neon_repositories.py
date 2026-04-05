from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Sequence, Tuple

from psycopg import errors, sql

from ..domain.models import ContextMessage, ConversationMessage, StoredMessage, TranslationRuntimeState
from ..domain.ports import MessageRepositoryPort
from ..domain.services.plan_policy import FREE_PLAN, PRO_PLAN, resolve_effective_plan
from ..domain.services.quota_service import QuotaDecision
from .message_crypto import decrypt_text, encrypt_text
from .neon_client import NeonClient

BOT_JOIN_MARKER = "__bot_join__"
GROUP_LANG_MARKER = "__group_lang__"
PRIVATE_ASSISTANT_MARKER = "__assistant__"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _MessageRow:
    sender_name: str
    text: str
    timestamp: datetime


class NeonMessageRepository(MessageRepositoryPort):
    """Neon(PostgreSQL) への永続化を担うリポジトリ実装。"""

    def __init__(
        self,
        client: NeonClient,
        max_group_languages: int = 5,
        message_encryption_key: str = "",
    ) -> None:
        self._client = client
        self._max_group_languages = max_group_languages
        self._message_encryption_key = (message_encryption_key or "").strip()
        self._message_encryption_version = "v1"

    def ensure_group_member(self, group_id: str, user_id: str) -> None:
        query = sql.SQL(
            """
            INSERT INTO group_members (group_id, user_id, display_name, display_name_updated_at)
            VALUES (%s, %s, NULL, NULL)
            ON CONFLICT (group_id, user_id)
            DO NOTHING
            """
        )
        with self._client.cursor() as cur:
            cur.execute(query, (group_id, user_id))

    def get_group_member_display_name(self, group_id: str, user_id: str) -> Optional[str]:
        with self._client.cursor() as cur:
            cur.execute(
                """
                SELECT display_name
                FROM group_members
                WHERE group_id = %s AND user_id = %s
                """,
                (group_id, user_id),
            )
            row = cur.fetchone()
        if not row:
            return None
        return (row[0] or "").strip() or None

    def is_group_member(self, group_id: str, user_id: str) -> bool:
        if not group_id or not user_id:
            return False
        with self._client.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                FROM group_members
                WHERE group_id = %s AND user_id = %s
                """,
                (group_id, user_id),
            )
            return cur.fetchone() is not None

    def upsert_group_member_display_name(self, group_id: str, user_id: str, display_name: str) -> None:
        normalized = (display_name or "").strip()
        if not normalized:
            return
        with self._client.cursor() as cur:
            cur.execute(
                """
                INSERT INTO group_members (group_id, user_id, display_name, display_name_updated_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (group_id, user_id)
                DO UPDATE SET
                    display_name = EXCLUDED.display_name,
                    display_name_updated_at = NOW(),
                    joined_at = group_members.joined_at
                """,
                (group_id, user_id, normalized),
            )

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
        rows = []
        try:
            query = sql.SQL(
                """
                SELECT sender_name, text, timestamp, is_encrypted, encrypted_body
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
                ContextMessage(
                    sender_name=row[0],
                    text=self._restore_message_text(row[1], bool(row[3]), row[4]),
                    timestamp=row[2],
                )
                for row in rows
            ]
        except errors.UndefinedColumn:
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
            messages = [ContextMessage(sender_name=row[0], text=row[1], timestamp=row[2]) for row in rows]
        messages.reverse()
        return messages

    def fetch_private_conversation(self, user_id: str, limit: int) -> List[ConversationMessage]:
        rows = []
        try:
            query = sql.SQL(
                """
                SELECT sender_name, text, timestamp, COALESCE(message_role, 'user'), is_encrypted, encrypted_body
                FROM messages
                WHERE group_id = %s
                ORDER BY timestamp DESC
                LIMIT %s
                """
            )
            with self._client.cursor() as cur:
                cur.execute(query, (user_id, limit))
                rows = cur.fetchall()
            history = [
                ConversationMessage(
                    role=(row[3] or "user"),
                    sender_name=row[0] or "",
                    text=self._restore_message_text(
                        row[1] or "",
                        bool(row[4]) if len(row) > 4 else False,
                        row[5] if len(row) > 5 else None,
                    ),
                    timestamp=row[2],
                )
                for row in rows
            ]
        except errors.UndefinedColumn:
            query = sql.SQL(
                """
                SELECT sender_name, text, timestamp, COALESCE(message_role, 'user')
                FROM messages
                WHERE group_id = %s
                ORDER BY timestamp DESC
                LIMIT %s
                """
            )
            with self._client.cursor() as cur:
                cur.execute(query, (user_id, limit))
                rows = cur.fetchall()
            history = [
                ConversationMessage(
                    role=(row[3] or "user"),
                    sender_name=row[0] or "",
                    text=row[1] or "",
                    timestamp=row[2],
                )
                for row in rows
            ]
        history.reverse()
        return history

    def insert_message(self, message: StoredMessage) -> None:
        text_to_store = message.text
        encrypted_body = message.encrypted_body
        is_encrypted = bool(message.is_encrypted)
        encryption_version = message.encryption_version
        if not is_encrypted and self._should_encrypt_group_message(message.group_id):
            try:
                encrypted_body = encrypt_text(message.text, key_secret=self._message_encryption_key)
                text_to_store = "[encrypted]"
                is_encrypted = True
                encryption_version = self._message_encryption_version
            except Exception:
                logger.warning("Failed to encrypt message; fallback to plain text", exc_info=True)
                encrypted_body = None
                is_encrypted = False
                encryption_version = None

        ts = message.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        try:
            query = sql.SQL(
                """
                INSERT INTO messages (
                    group_id, user_id, sender_name, text, timestamp, is_encrypted, encrypted_body, encryption_version, message_role
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """
            )
            with self._client.cursor() as cur:
                cur.execute(
                    query,
                    (
                        message.group_id,
                        message.user_id,
                        message.sender_name,
                        text_to_store,
                        ts,
                        is_encrypted,
                        encrypted_body,
                        encryption_version,
                        message.message_role or "user",
                    ),
                )
        except errors.UndefinedColumn:
            # 後方互換: 暗号化カラム未導入環境では従来挙動で保存
            query = sql.SQL(
                """
                INSERT INTO messages (group_id, user_id, sender_name, text, timestamp, message_role)
                VALUES (%s, %s, %s, %s, %s, %s)
                """
            )
            with self._client.cursor() as cur:
                cur.execute(
                    query,
                    (
                        message.group_id,
                        message.user_id,
                        message.sender_name,
                        message.text,
                        ts,
                        message.message_role or "user",
                    ),
                )

    def record_language_prompt(self, group_id: str) -> None:
        query = sql.SQL(
            """
            INSERT INTO group_members (group_id, user_id, display_name, display_name_updated_at, last_prompted_at)
            VALUES (%s, %s, %s, NOW(), NOW())
            ON CONFLICT (group_id, user_id)
            DO UPDATE SET last_prompted_at = NOW(), last_completed_at = NULL
            """
        )
        with self._client.cursor() as cur:
            cur.execute(query, (group_id, GROUP_LANG_MARKER, GROUP_LANG_MARKER))

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
                    INSERT INTO group_members (group_id, user_id, display_name, display_name_updated_at)
                    VALUES (%s, %s, %s, NOW())
                    ON CONFLICT (group_id, user_id) DO NOTHING
                    """,
                    (group_id, GROUP_LANG_MARKER, GROUP_LANG_MARKER),
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
                    INSERT INTO group_members (group_id, user_id, display_name, display_name_updated_at)
                    VALUES (%s, %s, %s, NOW())
                    ON CONFLICT (group_id, user_id) DO NOTHING
                    """,
                    (group_id, GROUP_LANG_MARKER, GROUP_LANG_MARKER),
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

    def shrink_group_languages(self, group_id: str, keep_limit: int) -> List[str]:
        """古い登録順で言語を絞り込む。削除した言語コードを返す。"""
        if keep_limit < 0:
            keep_limit = 0
        with self._client.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT lang_code
                    FROM group_languages
                    WHERE group_id = %s
                    ORDER BY created_at ASC, lang_code ASC
                    """,
                    (group_id,),
                )
                rows = cur.fetchall()
                all_codes = [row[0] for row in rows if row and row[0]]
                if len(all_codes) <= keep_limit:
                    return []

                remove_codes = all_codes[keep_limit:]
                cur.execute(
                    "DELETE FROM group_languages WHERE group_id = %s AND lang_code = ANY(%s)",
                    (group_id, remove_codes),
                )
                return remove_codes

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

    def upsert_group_name(self, group_id: str, group_name: str) -> None:
        """グループ名を保存する。既存の translation_enabled 値は維持する。"""
        if not group_name:
            return
        try:
            with self._client.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO group_settings (group_id, translation_enabled, group_name, updated_at)
                    VALUES (%s, TRUE, %s, NOW())
                    ON CONFLICT (group_id)
                    DO UPDATE SET group_name = EXCLUDED.group_name, updated_at = NOW()
                    """,
                    (group_id, group_name),
                )
        except errors.UndefinedColumn:
            logger.warning("group_name column missing; skip persisting group name", extra={"group_id": group_id})
        except errors.UndefinedTable:
            logger.warning("group_settings table missing; skip persisting group name", extra={"group_id": group_id})

    def get_total_distinct_users(self) -> int:
        """実ユーザーの累計人数を取得する。"""
        query = sql.SQL(
            """
            SELECT COUNT(DISTINCT user_id)::bigint
            FROM group_members
            WHERE user_id NOT IN (%s, %s)
            """
        )
        with self._client.cursor() as cur:
            cur.execute(query, (BOT_JOIN_MARKER, GROUP_LANG_MARKER))
            row = cur.fetchone()
        return int(row[0]) if row else 0

    def increment_contact_rate_limit(
        self,
        ip_hash: str,
        window_start: datetime,
        prune_before: Optional[datetime] = None,
    ) -> int:
        if window_start.tzinfo is None:
            window_start = window_start.replace(tzinfo=timezone.utc)
        if prune_before and prune_before.tzinfo is None:
            prune_before = prune_before.replace(tzinfo=timezone.utc)

        try:
            with self._client.connection() as conn:
                with conn.cursor() as cur:
                    if prune_before:
                        cur.execute(
                            "DELETE FROM contact_rate_limits WHERE updated_at < %s",
                            (prune_before,),
                        )
                    cur.execute(
                        """
                        INSERT INTO contact_rate_limits (ip_hash, window_start, count, updated_at)
                        VALUES (%s, %s, 1, NOW())
                        ON CONFLICT (ip_hash, window_start)
                        DO UPDATE SET count = contact_rate_limits.count + 1, updated_at = NOW()
                        RETURNING count
                        """,
                        (ip_hash, window_start),
                    )
                    row = cur.fetchone()
            return int(row[0]) if row else 0
        except errors.UndefinedTable:
            logger.warning("contact_rate_limits table missing; skip contact rate limit", extra={"ip_hash": ip_hash})
            return 0

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

    def reserve_quota_slot(
        self,
        *,
        group_id: str,
        period_key: str,
        plan_key: str,
        stop_translation_on_limit: bool,
        limit: int,
        increment: int,
    ) -> QuotaDecision:
        with self._client.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT translation_count, limit_notice_plan
                    FROM group_usage_counters
                    WHERE group_id = %s AND period_key = %s
                    FOR UPDATE
                    """,
                    (group_id, period_key),
                )
                row = cur.fetchone()
                current_usage = int(row[0]) if row else 0
                notice_plan = row[1] if row else None

                if current_usage >= limit:
                    return QuotaDecision(
                        allowed=False,
                        should_notify=notice_plan != plan_key,
                        stop_translation=stop_translation_on_limit,
                        usage=current_usage,
                        limit=limit,
                        period_key=period_key,
                        plan_key=plan_key,
                    )

                cur.execute(
                    """
                    INSERT INTO group_usage_counters (group_id, period_key, translation_count, created_at, updated_at)
                    VALUES (%s, %s, %s, NOW(), NOW())
                    ON CONFLICT (group_id, period_key)
                    DO UPDATE SET
                        translation_count = group_usage_counters.translation_count + EXCLUDED.translation_count,
                        updated_at = NOW()
                    RETURNING translation_count
                    """,
                    (group_id, period_key, increment),
                )
                usage_after_row = cur.fetchone()
                usage_after = int(usage_after_row[0]) if usage_after_row else current_usage

        if usage_after > limit:
            return QuotaDecision(
                False,
                notice_plan != plan_key,
                stop_translation_on_limit,
                usage_after,
                limit,
                period_key,
                plan_key,
            )
        if usage_after == limit:
            return QuotaDecision(True, notice_plan != plan_key, False, usage_after, limit, period_key, plan_key)
        return QuotaDecision(True, False, False, usage_after, limit, period_key, plan_key)

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

    def reset_limit_notice_plan(self, group_id: str) -> None:
        """退会時などに上限通知フラグをリセットする。"""
        try:
            with self._client.cursor() as cur:
                cur.execute(
                    """
                    UPDATE group_usage_counters
                    SET limit_notice_plan = NULL, updated_at = NOW()
                    WHERE group_id = %s
                    """,
                    (group_id,),
                )
        except errors.UndefinedColumn:
            logger.warning("limit_notice_plan column missing; skip resetting notice plan", extra={"group_id": group_id})
        except errors.UndefinedTable:
            logger.warning("group_usage_counters table missing; skip resetting notice plan", extra={"group_id": group_id})

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

    def get_subscription_plan(
        self, group_id: str
    ) -> Tuple[
        Optional[str],
        str,
        str,
        bool,
        Optional[str],
        Optional[datetime],
        Optional[datetime],
        Optional[int],
        Optional[str],
        Optional[datetime],
    ]:
        """購読状態とプラン情報を取得する。"""
        try:
            with self._client.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        status,
                        COALESCE(entitlement_plan, 'free'),
                        COALESCE(billing_interval, 'month'),
                        COALESCE(is_grandfathered, FALSE),
                        stripe_price_id,
                        current_period_start,
                        current_period_end,
                        quota_anchor_day,
                        scheduled_target_price_id,
                        scheduled_effective_at
                    FROM group_subscriptions
                    WHERE group_id = %s
                    """,
                    (group_id,),
                )
                row = cur.fetchone()
                if not row:
                    return (None, FREE_PLAN, "month", False, None, None, None, None, None, None)
                return (
                    row[0],
                    (row[1] or FREE_PLAN),
                    (row[2] or "month"),
                    bool(row[3]),
                    row[4],
                    row[5],
                    row[6],
                    row[7],
                    row[8],
                    row[9],
                )
        except errors.UndefinedColumn:
            status, period_start, period_end = self.get_subscription_period(group_id)
            legacy_plan = PRO_PLAN if status in {"active", "trialing"} else FREE_PLAN
            legacy_interval = "legacy_month" if legacy_plan == PRO_PLAN else "month"
            legacy_grandfathered = legacy_plan == PRO_PLAN
            return (
                status,
                legacy_plan,
                legacy_interval,
                legacy_grandfathered,
                None,
                period_start,
                period_end,
                period_start.day if period_start else None,
                None,
                None,
            )
        except errors.UndefinedTable:
            return (None, FREE_PLAN, "month", False, None, None, None, None, None, None)

    def get_subscription_detail(self, group_id: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """Stripe 顧客/サブスク ID とステータスを取得する。"""
        try:
            with self._client.cursor() as cur:
                cur.execute(
                    """
                    SELECT stripe_customer_id, stripe_subscription_id, status
                    FROM group_subscriptions
                    WHERE group_id = %s
                    """,
                    (group_id,),
                )
                row = cur.fetchone()
                if not row:
                    return (None, None, None)
                return row[0], row[1], row[2]
        except errors.UndefinedTable:
            logger.warning("group_subscriptions table missing; subscription detail unavailable", extra={"group_id": group_id})
            return (None, None, None)

    def get_billing_owner_user_id(self, group_id: str) -> Optional[str]:
        try:
            with self._client.cursor() as cur:
                cur.execute(
                    """
                    SELECT billing_owner_user_id
                    FROM group_subscriptions
                    WHERE group_id = %s
                    """,
                    (group_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return (row[0] or "").strip() or None
        except errors.UndefinedColumn:
            return None
        except errors.UndefinedTable:
            logger.warning("group_subscriptions table missing; billing owner unavailable", extra={"group_id": group_id})
            return None

    def get_billing_owner_claim_state(
        self, group_id: str
    ) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[datetime], Optional[datetime]]:
        try:
            with self._client.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        billing_owner_user_id,
                        pending_billing_owner_user_id,
                        pending_billing_owner_subscription_id,
                        pending_billing_owner_expires_at,
                        updated_at
                    FROM group_subscriptions
                    WHERE group_id = %s
                    """,
                    (group_id,),
                )
                row = cur.fetchone()
                if not row:
                    return (None, None, None, None, None)
                return (
                    (row[0] or "").strip() or None,
                    (row[1] or "").strip() or None,
                    (row[2] or "").strip() or None,
                    row[3],
                    row[4],
                )
        except errors.UndefinedColumn:
            return (self.get_billing_owner_user_id(group_id), None, None, None, None)
        except errors.UndefinedTable:
            logger.warning("group_subscriptions table missing; billing owner claim state unavailable", extra={"group_id": group_id})
            return (None, None, None, None, None)

    def set_billing_owner_user_id(self, group_id: str, user_id: str) -> None:
        if not group_id or not user_id:
            return
        try:
            with self._client.cursor() as cur:
                cur.execute(
                    """
                    UPDATE group_subscriptions
                    SET billing_owner_user_id = %s, updated_at = NOW()
                    WHERE group_id = %s
                    """,
                    (user_id, group_id),
                )
        except errors.UndefinedColumn:
            logger.warning("billing_owner_user_id column missing; cannot update owner", extra={"group_id": group_id})
        except errors.UndefinedTable:
            logger.warning("group_subscriptions table missing; cannot update owner", extra={"group_id": group_id})

    def set_pending_billing_owner_claim(self, group_id: str, user_id: str, subscription_id: str, expires_at: datetime) -> None:
        if not group_id or not user_id or not subscription_id or not expires_at:
            return
        try:
            with self._client.cursor() as cur:
                cur.execute(
                    """
                    UPDATE group_subscriptions
                    SET
                        pending_billing_owner_user_id = %s,
                        pending_billing_owner_subscription_id = %s,
                        pending_billing_owner_expires_at = %s,
                        updated_at = NOW()
                    WHERE group_id = %s
                    """,
                    (user_id, subscription_id, expires_at, group_id),
                )
        except errors.UndefinedColumn:
            logger.warning("pending billing owner columns missing; cannot set claim", extra={"group_id": group_id})
        except errors.UndefinedTable:
            logger.warning("group_subscriptions table missing; cannot set pending claim", extra={"group_id": group_id})

    def clear_pending_billing_owner_claim(self, group_id: str) -> None:
        if not group_id:
            return
        try:
            with self._client.cursor() as cur:
                cur.execute(
                    """
                    UPDATE group_subscriptions
                    SET
                        pending_billing_owner_user_id = NULL,
                        pending_billing_owner_subscription_id = NULL,
                        pending_billing_owner_expires_at = NULL,
                        updated_at = NOW()
                    WHERE group_id = %s
                    """,
                    (group_id,),
                )
        except errors.UndefinedColumn:
            logger.warning("pending billing owner columns missing; cannot clear claim", extra={"group_id": group_id})
        except errors.UndefinedTable:
            logger.warning("group_subscriptions table missing; cannot clear pending claim", extra={"group_id": group_id})

    def confirm_pending_billing_owner_claim(self, group_id: str, subscription_id: str, confirmed_user_id: str) -> None:
        if not group_id or not subscription_id or not confirmed_user_id:
            return
        try:
            with self._client.cursor() as cur:
                cur.execute(
                    """
                    UPDATE group_subscriptions
                    SET
                        billing_owner_user_id = %s,
                        pending_billing_owner_user_id = NULL,
                        pending_billing_owner_subscription_id = NULL,
                        pending_billing_owner_expires_at = NULL,
                        updated_at = NOW()
                    WHERE
                        group_id = %s
                        AND stripe_subscription_id = %s
                        AND billing_owner_user_id IS NULL
                    """,
                    (confirmed_user_id, group_id, subscription_id),
                )
        except errors.UndefinedColumn:
            logger.warning("pending billing owner columns missing; cannot confirm claim", extra={"group_id": group_id})
        except errors.UndefinedTable:
            logger.warning("group_subscriptions table missing; cannot confirm pending claim", extra={"group_id": group_id})

    def upsert_subscription(
        self,
        group_id: str,
        stripe_customer_id: str,
        stripe_subscription_id: str,
        status: str,
        current_period_start: Optional[datetime],
        current_period_end: Optional[datetime],
        *,
        stripe_price_id: Optional[str] = None,
        entitlement_plan: str = "free",
        billing_interval: str = "month",
        is_grandfathered: bool = False,
        quota_anchor_day: Optional[int] = None,
        scheduled_target_price_id: Optional[str] = None,
        scheduled_effective_at: Optional[datetime] = None,
        billing_owner_user_id: Optional[str] = None,
    ) -> None:
        try:
            with self._client.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO group_subscriptions (
                        group_id,
                        stripe_customer_id,
                        stripe_subscription_id,
                        status,
                        current_period_start,
                        current_period_end,
                        stripe_price_id,
                        entitlement_plan,
                        billing_interval,
                        is_grandfathered,
                        quota_anchor_day,
                        billing_owner_user_id,
                        scheduled_target_price_id,
                        scheduled_effective_at,
                        created_at,
                        updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                    ON CONFLICT (group_id)
                    DO UPDATE SET
                        stripe_customer_id = EXCLUDED.stripe_customer_id,
                        stripe_subscription_id = EXCLUDED.stripe_subscription_id,
                        status = EXCLUDED.status,
                        current_period_start = EXCLUDED.current_period_start,
                        current_period_end = EXCLUDED.current_period_end,
                        stripe_price_id = EXCLUDED.stripe_price_id,
                        entitlement_plan = EXCLUDED.entitlement_plan,
                        billing_interval = EXCLUDED.billing_interval,
                        is_grandfathered = EXCLUDED.is_grandfathered,
                        quota_anchor_day = EXCLUDED.quota_anchor_day,
                        billing_owner_user_id = COALESCE(group_subscriptions.billing_owner_user_id, EXCLUDED.billing_owner_user_id),
                        scheduled_target_price_id = EXCLUDED.scheduled_target_price_id,
                        scheduled_effective_at = EXCLUDED.scheduled_effective_at,
                        updated_at = NOW()
                    """,
                    (
                        group_id,
                        stripe_customer_id,
                        stripe_subscription_id,
                        status,
                        current_period_start,
                        current_period_end,
                        stripe_price_id,
                        entitlement_plan,
                        billing_interval,
                        is_grandfathered,
                        quota_anchor_day,
                        billing_owner_user_id,
                        scheduled_target_price_id,
                        scheduled_effective_at,
                    ),
                )
        except errors.UndefinedColumn:
            # 後方互換: 新カラムが無い環境では旧カラムのみ更新
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

    def fetch_translation_runtime_state(self, group_id: str) -> TranslationRuntimeState:
        translation_enabled = True
        try:
            with self._client.cursor() as cur:
                cur.execute(
                    "SELECT translation_enabled FROM group_settings WHERE group_id = %s",
                    (group_id,),
                )
                row = cur.fetchone()
                if row is not None:
                    translation_enabled = bool(row[0])
        except errors.UndefinedTable:
            translation_enabled = True

        group_languages: List[str] = []
        try:
            with self._client.cursor() as cur:
                cur.execute(
                    """
                    SELECT lang_code
                    FROM group_languages
                    WHERE group_id = %s
                    ORDER BY created_at ASC, lang_code ASC
                    """,
                    (group_id,),
                )
                rows = cur.fetchall()
                group_languages = [row[0] for row in rows if row and row[0]]
        except errors.UndefinedColumn:
            with self._client.cursor() as cur:
                cur.execute(
                    """
                    SELECT lang_code
                    FROM group_languages
                    WHERE group_id = %s
                    ORDER BY lang_code ASC
                    """,
                    (group_id,),
                )
                rows = cur.fetchall()
                group_languages = [row[0] for row in rows if row and row[0]]
        except errors.UndefinedTable:
            group_languages = []

        (
            status,
            entitlement_plan,
            billing_interval,
            is_grandfathered,
            _stripe_price_id,
            period_start,
            period_end,
            quota_anchor_day,
            scheduled_target_price_id,
            scheduled_effective_at,
        ) = self.get_subscription_plan(group_id)

        effective_plan = resolve_effective_plan(status, entitlement_plan)
        period_key = self._compute_period_key(
            plan_key=effective_plan,
            period_start=period_start,
            period_end=period_end,
            quota_anchor_day=quota_anchor_day,
        )
        usage = self.get_usage(group_id, period_key)
        notice_plan = self.get_limit_notice_plan(group_id, period_key)

        return TranslationRuntimeState(
            translation_enabled=translation_enabled,
            group_languages=group_languages,
            subscription_status=status,
            period_start=period_start,
            period_end=period_end,
            period_key=period_key,
            usage=usage,
            limit_notice_plan=notice_plan,
            entitlement_plan=effective_plan,
            billing_interval=billing_interval,
            is_grandfathered=is_grandfathered,
            quota_anchor_day=quota_anchor_day,
            scheduled_target_price_id=scheduled_target_price_id,
            scheduled_effective_at=scheduled_effective_at,
        )

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
            INSERT INTO group_members (group_id, user_id, display_name, display_name_updated_at, joined_at)
            VALUES (%s, %s, %s, NOW(), %s)
            ON CONFLICT (group_id, user_id)
            DO UPDATE SET joined_at = EXCLUDED.joined_at
            """
        )
        with self._client.cursor() as cur:
            cur.execute(query, (group_id, BOT_JOIN_MARKER, BOT_JOIN_MARKER, ts))

    def fetch_bot_joined_at(self, group_id: str) -> Optional[datetime]:
        with self._client.cursor() as cur:
            cur.execute(
                "SELECT joined_at FROM group_members WHERE group_id = %s AND user_id = %s",
                (group_id, BOT_JOIN_MARKER),
            )
            row = cur.fetchone()
        return row[0] if row else None

    def delete_expired_encrypted_messages(self, *, retention_days: int = 7) -> int:
        if retention_days < 1:
            retention_days = 1
        threshold = datetime.now(timezone.utc) - timedelta(days=retention_days)
        try:
            with self._client.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM messages
                    WHERE is_encrypted = TRUE
                      AND timestamp < %s
                    """,
                    (threshold,),
                )
                return cur.rowcount or 0
        except errors.UndefinedColumn:
            return 0
        except errors.UndefinedTable:
            return 0

    def _restore_message_text(self, text: str, is_encrypted: bool, encrypted_body: Optional[str]) -> str:
        if not is_encrypted or not encrypted_body:
            return text or ""
        if not self._message_encryption_key:
            return text or ""
        try:
            return decrypt_text(encrypted_body, key_secret=self._message_encryption_key)
        except Exception:  # pylint: disable=broad-except
            logger.warning("Failed to decrypt message; fallback to stored text", exc_info=True)
            return text or ""

    def _should_encrypt_group_message(self, group_id: str) -> bool:
        if not self._message_encryption_key:
            return False
        if not group_id:
            return False
        try:
            status, entitlement_plan, *_ = self.get_subscription_plan(group_id)
            effective_plan = resolve_effective_plan(status, entitlement_plan)
            return effective_plan == PRO_PLAN
        except Exception:  # pylint: disable=broad-except
            logger.warning("Failed to resolve plan for message encryption", exc_info=True)
            return False

    @staticmethod
    def _compute_period_key(
        *,
        plan_key: str,
        period_start: Optional[datetime],
        period_end: Optional[datetime],
        quota_anchor_day: Optional[int],
    ) -> str:
        now = datetime.now(timezone.utc)
        if plan_key == FREE_PLAN:
            return f"{now.year:04d}-{now.month:02d}-01"

        anchor = period_start
        if not anchor and period_end:
            anchor = period_end - timedelta(days=31)
        if anchor:
            return anchor.astimezone(timezone.utc).date().isoformat()

        if quota_anchor_day:
            normalized_day = min(max(int(quota_anchor_day), 1), 31)
            if now.day >= normalized_day:
                return f"{now.year:04d}-{now.month:02d}-{normalized_day:02d}"
            prev = now.replace(day=1) - timedelta(days=1)
            safe_day = min(normalized_day, prev.day)
            return f"{prev.year:04d}-{prev.month:02d}-{safe_day:02d}"
        return f"{now.year:04d}-{now.month:02d}-01"
