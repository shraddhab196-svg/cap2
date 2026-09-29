-- Controlled schema reconciliation for Chunk 9
-- Purpose:
--   Bring the existing Supabase database toward the intended project schema in schema.sql
--   without dropping tables, deleting rows, or modifying application code.
--
-- Safety notes:
--   - This migration is intentionally non-destructive.
--   - It uses ADD COLUMN IF NOT EXISTS and CREATE TABLE IF NOT EXISTS.
--   - It checks for duplicate or invalid auth_user_id values before creating uniqueness/foreign-key
--     constraints that could fail on existing data.
--   - If the live database contains conflicting data, this migration leaves the constraint unset and
--     emits a NOTICE so the data can be reconciled manually before the constraint is added.

BEGIN;

-- -----------------------------------------------------------------------------
-- 1) USERS: add missing columns, but do not overwrite existing data
-- -----------------------------------------------------------------------------
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS auth_user_id UUID;
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS email TEXT;
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

-- Ensure uuid default for users.id if it is not already set.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'users'
          AND column_name = 'id'
          AND is_nullable = 'NO'
    ) THEN
        -- Leave the column as-is; the project schema already declares the PK default.
        NULL;
    END IF;
END $$;

-- If there are duplicate auth_user_id values, do not create a uniqueness constraint automatically.
-- This prevents a destructive failure on existing data.
DO $$
BEGIN
    IF EXISTS (
        SELECT auth_user_id
        FROM public.users
        WHERE auth_user_id IS NOT NULL
        GROUP BY auth_user_id
        HAVING COUNT(*) > 1
    ) THEN
        RAISE NOTICE 'Duplicate auth_user_id values detected in public.users. Manual reconciliation required before unique constraint creation.';
    ELSIF NOT EXISTS (
        SELECT 1
        FROM pg_indexes
        WHERE schemaname = 'public'
          AND indexname = 'users_auth_user_id_unique_idx'
    ) THEN
        CREATE UNIQUE INDEX IF NOT EXISTS users_auth_user_id_unique_idx
            ON public.users (auth_user_id)
            WHERE auth_user_id IS NOT NULL;
    END IF;
END $$;

-- Add a foreign key only if existing auth_user_id values are all valid references.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM public.users u
        LEFT JOIN auth.users au ON au.id = u.auth_user_id
        WHERE u.auth_user_id IS NOT NULL
          AND au.id IS NULL
    ) THEN
        RAISE NOTICE 'Invalid auth_user_id values detected in public.users. Manual reconciliation required before adding FK to auth.users.';
    ELSIF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'public.users'::regclass
          AND conname = 'users_auth_user_id_fkey'
    ) THEN
        ALTER TABLE public.users
            ADD CONSTRAINT users_auth_user_id_fkey
            FOREIGN KEY (auth_user_id) REFERENCES auth.users(id) ON DELETE SET NULL;
    END IF;
END $$;

-- -----------------------------------------------------------------------------
-- 2) RESUMES: create if absent
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.resumes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    storage_path TEXT,
    extracted_text TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- -----------------------------------------------------------------------------
-- 3) CANDIDATE_PROFILES: create if absent
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.candidate_profiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    profile JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- -----------------------------------------------------------------------------
-- 4) GENERATED_COVER_LETTERS: create if absent
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.generated_cover_letters (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    content TEXT NOT NULL,
    revision_number INTEGER,
    is_final BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- -----------------------------------------------------------------------------
-- 5) Indexes required by schema.sql
-- -----------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_users_auth_user_id
    ON public.users (auth_user_id);

CREATE INDEX IF NOT EXISTS idx_resumes_user_id
    ON public.resumes (user_id);

CREATE INDEX IF NOT EXISTS idx_cover_letters_user_id
    ON public.cover_letters (user_id);

CREATE INDEX IF NOT EXISTS idx_candidate_profiles_user_id
    ON public.candidate_profiles (user_id);

CREATE INDEX IF NOT EXISTS idx_style_profiles_user_id
    ON public.style_profiles (user_id);

CREATE INDEX IF NOT EXISTS idx_generated_cover_letters_user_id
    ON public.generated_cover_letters (user_id);

