from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

from psycopg import sql

from .neon_client import NeonClient


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


def fetch_group_language_preferences(client: NeonClient, group_id: str) -> Dict[str, List[str]]:
    query = sql.SQL(
        """
        SELECT user_id, lang_code
        FROM group_user_languages
        WHERE group_id = %s
        ORDER BY user_id, lang_code
        """
    )

    with client.cursor() as cur:
        cur.execute(query, (group_id,))
        rows = cur.fetchall()

    result: Dict[str, List[str]] = {}
    for user_id, lang_code in rows:
        result.setdefault(user_id, []).append(lang_code)
    return result


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


def reset_group_language_settings(client: NeonClient, group_id: str) -> None:
    with client.cursor() as cur:
        cur.execute("DELETE FROM group_user_languages WHERE group_id = %s", (group_id,))
        cur.execute(
            "UPDATE group_members SET last_prompted_at = NULL, last_completed_at = NULL WHERE group_id = %s",
            (group_id,),
        )


def record_language_prompt(client: NeonClient, group_id: str, user_id: str) -> None:
    query = sql.SQL(
        """
        INSERT INTO group_members (group_id, user_id, last_prompted_at)
        VALUES (%s, %s, NOW())
        ON CONFLICT (group_id, user_id)
        DO UPDATE SET last_prompted_at = NOW()
        """
    )

    with client.cursor() as cur:
        cur.execute(query, (group_id, user_id))


def replace_user_languages(
    client: NeonClient,
    group_id: str,
    user_id: str,
    languages: Sequence[Tuple[str, str]],
) -> None:
    with client.cursor() as cur:
        cur.execute("DELETE FROM group_user_languages WHERE group_id = %s AND user_id = %s", (group_id, user_id))
        if languages:
            cur.executemany(
                """
                INSERT INTO group_user_languages (group_id, user_id, lang_code, lang_name)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (group_id, user_id, lang_code) DO UPDATE SET lang_name = EXCLUDED.lang_name
                """,
                [(group_id, user_id, code.lower(), name) for code, name in languages],
            )
        cur.execute(
            """
            INSERT INTO group_members (group_id, user_id, last_completed_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (group_id, user_id)
            DO UPDATE SET last_completed_at = NOW()
            """,
            (group_id, user_id),
        )


def fetch_user_languages(client: NeonClient, group_id: str, user_id: str) -> List[str]:
    with client.cursor() as cur:
        cur.execute(
            "SELECT lang_code FROM group_user_languages WHERE group_id = %s AND user_id = %s ORDER BY lang_code",
            (group_id, user_id),
        )
        rows = cur.fetchall()
    return [row[0] for row in rows]
