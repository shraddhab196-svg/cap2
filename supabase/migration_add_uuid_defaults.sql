-- Migration: add UUID defaults for existing tables
-- Run this in Supabase SQL Editor against the existing database.

ALTER TABLE public.users
    ALTER COLUMN id SET DEFAULT gen_random_uuid();

ALTER TABLE public.cover_letters
    ALTER COLUMN id SET DEFAULT gen_random_uuid();

ALTER TABLE public.style_profiles
    ALTER COLUMN id SET DEFAULT gen_random_uuid();
