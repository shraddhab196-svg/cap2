-- users
CREATE TABLE IF NOT EXISTS public.users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    auth_user_id UUID UNIQUE REFERENCES auth.users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    name TEXT,
    email TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- resumes
CREATE TABLE IF NOT EXISTS public.resumes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    storage_path TEXT,
    extracted_text TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- candidate_profiles
CREATE TABLE IF NOT EXISTS public.candidate_profiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    profile JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- cover_letters
CREATE TABLE IF NOT EXISTS public.cover_letters (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- style_profiles
CREATE TABLE IF NOT EXISTS public.style_profiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    profile JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- generated_cover_letters
CREATE TABLE IF NOT EXISTS public.generated_cover_letters (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    content TEXT NOT NULL,
    revision_number INTEGER,
    is_final BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_auth_user_id
    ON public.users (auth_user_id);

CREATE INDEX IF NOT EXISTS idx_resumes_user_id
    ON public.resumes (user_id);

CREATE INDEX IF NOT EXISTS idx_candidate_profiles_user_id
    ON public.candidate_profiles (user_id);

CREATE INDEX IF NOT EXISTS idx_cover_letters_user_id
    ON public.cover_letters (user_id);

CREATE INDEX IF NOT EXISTS idx_style_profiles_user_id
    ON public.style_profiles (user_id);

CREATE INDEX IF NOT EXISTS idx_generated_cover_letters_user_id
    ON public.generated_cover_letters (user_id);

ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.resumes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.candidate_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.cover_letters ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.style_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.generated_cover_letters ENABLE ROW LEVEL SECURITY;

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
