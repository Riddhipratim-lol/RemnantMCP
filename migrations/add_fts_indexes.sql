-- Migration: Add GIN full-text search indexes
-- Run this against your existing Supabase database to apply the new indexes.
-- All statements use IF NOT EXISTS — safe to re-run on an already-migrated DB.

-- GIN index for full-text search across title + content + rationale.
-- This is required for the Layer 4 fallback search (plainto_tsquery / to_tsvector).
-- Without it, recall_context returns 0 results even when memories exist.
CREATE INDEX IF NOT EXISTS idx_memories_fts_title_content
    ON memories
    USING GIN (to_tsvector('english', coalesce(title, '') || ' ' || coalesce(content, '') || ' ' || coalesce(rationale, '')));

-- Composite B-tree index for the most common read pattern:
-- project_id + is_superseded + memory_type (used by list_decisions, get_failed_approaches, recall_context)
CREATE INDEX IF NOT EXISTS idx_memories_project_active
    ON memories(project_id, is_superseded, memory_type);
