-- Mandanten
CREATE TABLE IF NOT EXISTS tenants (
  id BIGSERIAL PRIMARY KEY,
  slug TEXT UNIQUE NOT NULL,           -- z.B. "emerald", "kunde-a"
  name TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- Mapping: Telegram-Gruppen -> Mandant
CREATE TABLE IF NOT EXISTS tenant_groups (
  tenant_id BIGINT REFERENCES tenants(id) ON DELETE CASCADE,
  chat_id   BIGINT NOT NULL,
  title     TEXT,
  PRIMARY KEY (tenant_id, chat_id)
);

-- Optional: Benutzer-Mapping pro Mandant (für spätere Rollen)
CREATE TABLE IF NOT EXISTS tenant_users (
  tenant_id BIGINT REFERENCES tenants(id) ON DELETE CASCADE,
  user_id   BIGINT NOT NULL,
  role      TEXT DEFAULT 'member',     -- owner|admin|agent|member
  PRIMARY KEY (tenant_id, user_id)
);

-- Bestehende Tabellen erweitern
ALTER TABLE support_tickets ADD COLUMN IF NOT EXISTS tenant_id BIGINT;
ALTER TABLE support_messages ADD COLUMN IF NOT EXISTS tenant_id BIGINT;
ALTER TABLE kb_articles     ADD COLUMN IF NOT EXISTS tenant_id BIGINT;
ALTER TABLE group_settings  ADD COLUMN IF NOT EXISTS tenant_id BIGINT;

-- Fremdschlüssel (nullable bis Daten migriert)
ALTER TABLE support_tickets
  ADD CONSTRAINT fk_tickets_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE SET NULL;
ALTER TABLE support_messages
  ADD CONSTRAINT fk_msgs_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE SET NULL;
ALTER TABLE kb_articles
  ADD CONSTRAINT fk_kb_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE SET NULL;
ALTER TABLE group_settings
  ADD CONSTRAINT fk_gs_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE SET NULL;

-- Performance/Isolation
CREATE INDEX IF NOT EXISTS idx_tickets_tenant ON support_tickets(tenant_id);
CREATE INDEX IF NOT EXISTS idx_msgs_tenant    ON support_messages(tenant_id);
CREATE INDEX IF NOT EXISTS idx_kb_tenant      ON kb_articles(tenant_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_group_settings_tenant_chat
  ON group_settings(tenant_id, chat_id);
