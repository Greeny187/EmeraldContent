-- Emerald Support Bot v1.0 Schema
-- Komplettes Support-System mit Tickets, Messages, KB und Group Settings

CREATE TABLE IF NOT EXISTS support_users (
  user_id BIGINT PRIMARY KEY,
  handle TEXT,
  first_name TEXT,
  last_name TEXT,
  tier TEXT DEFAULT 'free',
  locale TEXT DEFAULT 'de',
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TYPE support_ticket_status AS ENUM ('neu','in_bearbeitung','warten','geloest','archiv');

CREATE TABLE IF NOT EXISTS support_tickets (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT REFERENCES support_users(user_id) ON DELETE CASCADE,
  tenant_id BIGINT,
  channel TEXT NOT NULL DEFAULT 'telegram',
  category TEXT NOT NULL DEFAULT 'allgemein',
  subject TEXT NOT NULL,
  priority TEXT NOT NULL DEFAULT 'normal',
  status support_ticket_status NOT NULL DEFAULT 'neu',
  assignee_id BIGINT,
  sla_due_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  closed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS support_messages (
  id BIGSERIAL PRIMARY KEY,
  ticket_id BIGINT REFERENCES support_tickets(id) ON DELETE CASCADE,
  tenant_id BIGINT,
  author_user_id BIGINT,
  is_public BOOLEAN NOT NULL DEFAULT TRUE,
  text TEXT,
  attachments JSONB DEFAULT '[]',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS kb_articles (
  id BIGSERIAL PRIMARY KEY,
  tenant_id BIGINT,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  tags TEXT[] DEFAULT '{}',
  score INT DEFAULT 0,
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS group_settings (
  id BIGSERIAL PRIMARY KEY,
  chat_id BIGINT NOT NULL,
  tenant_id BIGINT,
  title TEXT,
  data JSONB DEFAULT '{}',
  updated_by BIGINT,
  updated_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(chat_id)
);

CREATE TABLE IF NOT EXISTS group_daily_agg (
  id BIGSERIAL PRIMARY KEY,
  chat_id BIGINT NOT NULL,
  day_date DATE NOT NULL,
  messages INT DEFAULT 0,
  active INT DEFAULT 0,
  joins INT DEFAULT 0,
  leaves INT DEFAULT 0,
  kicks INT DEFAULT 0,
  reply_p90_ms INT DEFAULT 0,
  spam_actions INT DEFAULT 0,
  UNIQUE(chat_id, day_date)
);

-- Indexes für Performance
CREATE INDEX IF NOT EXISTS idx_support_tickets_user ON support_tickets(user_id);
CREATE INDEX IF NOT EXISTS idx_support_tickets_status ON support_tickets(status);
CREATE INDEX IF NOT EXISTS idx_support_tickets_tenant ON support_tickets(tenant_id);
CREATE INDEX IF NOT EXISTS idx_support_messages_ticket ON support_messages(ticket_id);
CREATE INDEX IF NOT EXISTS idx_support_messages_tenant ON support_messages(tenant_id);
CREATE INDEX IF NOT EXISTS idx_kb_title_trgm ON kb_articles USING gin (title gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_kb_body_trgm ON kb_articles USING gin (body gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_group_settings_chat ON group_settings(chat_id);
CREATE INDEX IF NOT EXISTS idx_group_daily_agg_chat ON group_daily_agg(chat_id, day_date DESC);