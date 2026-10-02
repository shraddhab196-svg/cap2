-- Chunk 10: web feedback + revision loop
-- Links generated letters to a job application and stores the feedback that produced each revision.
-- Non-destructive: only adds nullable columns, an index, and tightens insert/update policies.
-- Requires migration_add_job_applications.sql to have been applied first.

BEGIN;

ALTER TABLE public.generated_cover_letters
    ADD COLUMN IF NOT EXISTS job_application_id UUID
        REFERENCES public.job_applications(id) ON DELETE CASCADE;

ALTER TABLE public.generated_cover_letters
    ADD COLUMN IF NOT EXISTS feedback TEXT;

CREATE INDEX IF NOT EXISTS idx_generated_cover_letters_job_application_id
    ON public.generated_cover_letters (job_application_id);

-- Ownership: the row must belong to the current user AND, when linked, the job application
-- must belong to that same user. SELECT/DELETE policies are unchanged (user_id ownership).
DROP POLICY IF EXISTS "generated_cover_letters_insert_own_records" ON public.generated_cover_letters;
CREATE POLICY "generated_cover_letters_insert_own_records"
ON public.generated_cover_letters
FOR INSERT
WITH CHECK (
    user_id = (SELECT id FROM public.users WHERE auth_user_id = auth.uid())
    AND (
        job_application_id IS NULL
        OR EXISTS (
            SELECT 1 FROM public.job_applications ja
            WHERE ja.id = job_application_id
              AND ja.user_id = (SELECT id FROM public.users WHERE auth_user_id = auth.uid())
        )
    )
);

DROP POLICY IF EXISTS "generated_cover_letters_update_own_records" ON public.generated_cover_letters;
CREATE POLICY "generated_cover_letters_update_own_records"
ON public.generated_cover_letters
FOR UPDATE
USING (user_id = (SELECT id FROM public.users WHERE auth_user_id = auth.uid()))
WITH CHECK (
    user_id = (SELECT id FROM public.users WHERE auth_user_id = auth.uid())
    AND (
        job_application_id IS NULL
        OR EXISTS (
            SELECT 1 FROM public.job_applications ja
            WHERE ja.id = job_application_id
              AND ja.user_id = (SELECT id FROM public.users WHERE auth_user_id = auth.uid())
        )
    )
);

COMMIT;