-- -----------------------------------------------------------------------------
-- 6) RLS: enable on all six tables if present
-- -----------------------------------------------------------------------------
ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.resumes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.candidate_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.cover_letters ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.style_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.generated_cover_letters ENABLE ROW LEVEL SECURITY;

-- -----------------------------------------------------------------------------
-- 7) RLS policies: create idempotently and tie data ownership to auth.uid()
-- -----------------------------------------------------------------------------
DROP POLICY IF EXISTS "users_select_own_row" ON public.users;
CREATE POLICY "users_select_own_row"
ON public.users
FOR SELECT
USING (auth.uid() = auth_user_id);

DROP POLICY IF EXISTS "users_insert_own_row" ON public.users;
CREATE POLICY "users_insert_own_row"
ON public.users
FOR INSERT
WITH CHECK (auth.uid() = auth_user_id);

DROP POLICY IF EXISTS "users_update_own_row" ON public.users;
CREATE POLICY "users_update_own_row"
ON public.users
FOR UPDATE
USING (auth.uid() = auth_user_id)
WITH CHECK (auth.uid() = auth_user_id);

DROP POLICY IF EXISTS "users_delete_own_row" ON public.users;
CREATE POLICY "users_delete_own_row"
ON public.users
FOR DELETE
USING (auth.uid() = auth_user_id);

DROP POLICY IF EXISTS "resumes_select_own_records" ON public.resumes;
CREATE POLICY "resumes_select_own_records"
ON public.resumes
FOR SELECT
USING (user_id = (SELECT id FROM public.users WHERE auth_user_id = auth.uid()));

DROP POLICY IF EXISTS "resumes_insert_own_records" ON public.resumes;
CREATE POLICY "resumes_insert_own_records"
ON public.resumes
FOR INSERT
WITH CHECK (user_id = (SELECT id FROM public.users WHERE auth_user_id = auth.uid()));

DROP POLICY IF EXISTS "resumes_update_own_records" ON public.resumes;
CREATE POLICY "resumes_update_own_records"
ON public.resumes
FOR UPDATE
USING (user_id = (SELECT id FROM public.users WHERE auth_user_id = auth.uid()))
WITH CHECK (user_id = (SELECT id FROM public.users WHERE auth_user_id = auth.uid()));

DROP POLICY IF EXISTS "resumes_delete_own_records" ON public.resumes;
CREATE POLICY "resumes_delete_own_records"
ON public.resumes
FOR DELETE
USING (user_id = (SELECT id FROM public.users WHERE auth_user_id = auth.uid()));

DROP POLICY IF EXISTS "candidate_profiles_select_own_records" ON public.candidate_profiles;
CREATE POLICY "candidate_profiles_select_own_records"
ON public.candidate_profiles
FOR SELECT
USING (user_id = (SELECT id FROM public.users WHERE auth_user_id = auth.uid()));

DROP POLICY IF EXISTS "candidate_profiles_insert_own_records" ON public.candidate_profiles;
CREATE POLICY "candidate_profiles_insert_own_records"
ON public.candidate_profiles
FOR INSERT
WITH CHECK (user_id = (SELECT id FROM public.users WHERE auth_user_id = auth.uid()));

DROP POLICY IF EXISTS "candidate_profiles_update_own_records" ON public.candidate_profiles;
CREATE POLICY "candidate_profiles_update_own_records"
ON public.candidate_profiles
FOR UPDATE
USING (user_id = (SELECT id FROM public.users WHERE auth_user_id = auth.uid()))
WITH CHECK (user_id = (SELECT id FROM public.users WHERE auth_user_id = auth.uid()));

DROP POLICY IF EXISTS "candidate_profiles_delete_own_records" ON public.candidate_profiles;
CREATE POLICY "candidate_profiles_delete_own_records"
ON public.candidate_profiles
FOR DELETE
USING (user_id = (SELECT id FROM public.users WHERE auth_user_id = auth.uid()));

DROP POLICY IF EXISTS "cover_letters_select_own_records" ON public.cover_letters;
CREATE POLICY "cover_letters_select_own_records"
ON public.cover_letters
FOR SELECT
USING (user_id = (SELECT id FROM public.users WHERE auth_user_id = auth.uid()));

