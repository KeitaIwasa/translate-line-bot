from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Sequence, Tuple

from psycopg import sql

from .neon_client import NeonClient

BOT_JOIN_MARKER = "__bot_join__"
GROUP_LANG_MARKER = "__group_lang__"


@dataclass(frozen=True)
class ContextMessage:
    sender_name: str
    text: str
    timestamp: datetime


@dataclass(frozen=True)
class MessageRecord:
    group_id: str
    user_id: str
    sender_name: str
    text: str
    timestamp: datetime


def fetch_group_languages(client: NeonClient, group_id: str) -> List[str]:
    query = sql.SQL(
        """
        SELECT lang_code
        FROM group_languages
        WHERE group_id = %s
        ORDER BY lang_code
        """
    )

    with client.cursor() as cur:
        cur.execute(query, (group_id,))
        rows = cur.fetchall()

    return [row[0] for row in rows]


def fetch_recent_messages(
    client: NeonClient,
    group_id: str,
    bot_user_id: Optional[str],
    limit: int,
) -> List[ContextMessage]:
    params = [group_id]
    exclusion_clause = ""
    if bot_user_id:
        exclusion_clause = "AND user_id <> %s"
        params.append(bot_user_id)

    query = sql.SQL(
        f"""
        SELECT sender_name, text, timestamp
        FROM messages
        WHERE group_id = %s
        {exclusion_clause}
        ORDER BY timestamp DESC
        LIMIT %s
        """
    )
    params.append(limit)

    with client.cursor() as cur:
        cur.execute(query, tuple(params))
        rows = cur.fetchall()

    messages = [ContextMessage(sender_name=row[0], text=row[1], timestamp=row[2]) for row in rows]
    messages.reverse()
    return messages


def insert_message(client: NeonClient, record: MessageRecord) -> None:
    query = sql.SQL(
        """
        INSERT INTO messages (group_id, user_id, sender_name, text, timestamp)
        VALUES (%s, %s, %s, %s, %s)
        """
    )

    ts = record.timestamp
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    with client.cursor() as cur:
        cur.execute(query, (
            record.group_id,
            record.user_id,
            record.sender_name,
            record.text,
            ts,
        ))


def ensure_group_member(client: NeonClient, group_id: str, user_id: str) -> None:
    query = sql.SQL(
        """
        INSERT INTO group_members (group_id, user_id)
        VALUES (%s, %s)
        ON CONFLICT (group_id, user_id)
        DO UPDATE SET joined_at = NOW()
        """
    )

    with client.cursor() as cur:
        cur.execute(query, (group_id, user_id))


def record_bot_joined_at(client: NeonClient, group_id: str, joined_at: datetime) -> None:
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

    with client.cursor() as cur:
        cur.execute(query, (group_id, BOT_JOIN_MARKER, ts))


def fetch_bot_joined_at(client: NeonClient, group_id: str) -> Optional[datetime]:
    with client.cursor() as cur:
        cur.execute(
            "SELECT joined_at FROM group_members WHERE group_id = %s AND user_id = %s",
            (group_id, BOT_JOIN_MARKER),
        )
        row = cur.fetchone()
    return row[0] if row else None


def reset_group_language_settings(client: NeonClient, group_id: str) -> None:
    with client.cursor() as cur:
        cur.execute("DELETE FROM group_languages WHERE group_id = %s", (group_id,))
        cur.execute(
            "UPDATE group_members SET last_prompted_at = NULL, last_completed_at = NULL WHERE group_id = %s",
            (group_id,),
        )


def record_language_prompt(client: NeonClient, group_id: str) -> None:
    """Mark when the group-level language prompt was last sent."""

    query = sql.SQL(
        """
        INSERT INTO group_members (group_id, user_id, last_prompted_at)
        VALUES (%s, %s, NOW())
        ON CONFLICT (group_id, user_id)
        DO UPDATE SET last_prompted_at = NOW()
        """
    )

    with client.cursor() as cur:
        cur.execute(query, (group_id, GROUP_LANG_MARKER))


def replace_group_languages(
    client: NeonClient,
    group_id: str,
    languages: Sequence[Tuple[str, str]],
) -> None:
    with client.cursor() as cur:
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
            INSERT INTO group_members (group_id, user_id, last_completed_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (group_id, user_id)
            DO UPDATE SET last_completed_at = NOW()
            """,
            (group_id, GROUP_LANG_MARKER),
        )


def try_complete_group_languages(
    client: NeonClient,
    group_id: str,
    languages: Sequence[Tuple[str, str]],
) -> bool:
    """Complete language enrollment once; ignore subsequent duplicate postbacks.

    Returns True if completion was performed, False if already completed.
    """

    with client.connection() as conn:
        with conn.cursor() as cur:
            # Ensure marker row exists and lock it to avoid races.
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
                # Already completed previously.
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
