from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Sequence, Tuple

from psycopg import sql

from ..domain.models import ContextMessage, StoredMessage
from ..domain.ports import MessageRepositoryPort
from .neon_client import NeonClient

BOT_JOIN_MARKER = "__bot_join__"
GROUP_LANG_MARKER = "__group_lang__"


@dataclass(frozen=True)
class _MessageRow:
    sender_name: str
    text: str
    timestamp: datetime


class NeonMessageRepository(MessageRepositoryPort):
    """Neon(PostgreSQL) への永続化を担うリポジトリ実装。"""

    def __init__(self, client: NeonClient) -> None:
        self._client = client

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
                if languages:
                    cur.executemany(
                        """
                        INSERT INTO group_languages (group_id, lang_code, lang_name)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (group_id, lang_code) DO UPDATE SET lang_name = EXCLUDED.lang_name
                        """,
                        [(group_id, code.lower(), name) for code, name in languages],
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
        with self._client.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO group_languages (group_id, lang_code, lang_name)
                VALUES (%s, %s, %s)
                ON CONFLICT (group_id, lang_code) DO UPDATE SET lang_name = EXCLUDED.lang_name
                """,
                [(group_id, code.lower(), name) for code, name in languages],
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
        except Exception:
            # 後方互換: group_settings が未作成でも致命的エラーにしない
            return

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
        except Exception:
            return True

    def reset_group_language_settings(self, group_id: str) -> None:
        with self._client.cursor() as cur:
            cur.execute("DELETE FROM group_languages WHERE group_id = %s", (group_id,))
            cur.execute(
                "UPDATE group_members SET last_prompted_at = NULL, last_completed_at = NULL WHERE group_id = %s",
                (group_id,),
            )

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