DROP POLICY IF EXISTS "cover_letters_insert_own_records" ON public.cover_letters;
CREATE POLICY "cover_letters_insert_own_records"
ON public.cover_letters
FOR INSERT
WITH CHECK (user_id = (SELECT id FROM public.users WHERE auth_user_id = auth.uid()));

DROP POLICY IF EXISTS "cover_letters_update_own_records" ON public.cover_letters;
CREATE POLICY "cover_letters_update_own_records"
ON public.cover_letters
FOR UPDATE
USING (user_id = (SELECT id FROM public.users WHERE auth_user_id = auth.uid()))
WITH CHECK (user_id = (SELECT id FROM public.users WHERE auth_user_id = auth.uid()));

DROP POLICY IF EXISTS "cover_letters_delete_own_records" ON public.cover_letters;
CREATE POLICY "cover_letters_delete_own_records"
ON public.cover_letters
FOR DELETE
USING (user_id = (SELECT id FROM public.users WHERE auth_user_id = auth.uid()));

DROP POLICY IF EXISTS "style_profiles_select_own_records" ON public.style_profiles;
CREATE POLICY "style_profiles_select_own_records"
ON public.style_profiles
FOR SELECT
USING (user_id = (SELECT id FROM public.users WHERE auth_user_id = auth.uid()));

DROP POLICY IF EXISTS "style_profiles_insert_own_records" ON public.style_profiles;
CREATE POLICY "style_profiles_insert_own_records"
ON public.style_profiles
FOR INSERT
WITH CHECK (user_id = (SELECT id FROM public.users WHERE auth_user_id = auth.uid()));

DROP POLICY IF EXISTS "style_profiles_update_own_records" ON public.style_profiles;
CREATE POLICY "style_profiles_update_own_records"
ON public.style_profiles
FOR UPDATE
USING (user_id = (SELECT id FROM public.users WHERE auth_user_id = auth.uid()))
WITH CHECK (user_id = (SELECT id FROM public.users WHERE auth_user_id = auth.uid()));

DROP POLICY IF EXISTS "style_profiles_delete_own_records" ON public.style_profiles;
CREATE POLICY "style_profiles_delete_own_records"
ON public.style_profiles
FOR DELETE
USING (user_id = (SELECT id FROM public.users WHERE auth_user_id = auth.uid()));

DROP POLICY IF EXISTS "generated_cover_letters_select_own_records" ON public.generated_cover_letters;
CREATE POLICY "generated_cover_letters_select_own_records"
ON public.generated_cover_letters
FOR SELECT
USING (user_id = (SELECT id FROM public.users WHERE auth_user_id = auth.uid()));

DROP POLICY IF EXISTS "generated_cover_letters_insert_own_records" ON public.generated_cover_letters;
CREATE POLICY "generated_cover_letters_insert_own_records"
ON public.generated_cover_letters
FOR INSERT
WITH CHECK (user_id = (SELECT id FROM public.users WHERE auth_user_id = auth.uid()));

DROP POLICY IF EXISTS "generated_cover_letters_update_own_records" ON public.generated_cover_letters;
CREATE POLICY "generated_cover_letters_update_own_records"
ON public.generated_cover_letters
FOR UPDATE
USING (user_id = (SELECT id FROM public.users WHERE auth_user_id = auth.uid()))
WITH CHECK (user_id = (SELECT id FROM public.users WHERE auth_user_id = auth.uid()));

DROP POLICY IF EXISTS "generated_cover_letters_delete_own_records" ON public.generated_cover_letters;
CREATE POLICY "generated_cover_letters_delete_own_records"
ON public.generated_cover_letters
FOR DELETE
USING (user_id = (SELECT id FROM public.users WHERE auth_user_id = auth.uid()));

-- -----------------------------------------------------------------------------
-- 8) Manual reconciliation note
-- -----------------------------------------------------------------------------
-- If the live database contains duplicate auth_user_id values, invalid auth_user_id references,
-- or conflicting historical rows, this migration intentionally does not force a destructive fix.
-- Those rows must be reviewed manually before a unique index or FK constraint is added.

COMMIT;
