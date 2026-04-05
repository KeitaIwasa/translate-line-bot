#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import psycopg
import stripe


@dataclass
class TargetRow:
    group_id: str
    subscription_id: str
    billing_owner_user_id: Optional[str]
    current_period_end: Optional[datetime]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fix owner-lost subscription consistency rows.")
    parser.add_argument("--db-url", required=True, help="PostgreSQL DSN")
    parser.add_argument("--stripe-secret-key", required=True, help="Stripe secret key")
    parser.add_argument("--group-id", default="", help="Optional target group id")
    parser.add_argument("--apply", action="store_true", help="Apply updates (default is dry-run)")
    return parser.parse_args()


def _fetch_targets(conn: psycopg.Connection, group_id: str) -> list[TargetRow]:
    query = """
        SELECT group_id, stripe_subscription_id, billing_owner_user_id, current_period_end
        FROM group_subscriptions
        WHERE billing_owner_lost_at IS NOT NULL
          AND (
            billing_owner_user_id IS NOT NULL
            OR current_period_end IS NULL
          )
    """
    params = ()
    if group_id:
        query += " AND group_id = %s"
        params = (group_id,)
    with conn.cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall()
    return [TargetRow(*row) for row in rows]


def _fetch_stripe_period_end(subscription_id: str) -> Optional[datetime]:
    try:
        sub = stripe.Subscription.retrieve(subscription_id)
    except Exception:  # pylint: disable=broad-except
        return None
    ts = sub.get("current_period_end")
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    return None


def main() -> None:
    args = _parse_args()
    stripe.api_key = args.stripe_secret_key

    with psycopg.connect(args.db_url) as conn:
        targets = _fetch_targets(conn, args.group_id.strip())
        if not targets:
            print("No inconsistent owner-lost rows found.")
            return

        print(f"Found {len(targets)} inconsistent rows.")
        for row in targets:
            period_end = row.current_period_end or _fetch_stripe_period_end(row.subscription_id)
            print(
                f"group={row.group_id} sub={row.subscription_id} "
                f"owner={row.billing_owner_user_id!r}->None period_end={row.current_period_end}->{period_end}"
            )
            if not args.apply:
                continue
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE group_subscriptions
                    SET
                      billing_owner_user_id = NULL,
                      current_period_end = COALESCE(%s, current_period_end),
                      updated_at = NOW()
                    WHERE group_id = %s
                      AND billing_owner_lost_at IS NOT NULL
                    """,
                    (period_end, row.group_id),
                )
        if args.apply:
            conn.commit()
            print("Applied fixes.")
        else:
            print("Dry-run only. Re-run with --apply to persist.")


if __name__ == "__main__":
    main()
