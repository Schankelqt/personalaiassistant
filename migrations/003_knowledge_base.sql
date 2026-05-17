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

-- Product / onboarding context (editable in SQL; no user_id)
INSERT INTO knowledge_entries (scope, category, title, content, tags) VALUES
(
  'system',
  'product',
  'Personal AI OS — суть',
  'Telegram-бот с Meta-оркестратором и специализированными агентами. Изоляция: супергруппа с Topics, /link_workspace, топик на агента. Команды: /agents, /skills, /skill <id>, /workspace, /topic, /help.',
  ARRAY['product', 'overview']
),
(
  'system',
  'onboarding',
  'Цели онбординга',
  'Живой диалог: узнать имя, чем занимается человек, какие инструменты использует, что хочет автоматизировать, важные люди/даты (опционально). Не анкета — 3–6 реплик, уточняющие вопросы по ответам. Когда достаточно — вызови finish_onboarding.',
  ARRAY['onboarding']
),
(
  'system',
  'onboarding',
  'Тон онбординга',
  'По-русски, тепло, коротко. Один вопрос за раз. Реагируй на слова пользователя, не читай список вопросов. Можно лёгкий юмор. Если спешит — предложи «настроим за минуту: имя, работа, цель».',
  ARRAY['onboarding', 'tone']
),
(
  'system',
  'meta',
  'Цикл Meta-Agent',
  'Запрос пользователя → собранный контекст (профиль, база знаний, агенты, память) → ответ или делегирование route_to_agent. Не выдумывай факты вне контекста.',
  ARRAY['meta', 'workflow']
);
