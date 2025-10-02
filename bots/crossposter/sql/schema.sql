
-- Emerald Crossposter v0.3 – Multi-Tenant + X + Discord

CREATE TABLE IF NOT EXISTS tenants (
  id BIGSERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  slug TEXT UNIQUE NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tenant_members (
  tenant_id BIGINT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  user_id BIGINT NOT NULL,
  role TEXT NOT NULL DEFAULT 'owner',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (tenant_id, user_id)
);

CREATE TABLE IF NOT EXISTS crossposter_routes (
  id BIGSERIAL PRIMARY KEY,
  tenant_id BIGINT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  owner_user_id BIGINT NOT NULL,
  source_chat_id BIGINT NOT NULL,
  destinations JSONB NOT NULL,          -- [{type:'telegram',chat_id:-100...}|{type:'x'}|{type:'discord',webhook_url:'...'}]
  active BOOLEAN NOT NULL DEFAULT TRUE,
  transform JSONB NOT NULL DEFAULT '{}'::jsonb, -- {prefix:'',suffix:'',plain_text:false}
  filters JSONB NOT NULL DEFAULT '{}'::jsonb,   -- {hashtags_whitelist:[],hashtags_blacklist:[]}
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS crossposter_logs (
  id BIGSERIAL PRIMARY KEY,
  tenant_id BIGINT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  route_id BIGINT NOT NULL REFERENCES crossposter_routes(id) ON DELETE CASCADE,
  source_chat_id BIGINT NOT NULL,
  source_message_id BIGINT,
  dest_descriptor JSONB NOT NULL,
  status TEXT NOT NULL,                 -- sent|error|skipped
  error TEXT,
  dedup_hash TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tg_admin_rights (
  user_id BIGINT NOT NULL,
  chat_id BIGINT NOT NULL,
  is_admin BOOLEAN NOT NULL,
  checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (user_id, chat_id)
);

-- External connectors (for X user tokens etc.)
CREATE TABLE IF NOT EXISTS connectors (
  id BIGSERIAL PRIMARY KEY,
  tenant_id BIGINT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  type TEXT NOT NULL,                   -- 'x' | future: 'mastodon', etc.
  label TEXT,
  config JSONB NOT NULL,                -- {'access_token':'...'}
  active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_connectors_tenant_type ON connectors(tenant_id, type) WHERE active=TRUE;
