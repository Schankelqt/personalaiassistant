-- Personal AI OS — initial schema (Supabase / Postgres 15+)

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  telegram_id BIGINT UNIQUE NOT NULL,
  username TEXT,
  full_name TEXT,
  plan TEXT NOT NULL DEFAULT 'free' CHECK (plan IN ('free', 'personal', 'pro', 'business')),
  daily_token_limit INTEGER NOT NULL DEFAULT 50000,
  daily_tokens_used INTEGER NOT NULL DEFAULT 0,
  token_balance INTEGER NOT NULL DEFAULT 0,
  timezone TEXT NOT NULL DEFAULT 'UTC',
  reminder_hour SMALLINT NOT NULL DEFAULT 9 CHECK (reminder_hour >= 0 AND reminder_hour <= 23),
  meeting_reminder_minutes INTEGER NOT NULL DEFAULT 15 CHECK (meeting_reminder_minutes >= 0 AND meeting_reminder_minutes <= 1440),
  language TEXT NOT NULL DEFAULT 'ru',
  pending_plan TEXT CHECK (pending_plan IN ('free', 'personal', 'pro', 'business')),
  plan_expires_at TIMESTAMPTZ,
  referral_rewarded_at TIMESTAMPTZ,
  onboarding_complete BOOLEAN NOT NULL DEFAULT false,
  referral_code TEXT UNIQUE,
  referred_by UUID REFERENCES users (id) ON DELETE SET NULL,
  paddle_customer_id TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE agents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  agent_type TEXT NOT NULL,
  system_prompt TEXT NOT NULL,
  tools JSONB NOT NULL DEFAULT '[]',
  is_active BOOLEAN NOT NULL DEFAULT true,
  metadata JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE memory_entries (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
  entry_type TEXT NOT NULL CHECK (entry_type IN ('person', 'note')),
  name TEXT,
  birthday DATE,
  relation TEXT,
  tg_username TEXT,
  notes TEXT,
  tags TEXT[] NOT NULL DEFAULT '{}',
  content TEXT,
  expires_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE reminders (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
  reminder_type TEXT NOT NULL,
  ref_id UUID,
  trigger_at TIMESTAMPTZ NOT NULL,
  payload JSONB NOT NULL DEFAULT '{}',
  sent BOOLEAN NOT NULL DEFAULT false,
  sent_at TIMESTAMPTZ,
  celery_task_id TEXT
);

CREATE TABLE oauth_tokens (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
  provider TEXT NOT NULL CHECK (provider IN ('google', 'jira')),
  access_token_enc TEXT NOT NULL,
  refresh_token_enc TEXT NOT NULL,
  token_type TEXT NOT NULL DEFAULT 'Bearer',
  expires_at TIMESTAMPTZ,
  scope TEXT,
  jira_cloud_id TEXT,
  jira_base_url TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (user_id, provider)
);

CREATE TABLE token_logs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
  agent_id UUID REFERENCES agents (id) ON DELETE SET NULL,
  model TEXT,
  input_tokens INTEGER NOT NULL,
  output_tokens INTEGER NOT NULL,
  total_tokens INTEGER GENERATED ALWAYS AS (input_tokens + output_tokens) STORED,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE billing_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
  event_type TEXT NOT NULL,
  plan TEXT,
  amount_usd DECIMAL(10, 2),
  paddle_tx_id TEXT UNIQUE,
  metadata JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE interaction_history (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
  summary TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE message_feedback (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
  message_ref TEXT NOT NULL,
  score SMALLINT NOT NULL CHECK (score IN (-1, 1)),
  comment TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_users_telegram_id ON users (telegram_id);
CREATE INDEX idx_agents_user_id ON agents (user_id) WHERE is_active = true;
CREATE INDEX idx_memory_user_bday ON memory_entries (user_id, birthday)
  WHERE entry_type = 'person' AND birthday IS NOT NULL;
CREATE INDEX idx_reminders_trigger ON reminders (trigger_at) WHERE sent = false;
CREATE INDEX idx_token_logs_user_date ON token_logs (user_id, created_at);
CREATE INDEX idx_oauth_user_provider ON oauth_tokens (user_id, provider);
CREATE INDEX idx_interaction_user ON interaction_history (user_id, created_at DESC);
CREATE INDEX idx_feedback_user_date ON message_feedback (user_id, created_at DESC);

-- RLS: включите в Supabase при доступе к PostgREST клиентам; backend изолирует данные по user_id в SQL.
