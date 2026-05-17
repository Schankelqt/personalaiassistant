-- Workspace (forum supergroup) + per-agent forum topics

ALTER TABLE users
  ADD COLUMN IF NOT EXISTS telegram_workspace_chat_id BIGINT,
  ADD COLUMN IF NOT EXISTS workspace_linked_at TIMESTAMPTZ;

CREATE TABLE agent_topics (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
  agent_id UUID NOT NULL REFERENCES agents (id) ON DELETE CASCADE,
  workspace_chat_id BIGINT NOT NULL,
  telegram_thread_id BIGINT NOT NULL,
  topic_title TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (workspace_chat_id, telegram_thread_id)
);

CREATE INDEX idx_agent_topics_user ON agent_topics (user_id);
CREATE INDEX idx_agent_topics_agent ON agent_topics (agent_id) WHERE status = 'active';
CREATE INDEX idx_agent_topics_lookup ON agent_topics (workspace_chat_id, telegram_thread_id)
  WHERE status = 'active';
