
# Emerald Crossposter – v0.1 (Multi‑Tenant)

**Was ist drin?**
- Mandantenfähiges DB‑Schema (`sql/schema.sql`)
- FastAPI‑API mit Telegram WebApp initData‑Verify & Mandantenprüfung
- Telegram PTB‑Handler (`/crossposter`) + Worker‑Pipeline
- MiniApp UI (WebApp) `web/miniapp/crossposter.html`
- Minimaler DB‑Pool (`common/database.py`) & Test‑Starter `bot.py`

## Quickstart (lokal, Polling)
1) PostgreSQL bereitstellen und `DATABASE_URL` setzen.  
2) `sql/schema.sql` ausführen.  
3) `TELEGRAM_BOT_TOKEN` setzen.  
4) `pip install python-telegram-bot==21.* fastapi uvicorn asyncpg`  
5) `python bot.py` (separat ASGI starten: `uvicorn bots.content.miniapp_crossposter:API --port 8080`)

## Produktion
- Webhook + ASGI: MiniApp‑API unter `/miniapi` mounten, `CROSSPOSTER_MINIAPP_URL` auf deine GitHub‑Pages‑URL legen.
- Bot in Quelle(n) & Ziel(en) hinzufügen (mind. in Quelle Admin).
- Tenants/Mitgliedschaften mit Seeds befüllen (z. B. für dich als Owner).

## Multi‑Tenant
- Tabellen: `tenants`, `tenant_members`, `crossposter_routes`, `crossposter_logs`.
- Jede API‑Operation prüft: `tenant_members (tenant_id, user_id)`.
- MiniApp lädt deine Mandanten via `/tenants` und setzt `tenant_id` in Anfragen.

## Free vs. Pro (Server‑Gate, Idee)
- Free: 1 Route, 1 Ziel → vor INSERT `COUNT(*)` prüfen.
- Pro: 20 Routen, mehrere Ziele, erweiterte Statistik.

## ENV Variablen
- `TELEGRAM_BOT_TOKEN` – Pflicht
- `DATABASE_URL` – Pflicht
- `CROSSPOSTER_MINIAPP_URL` – WebApp‑URL (für Button)

Viel Spaß! 🤘
