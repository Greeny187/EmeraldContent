
-- =========================================================
-- Emerald Crossposter – Multi-Tenancy Schema (v0.1-mt)
-- =========================================================

-- Mandanten
CREATE TABLE IF NOT EXISTS tenants (
  id           BIGSERIAL PRIMARY KEY,
  name         TEXT        NOT NULL,
  slug         TEXT        UNIQUE NOT NULL,
  api_key      TEXT        UNIQUE,                -- optional für externe Zugriffe
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Mitgliedschaften (User <-> Tenant)
CREATE TABLE IF NOT EXISTS tenant_members (
  tenant_id    BIGINT      NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  user_id      BIGINT      NOT NULL,
  role         TEXT        NOT NULL DEFAULT 'member', -- 'owner' | 'admin' | 'member'
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (tenant_id, user_id)
);

-- Routen (mandantenfähig)
CREATE TABLE IF NOT EXISTS crossposter_routes (
  id                BIGSERIAL PRIMARY KEY,
  tenant_id         BIGINT      NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  owner_user_id     BIGINT      NOT NULL,
  source_chat_id    BIGINT      NOT NULL,
  destinations      JSONB       NOT NULL,   -- [{ "type":"telegram", "chat_id":-100123 }, ...]
  active            BOOLEAN     NOT NULL DEFAULT TRUE,
  transform         JSONB       NOT NULL DEFAULT '{}'::jsonb, -- {"prefix":"","suffix":"","plain_text":false}
  filters           JSONB       NOT NULL DEFAULT '{}'::jsonb, -- {"hashtags_whitelist":[],"hashtags_blacklist":[]}
  schedule_cron     TEXT        NULL,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Logs (mandantenfähig)
CREATE TABLE IF NOT EXISTS crossposter_logs (
  id                 BIGSERIAL PRIMARY KEY,
  tenant_id          BIGINT      NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  route_id           BIGINT      NOT NULL REFERENCES crossposter_routes(id) ON DELETE CASCADE,
  source_chat_id     BIGINT      NOT NULL,
  source_message_id  BIGINT      NULL,
  dest_descriptor    JSONB       NOT NULL,
  status             TEXT        NOT NULL,   -- 'sent' | 'skipped' | 'error'
  error              TEXT        NULL,
  dedup_hash         TEXT        NULL,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Optionaler Admin-Cache
CREATE TABLE IF NOT EXISTS tg_admin_rights (
  user_id    BIGINT      NOT NULL,
  chat_id    BIGINT      NOT NULL,
  is_admin   BOOLEAN     NOT NULL,
  checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (user_id, chat_id)
);
