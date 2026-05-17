-- Replace FAQ-style system seeds with working dialogue context.
-- Full text is maintained in personal_ai_os/knowledge/working_context.py
-- and applied on bot startup via sync_working_context().

DELETE FROM knowledge_entries WHERE scope = 'system' AND user_id IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_knowledge_system_title
  ON knowledge_entries (category, title)
  WHERE scope = 'system' AND user_id IS NULL;
