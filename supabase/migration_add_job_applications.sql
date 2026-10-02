CREATE TABLE IF NOT EXISTS public.job_applications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    job_description TEXT NOT NULL,
    company_url TEXT NOT NULL,
    selected_anchor JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_job_applications_user_id
    ON public.job_applications (user_id);

ALTER TABLE public.job_applications ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "job_applications_select_own_records" ON public.job_applications;
CREATE POLICY "job_applications_select_own_records"
ON public.job_applications
FOR SELECT
USING (user_id = (SELECT id FROM public.users WHERE auth_user_id = auth.uid()));

DROP POLICY IF EXISTS "job_applications_insert_own_records" ON public.job_applications;
CREATE POLICY "job_applications_insert_own_records"
ON public.job_applications
FOR INSERT
WITH CHECK (user_id = (SELECT id FROM public.users WHERE auth_user_id = auth.uid()));

DROP POLICY IF EXISTS "job_applications_update_own_records" ON public.job_applications;
CREATE POLICY "job_applications_update_own_records"
ON public.job_applications
FOR UPDATE
USING (user_id = (SELECT id FROM public.users WHERE auth_user_id = auth.uid()))
WITH CHECK (user_id = (SELECT id FROM public.users WHERE auth_user_id = auth.uid()));

DROP POLICY IF EXISTS "job_applications_delete_own_records" ON public.job_applications;
CREATE POLICY "job_applications_delete_own_records"
ON public.job_applications
FOR DELETE
USING (user_id = (SELECT id FROM public.users WHERE auth_user_id = auth.uid()));
