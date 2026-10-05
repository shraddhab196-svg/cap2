-- Post-revision satisfaction response (asked after the 3rd revision).
-- Stored on the generated letter the user was asked about, so user_id, job_application_id,
-- id and revision_number already identify who/which application/which letter/which revision.
-- Kept separate from generated_cover_letters.feedback, which holds the revision feedback that produced that letter.
-- Non-destructive: nullable columns only. The existing update policy (generated_cover_letters_update_own_records)
-- already restricts updates to the owner's rows, so no RLS change is needed.

BEGIN;

ALTER TABLE public.generated_cover_letters
    ADD COLUMN IF NOT EXISTS satisfaction TEXT
        CHECK (satisfaction IN ('satisfied', 'not_satisfied'));

ALTER TABLE public.generated_cover_letters
    ADD COLUMN IF NOT EXISTS satisfaction_feedback TEXT;

ALTER TABLE public.generated_cover_letters
    ADD COLUMN IF NOT EXISTS satisfaction_submitted_at TIMESTAMPTZ;

COMMIT;

-- Make the API (PostgREST) see the new columns immediately.
NOTIFY pgrst, 'reload schema';
