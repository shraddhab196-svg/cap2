-- Lets the app check how full the "documents" bucket is before keeping another original file
-- (app.py STORAGE_BUDGET_MB, default 800 MB; Supabase's free plan has 1 GB of storage per project).
-- Returns one number, the bucket's total size in bytes; it reveals no file names or contents.

CREATE OR REPLACE FUNCTION public.documents_bucket_bytes()
RETURNS bigint
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $$
    SELECT coalesce(sum((metadata->>'size')::bigint), 0)
    FROM storage.objects
    WHERE bucket_id = 'documents';
$$;

REVOKE ALL ON FUNCTION public.documents_bucket_bytes() FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.documents_bucket_bytes() TO authenticated;
