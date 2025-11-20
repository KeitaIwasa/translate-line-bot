from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

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


def fetch_group_language_preferences(client: NeonClient, group_id: str) -> Dict[str, Optional[str]]:
    query = sql.SQL(
        """
        SELECT user_id, preferred_lang
        FROM group_members
        WHERE group_id = %s
        """
    )

    with client.cursor() as cur:
        cur.execute(query, (group_id,))
        rows = cur.fetchall()

    return {row[0]: row[1] for row in rows}


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
