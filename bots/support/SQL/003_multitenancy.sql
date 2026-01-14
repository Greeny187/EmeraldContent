-- Emerald Support Bot - Multi-Tenancy Schema (v1.0)
-- Erlaubt Nutzern ihren eigenen Support Bot zu hosten

CREATE TABLE IF NOT EXISTS tenants (
  id BIGSERIAL PRIMARY KEY,
  slug TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- Mapping: Telegram-Gruppen -> Mandant
CREATE TABLE IF NOT EXISTS tenant_groups (
  tenant_id BIGINT REFERENCES tenants(id) ON DELETE CASCADE,
  chat_id BIGINT NOT NULL,
  title TEXT,
  PRIMARY KEY (tenant_id, chat_id)
);

-- Optional: Benutzer-Rollen pro Mandant
CREATE TABLE IF NOT EXISTS tenant_users (
  tenant_id BIGINT REFERENCES tenants(id) ON DELETE CASCADE,
  user_id BIGINT NOT NULL,
  role TEXT DEFAULT 'member',
  created_at TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (tenant_id, user_id)
);

-- Fremdschlüssel für bestehende Tabellen (nullable bis migriert)
ALTER TABLE support_tickets
  ADD CONSTRAINT fk_tickets_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE SET NULL;

ALTER TABLE support_messages
  ADD CONSTRAINT fk_msgs_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE SET NULL;

ALTER TABLE kb_articles
  ADD CONSTRAINT fk_kb_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE SET NULL;

ALTER TABLE group_settings
  ADD CONSTRAINT fk_gs_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE SET NULL;

-- Indexes
CREATE INDEX IF NOT EXISTS idx_tenants_slug ON tenants(slug);
CREATE INDEX IF NOT EXISTS idx_tenant_groups_chat ON tenant_groups(chat_id);
CREATE INDEX IF NOT EXISTS idx_tenant_users_user ON tenant_users(user_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_group_settings_tenant_chat
  ON group_settings(tenant_id, chat_id);

-- Default Emerald Tenant
INSERT INTO tenants (slug, name) VALUES ('emerald', 'Emerald Support') ON CONFLICT DO NOTHING;
