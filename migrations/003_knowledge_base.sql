-- Knowledge base: system docs + per-user facts (context for Meta and onboarding)

CREATE TABLE knowledge_entries (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES users (id) ON DELETE CASCADE,
  scope TEXT NOT NULL CHECK (scope IN ('system', 'user')),
  category TEXT NOT NULL DEFAULT 'general',
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  tags TEXT[] NOT NULL DEFAULT '{}',
  metadata JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT knowledge_scope_user CHECK (
    (scope = 'system' AND user_id IS NULL) OR (scope = 'user' AND user_id IS NOT NULL)
  )
);

CREATE INDEX idx_knowledge_user_cat ON knowledge_entries (user_id, category)
  WHERE scope = 'user';
CREATE INDEX idx_knowledge_system ON knowledge_entries (category) WHERE scope = 'system';

CREATE UNIQUE INDEX IF NOT EXISTS idx_knowledge_system_title
  ON knowledge_entries (category, title)
  WHERE scope = 'system' AND user_id IS NULL;

-- Seeds: personal_ai_os/knowledge/working_context.py (sync on bot startup)
