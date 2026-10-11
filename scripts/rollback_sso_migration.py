#!/usr/bin/env python3
"""
Safe Rollback Script for Microsoft SSO Schema Changes.

This script safely handles users who signed up via SSO (whose `password_hash` is NULL)
before restoring any NOT NULL constraints, preventing database constraint crashes
and preserving foreign key integrity for user interviews, copilots, and audit logs.

Usage:
    python scripts/rollback_sso_migration.py [--dry-run] [--drop-identities-table]
"""

import argparse
import asyncio
import os
import sys
from dotenv import load_dotenv

# Load environment
load_dotenv(override=True)

# Add repo root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tortoise import Tortoise
from loguru import logger
from services.copilot.src.core.config import Settings as CopilotSettings
from services.interview.src.core.config import Settings as InterviewSettings


LOCKED_SHADOW_HASH = "!LOCKED_SSO_ROLLBACK!"


async def run_rollback(dry_run: bool = False, drop_identities: bool = False, db_url: str | None = None):
    target_db_url = db_url or os.getenv("DATABASE_URL") or CopilotSettings.DATABASE_URL or InterviewSettings.DATABASE_URL
    if not target_db_url:
        logger.error("No DATABASE_URL configured.")
        return False

    logger.info(f"Connecting to database (dry_run={dry_run})...")
    await Tortoise.init(
        db_url=target_db_url,
        modules={
            "models": [
                "services.interview.src.models.interview",
                "services.copilot.src.models.copilot",
                "services.auth.src.models.user",
            ]
        },
    )

    conn = Tortoise.get_connection("default")
    dialect = getattr(conn.capabilities, "dialect", "")
    logger.info(f"Connected to database engine dialect: {dialect}")

    try:
        # 1. Count SSO users where password_hash is NULL
        _, count_rows = await conn.execute_query(
            "SELECT COUNT(*) as cnt FROM users WHERE password_hash IS NULL;"
        )
        row = count_rows[0] if count_rows else None
        null_count = row["cnt"] if row is not None else 0
        logger.info(f"Detected {null_count} users with NULL password_hash.")

        if dry_run:
            logger.info("[DRY RUN] Would set locked shadow hash for null users and restore NOT NULL.")
            return True

        if null_count > 0:
            logger.info(f"Updating {null_count} users to locked shadow hash...")
            await conn.execute_query(
                f"UPDATE users SET password_hash = '{LOCKED_SHADOW_HASH}' WHERE password_hash IS NULL;"
            )
            logger.info("Successfully populated locked shadow password hashes.")

        # 2. Apply NOT NULL constraint if PostgreSQL
        if dialect == "postgres":
            logger.info("Enforcing NOT NULL on users.password_hash in PostgreSQL...")
            await conn.execute_query("ALTER TABLE users ALTER COLUMN password_hash SET NOT NULL;")
            logger.info("NOT NULL constraint restored on users.password_hash.")
        else:
            logger.info("SQLite schema does not require ALTER COLUMN SET NOT NULL.")

        # 3. Optionally drop user_external_identities table
        if drop_identities:
            logger.warning("Dropping user_external_identities table as requested...")
            await conn.execute_query("DROP TABLE IF EXISTS user_external_identities;")
            logger.info("Dropped user_external_identities.")

        logger.info("Rollback completed safely with relational integrity intact.")
        return True
    finally:
        await Tortoise.close_connections()


def main():
    parser = argparse.ArgumentParser(description="Safely rollback SSO migration")
    parser.add_argument("--dry-run", action="store_true", help="Report planned operations without modifying DB")
    parser.add_argument("--drop-identities-table", action="store_true", help="Also drop user_external_identities table")
    args = parser.parse_args()

    success = asyncio.run(run_rollback(dry_run=args.dry_run, drop_identities=args.drop_identities_table))
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
