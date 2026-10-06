-- Keep users' original resume and cover-letter files in Supabase Storage.
-- Private bucket "documents"; each user's files live under "<auth user id>/..." and only that user can reach them.
-- The extracted text stays in resumes.extracted_text / cover_letters.content, so the app works without the files.

INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES ('documents', 'documents', false, 5242880, ARRAY['application/pdf', 'text/plain'])
ON CONFLICT (id) DO UPDATE
    SET public = false,
        file_size_limit = EXCLUDED.file_size_limit,
        allowed_mime_types = EXCLUDED.allowed_mime_types;

ALTER TABLE public.cover_letters ADD COLUMN IF NOT EXISTS storage_path TEXT;

DROP POLICY IF EXISTS "documents_select_own" ON storage.objects;
CREATE POLICY "documents_select_own" ON storage.objects
    FOR SELECT TO authenticated
    USING (bucket_id = 'documents' AND (storage.foldername(name))[1] = (SELECT auth.uid())::text);

DROP POLICY IF EXISTS "documents_insert_own" ON storage.objects;
CREATE POLICY "documents_insert_own" ON storage.objects
    FOR INSERT TO authenticated
    WITH CHECK (bucket_id = 'documents' AND (storage.foldername(name))[1] = (SELECT auth.uid())::text);

DROP POLICY IF EXISTS "documents_update_own" ON storage.objects;
CREATE POLICY "documents_update_own" ON storage.objects
    FOR UPDATE TO authenticated
    USING (bucket_id = 'documents' AND (storage.foldername(name))[1] = (SELECT auth.uid())::text)
    WITH CHECK (bucket_id = 'documents' AND (storage.foldername(name))[1] = (SELECT auth.uid())::text);

DROP POLICY IF EXISTS "documents_delete_own" ON storage.objects;
CREATE POLICY "documents_delete_own" ON storage.objects
    FOR DELETE TO authenticated
    USING (bucket_id = 'documents' AND (storage.foldername(name))[1] = (SELECT auth.uid())::text);
