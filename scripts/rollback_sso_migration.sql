-- Safe Rollback Script for Microsoft SSO Schema Changes (PostgreSQL)
--
-- PURPOSE:
-- Safely re-applies NOT NULL on `users.password_hash` without violating foreign keys,
-- deleting historical interview/copilot records, or failing with NotNullViolation errors.
--
-- EXECUTION:
-- psql -U <db_user> -d <db_name> -f scripts/rollback_sso_migration.sql

BEGIN;

-- Step 1: Check how many users have password_hash IS NULL
DO $$
DECLARE
    null_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO null_count FROM users WHERE password_hash IS NULL;
    RAISE NOTICE 'Found % SSO users with NULL password_hash', null_count;
END $$;

-- Step 2: Assign an unmatchable shadow password hash to prevent login and satisfy NOT NULL.
-- The format '!LOCKED_SSO_ROLLBACK!' cannot be matched by any bcrypt hash evaluation.
UPDATE users
SET password_hash = '!LOCKED_SSO_ROLLBACK!'
WHERE password_hash IS NULL;

-- Step 3: Re-apply the NOT NULL constraint on password_hash
ALTER TABLE users ALTER COLUMN password_hash SET NOT NULL;

-- Step 4: Schema cleanup for external identities (optional, commented out by default)
-- Uncomment if the SSO integration table must be dropped:
-- DROP TABLE IF EXISTS user_external_identities;

COMMIT;
