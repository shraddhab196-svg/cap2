-- Company angles used to be written to the server's local disk, which free hosts wipe on every restart.
-- They now live on the job application row, covered by the existing job_applications RLS policies.
-- Requires migration_add_job_applications.sql to have been applied first.
ALTER TABLE public.job_applications ADD COLUMN IF NOT EXISTS anchors JSONB;
