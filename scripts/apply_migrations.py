#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict

import psycopg


MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "sql"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply SQL migrations to Neon/PostgreSQL.")
    parser.add_argument("--db-url", required=True, help="PostgreSQL connection URL")
    parser.add_argument("--dry-run", action="store_true", help="Print pending migrations without applying")
    return parser.parse_args()


def _already_applied_checks() -> Dict[str, str]:
    return {
        "001_init_schema.sql": """
            SELECT
              to_regclass('public.group_members') IS NOT NULL
              AND to_regclass('public.group_user_languages') IS NOT NULL
              AND to_regclass('public.group_languages') IS NOT NULL
              AND to_regclass('public.messages') IS NOT NULL
        """,
        "002_group_user_languages.sql": """
            SELECT
              to_regclass('public.group_user_languages') IS NOT NULL
              AND EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'group_members' AND column_name = 'joined_at'
              )
              AND EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'group_members' AND column_name = 'last_prompted_at'
              )
              AND EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'group_members' AND column_name = 'last_completed_at'
              )
        """,
        "003_group_languages.sql": """
            SELECT to_regclass('public.group_languages') IS NOT NULL
        """,
        "20251130_add_group_settings.sql": """
            SELECT to_regclass('public.group_settings') IS NOT NULL
        """,
        "20251206_stripe_billing.sql": """
            SELECT
              to_regclass('public.group_subscriptions') IS NOT NULL
              AND to_regclass('public.group_usage_counters') IS NOT NULL
        """,
        "20251207_update_usage_notice_plan.sql": """
            SELECT
              EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'group_usage_counters' AND column_name = 'limit_notice_plan'
              )
              AND EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                WHERE table_name = 'group_usage_counters'
                  AND constraint_name = 'chk_usage_notice_plan'
              )
        """,
        "20251209_align_usage_cycle_with_subscription.sql": """
            SELECT EXISTS (
              SELECT 1
              FROM information_schema.columns
              WHERE table_name = 'group_usage_counters'
                AND column_name = 'period_key'
            )
        """,
        "20251210_add_group_name_to_group_settings.sql": """
            SELECT EXISTS (
              SELECT 1
              FROM information_schema.columns
              WHERE table_name = 'group_settings'
                AND column_name = 'group_name'
            )
        """,
        "20260211_add_contact_rate_limits.sql": """
            SELECT to_regclass('public.contact_rate_limits') IS NOT NULL
        """,
        "20260211_add_message_role_for_private_chat.sql": """
            SELECT
              EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'messages' AND column_name = 'message_role'
              )
              AND EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'messages_message_role_check'
              )
        """,
        "20260212_add_display_name_to_group_members.sql": """
            SELECT
              EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'group_members' AND column_name = 'display_name'
              )
              AND
              EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'group_members' AND column_name = 'display_name_updated_at'
              )
        """,
        "20260212_make_group_member_display_name_nullable.sql": """
            SELECT
              EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'group_members'
                  AND column_name = 'display_name'
                  AND is_nullable = 'YES'
              )
              AND EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'group_members'
                  AND column_name = 'display_name_updated_at'
                  AND is_nullable = 'YES'
                  AND column_default IS NULL
              )
        """,
    }


def _ensure_migration_table(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                filename TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    conn.commit()


def _is_recorded(conn: psycopg.Connection, filename: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM schema_migrations WHERE filename = %s",
            (filename,),
        )
        return cur.fetchone() is not None


def _mark_applied(conn: psycopg.Connection, filename: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO schema_migrations (filename)
            VALUES (%s)
            ON CONFLICT (filename) DO NOTHING
            """,
            (filename,),
        )
    conn.commit()


def _is_already_applied_by_state(conn: psycopg.Connection, filename: str, checks: Dict[str, str]) -> bool:
    check_sql = checks.get(filename)
    if not check_sql:
        return False
    with conn.cursor() as cur:
        cur.execute(check_sql)
        row = cur.fetchone()
    return bool(row and row[0])


def _migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"), key=lambda p: p.name)


def main() -> int:
    args = _parse_args()
    checks = _already_applied_checks()
    files = _migration_files()
    if not files:
        print("No migration files found.")
        return 0

    with psycopg.connect(args.db_url) as conn:
        _ensure_migration_table(conn)

        for path in files:
            name = path.name
            if _is_recorded(conn, name):
                print(f"SKIP recorded: {name}")
                continue

            if _is_already_applied_by_state(conn, name, checks):
                print(f"MARK applied by state: {name}")
                if not args.dry_run:
                    _mark_applied(conn, name)
                continue

            if args.dry_run:
                print(f"PENDING: {name}")
                continue

            sql_text = path.read_text(encoding="utf-8")
            with conn.cursor() as cur:
                cur.execute(sql_text)
            conn.commit()
            _mark_applied(conn, name)
            print(f"APPLIED: {name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
