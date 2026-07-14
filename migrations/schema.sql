-- Create custom enums if they do not exist
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'memory_type_enum') THEN
        CREATE TYPE memory_type_enum AS ENUM (
            'ARCHITECTURAL_DECISION',
            'IMPLEMENTATION_RATIONALE',
            'FAILED_APPROACH',
            'BUG_RESOLUTION',
            'DESIGN_TRADEOFF',
            'COMPONENT_RELATIONSHIP',
            'CONSTRAINT'
        );
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'relationship_type_enum') THEN
        CREATE TYPE relationship_type_enum AS ENUM (
            'INFLUENCED',
            'REJECTED_IN_FAVOR_OF',
            'FIXES',
            'APPLIES_TO',
            'TOUCHES',
            'PRODUCED'
        );
    END IF;
END$$;

-- Create projects table
CREATE TABLE IF NOT EXISTS projects (
    id UUID PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    repo_path TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    config JSONB DEFAULT '{}'::jsonb
);

-- Create sessions table
CREATE TABLE IF NOT EXISTS sessions (
    id UUID PRIMARY KEY,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    started_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at TIMESTAMP WITH TIME ZONE,
    artifact_count INT DEFAULT 0,
    status VARCHAR(50) DEFAULT 'ACTIVE'
);

-- Create memories table
CREATE TABLE IF NOT EXISTS memories (
    id UUID PRIMARY KEY,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    session_id UUID REFERENCES sessions(id) ON DELETE SET NULL,
    memory_type memory_type_enum NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    rationale TEXT,
    components TEXT[] DEFAULT '{}',
    file_paths TEXT[] DEFAULT '{}',
    tags TEXT[] DEFAULT '{}',
    confidence_score DOUBLE PRECISION DEFAULT 1.0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    is_superseded BOOLEAN DEFAULT FALSE,
    superseded_by UUID REFERENCES memories(id) ON DELETE SET NULL
);

-- Create memory_relationships table
CREATE TABLE IF NOT EXISTS memory_relationships (
    id UUID PRIMARY KEY,
    source_memory_id UUID NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    target_memory_id UUID NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    relationship_type relationship_type_enum NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT unique_relationship UNIQUE (source_memory_id, target_memory_id, relationship_type)
);

-- Create session_log table for tracking processed artifacts/shas to prevent duplicate processing
CREATE TABLE IF NOT EXISTS session_log (
    id SERIAL PRIMARY KEY,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    session_id UUID REFERENCES sessions(id) ON DELETE SET NULL,
    processed_sha VARCHAR(40),
    artifact_hash VARCHAR(64) UNIQUE, -- SHA-256 hash of content for deduplication
    processed_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_memories_project_id ON memories(project_id);
CREATE INDEX IF NOT EXISTS idx_memories_memory_type ON memories(memory_type);
CREATE INDEX IF NOT EXISTS idx_sessions_project_id ON sessions(project_id);
CREATE INDEX IF NOT EXISTS idx_session_log_project_id ON session_log(project_id);
CREATE INDEX IF NOT EXISTS idx_session_log_processed_sha ON session_log(processed_sha);
