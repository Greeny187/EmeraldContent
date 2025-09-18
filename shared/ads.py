from __future__ import annotations
import os
import random
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional, List

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, Application, filters

# interne Imports aus eurer Codebase
from bots.content.database import (
    _with_cursor,
    get_registered_groups,      # (chat_id, title)
    get_group_language,         # chat -> 'de'/'en'/...
)

# ---------------------------
# Developer-Whitelist / Utils
# ---------------------------

DEV_IDS = {int(x) for x in os.getenv("DEVELOPER_IDS", "").split(",") if x.strip().isdigit()}

def is_developer(user_id: Optional[int]) -> bool:
    return user_id is not None and user_id in DEV_IDS

def _tz_now(tz: str = "Europe/Berlin") -> datetime:
    return datetime.now(ZoneInfo(tz))

def _is_quiet_now(settings: dict, now_local: datetime) -> bool:
    """
    Quiet Hours je Chat (lokal gedacht).
    quiet_start_min .. quiet_end_min (Minute im Tag, 0..1439), wrap über Mitternacht möglich.
    """
    qs = int(settings.get("quiet_start_min", 1320))  # 22:00
    qe = int(settings.get("quiet_end_min", 360))     # 06:00
    mins = now_local.hour * 60 + now_local.minute
    if qs <= qe:
        return qs <= mins < qe
    return mins >= qs or mins < qe

# -------------
# DB: SCHEMA/DDL
# -------------

@_with_cursor
def ensure_adv_schema(cur):
    # === Pro-Abo / Subscriptions je Chat ===
    cur.execute("""
        CREATE TABLE IF NOT EXISTS group_subscriptions (
          chat_id     BIGINT PRIMARY KEY,
          tier        TEXT NOT NULL DEFAULT 'free',     -- 'free' | 'pro' | 'pro_plus'
          valid_until TIMESTAMPTZ,                      -- NULL oder in der Vergangenheit: nicht aktiv
          updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_groupsub_valid ON group_subscriptions(valid_until DESC);")
    # Chat-Einstellungen für Werbung
    cur.execute("""
        CREATE TABLE IF NOT EXISTS adv_settings (
          chat_id           BIGINT PRIMARY KEY,
          adv_enabled       BOOLEAN NOT NULL DEFAULT TRUE,
          adv_topic_id      BIGINT NULL,                 -- None => Default-Topic
          min_gap_min       INT     NOT NULL DEFAULT 240, -- Mindestabstand in Minuten
          daily_cap         INT     NOT NULL DEFAULT 2,   -- maximale Ads pro Tag
          every_n_messages  INT     NOT NULL DEFAULT 0,   -- optional: nur nach N Messages
          label             TEXT    NOT NULL DEFAULT 'Anzeige',
          quiet_start_min   SMALLINT NOT NULL DEFAULT 1320, -- 22*60
          quiet_end_min     SMALLINT NOT NULL DEFAULT 360,  -- 06*60
          last_adv_ts       TIMESTAMPTZ NULL
        );
    """)
    # Kampagnen (global)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS adv_campaigns (
          campaign_id   BIGSERIAL PRIMARY KEY,
          title         TEXT,
          body_text     TEXT,
          media_url     TEXT,      -- optional (Bild)
          link_url      TEXT,      -- CTA-URL
          cta_label     TEXT DEFAULT 'Mehr erfahren',
          enabled       BOOLEAN NOT NULL DEFAULT TRUE,
          weight        INT NOT NULL DEFAULT 1,
          start_ts      TIMESTAMPTZ NULL,
          end_ts        TIMESTAMPTZ NULL,
          created_by    BIGINT,
          created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)
    # Impressionen/Versand-Log
    cur.execute("""
        CREATE TABLE IF NOT EXISTS adv_impressions (
          chat_id     BIGINT,
          campaign_id BIGINT,
          message_id  BIGINT,
          ts          TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_adv_impr_chat_ts ON adv_impressions(chat_id, ts DESC);")

    # Für Message-basierte Trigger: stellt sicher, dass message_logs passt
    cur.execute("ALTER TABLE message_logs ADD COLUMN IF NOT EXISTS topic_id BIGINT;")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_msglogs_topic_user_ts ON message_logs(chat_id, topic_id, user_id, timestamp DESC);")

# --------------------------------
# DB: Settings / Kampagnen-Helper
# --------------------------------

@_with_cursor
def set_adv_topic(cur, chat_id: int, topic_id: Optional[int]):
    cur.execute("""
      INSERT INTO adv_settings (chat_id, adv_topic_id)
      VALUES (%s, %s)
      ON CONFLICT (chat_id) DO UPDATE SET adv_topic_id=EXCLUDED.adv_topic_id;
    """, (chat_id, topic_id))

@_with_cursor
def get_adv_settings(cur, chat_id: int) -> dict:
    cur.execute("""
      SELECT adv_enabled, adv_topic_id, min_gap_min, daily_cap, every_n_messages,
             label, quiet_start_min, quiet_end_min, last_adv_ts
        FROM adv_settings WHERE chat_id=%s;
    """, (chat_id,))
    r = cur.fetchone()
    if not r:
        # Defaults, wenn nie gesetzt
        return {
            "adv_enabled": True, "adv_topic_id": None, "min_gap_min": 240,
            "daily_cap": 2, "every_n_messages": 0, "label": "Anzeige",
            "quiet_start_min": 1320, "quiet_end_min": 360, "last_adv_ts": None,
        }
    (en, tid, gap, cap, nmsg, label, qs, qe, last_ts) = r
    return {
        "adv_enabled": en, "adv_topic_id": tid, "min_gap_min": gap,
        "daily_cap": cap, "every_n_messages": nmsg, "label": label,
        "quiet_start_min": qs, "quiet_end_min": qe, "last_adv_ts": last_ts,
    }

@_with_cursor
def set_adv_settings(cur, chat_id: int, **fields):
    allowed = {"adv_enabled","min_gap_min","daily_cap","every_n_messages","label","quiet_start_min","quiet_end_min"}
    cols, vals = [], []
    for k,v in fields.items():
        if k in allowed:
            cols.append(f"{k}=%s"); vals.append(v)
    if not cols: return
    cur.execute(f"""
      INSERT INTO adv_settings (chat_id) VALUES (%s)
      ON CONFLICT (chat_id) DO UPDATE SET {", ".join(cols)};
    """, (chat_id, *vals))

@_with_cursor
def add_campaign(cur, title: str, body_text: str, link_url: str,
                 media_url: Optional[str] = None, cta_label: str = "Mehr erfahren",
                 weight: int = 1, start_ts: Optional[datetime] = None,
                 end_ts: Optional[datetime] = None, created_by: Optional[int] = None) -> int:
    cur.execute("""
      INSERT INTO adv_campaigns (title, body_text, media_url, link_url, cta_label, weight, start_ts, end_ts, created_by)
      VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
      RETURNING campaign_id;
    """, (title, body_text, media_url, link_url, cta_label, weight, start_ts, end_ts, created_by))
    return cur.fetchone()[0]

@_with_cursor
def list_active_campaigns(cur):
    cur.execute("""
      SELECT campaign_id, title, body_text, media_url, link_url, cta_label, weight
        FROM adv_campaigns
       WHERE enabled=TRUE
         AND (start_ts IS NULL OR NOW() >= start_ts)
         AND (end_ts   IS NULL OR NOW() <= end_ts)
    """)
    return cur.fetchall() or []

@_with_cursor
def record_impression(cur, chat_id: int, campaign_id: int, message_id: int):
    cur.execute("INSERT INTO adv_impressions (chat_id, campaign_id, message_id) VALUES (%s,%s,%s);",
                (chat_id, campaign_id, message_id))

@_with_cursor
def update_last_adv_ts(cur, chat_id: int):
    cur.execute("UPDATE adv_settings SET last_adv_ts=NOW() AT TIME ZONE 'UTC' WHERE chat_id=%s;", (chat_id,))

@_with_cursor
def count_ads_today(cur, chat_id: int) -> int:
    # Heute in der lokalen Zeitzone des Chats
    cur.execute("""
        SELECT COUNT(*) FROM adv_impressions 
        WHERE chat_id=%s AND ts AT TIME ZONE 'Europe/Berlin' >= CURRENT_DATE AT TIME ZONE 'Europe/Berlin';
    """, (chat_id,))
    return int(cur.fetchone()[0])

@_with_cursor
def is_pro_chat(cur, chat_id: int) -> bool:
    cur.execute("SELECT tier, valid_until FROM group_subscriptions WHERE chat_id=%s;", (chat_id,))
    r = cur.fetchone()
    if not r:
        return False
    tier, until = r
    if tier not in ("pro", "pro_plus"):
        return False
    if until is None:
        return True
    return until > datetime.now(ZoneInfo("UTC"))  # UTC verwenden

@_with_cursor
def set_pro_until(cur, chat_id: int, until: datetime | None, tier: str = "pro"):
    if until is not None and until <= datetime.now(ZoneInfo("UTC")):  # UTC verwenden
        cur.execute("""
            INSERT INTO group_subscriptions (chat_id, tier, valid_until, updated_at)
            VALUES (%s, 'free', NULL, NOW())
            ON CONFLICT (chat_id) DO UPDATE SET tier='free', valid_until=NULL, updated_at=NOW();
        """, (chat_id,))
        return
    cur.execute("""
        INSERT INTO group_subscriptions (chat_id, tier, valid_until, updated_at)
        VALUES (%s, %s, %s, NOW())
        ON CONFLICT (chat_id) DO UPDATE SET tier=EXCLUDED.tier, valid_until=EXCLUDED.valid_until, updated_at=NOW();
    """, (chat_id, tier, until))

@_with_cursor
def get_subscription_info(cur, chat_id: int) -> dict:
    cur.execute("SELECT tier, valid_until FROM group_subscriptions WHERE chat_id=%s;", (chat_id,))
    r = cur.fetchone()
    if not r:
        return {"tier": "free", "valid_until": None, "active": False}
    tier, until = r
    active = tier in ("pro","pro_plus") and (until is None or until > datetime.now(ZoneInfo("UTC")))
    return {"tier": tier, "valid_until": until, "active": active}

@_with_cursor
def messages_since(cur, chat_id: int, since_ts: datetime) -> int:
    cur.execute("SELECT COUNT(*) FROM message_logs WHERE chat_id=%s AND timestamp > %s;", (chat_id, since_ts))
    return int(cur.fetchone()[0])

@_with_cursor
def list_candidate_chats_for_ads(cur) -> List[int]:
    cur.execute("SELECT DISTINCT chat_id FROM message_logs WHERE timestamp > NOW() - INTERVAL '30 days';")
    return [r[0] for r in (cur.fetchall() or [])]

# -------------------------
# Commands (Developer only)
# -------------------------

async def set_adv_topic_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_developer(user.id):
        return await update.effective_message.reply_text("Nur Entwickler: Zugriff verweigert.")
    chat = update.effective_chat
    tid = getattr(update.effective_message, "message_thread_id", None)  # None => Default-Topic
    set_adv_topic(chat.id, tid)
    where = f"Topic {tid}" if tid is not None else "Default-Topic"
    await update.effective_message.reply_text(f"✅ Werbe-Topic gesetzt: {where}")

async def ad_add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_developer(update.effective_user.id):
        return await update.effective_message.reply_text("Nur Entwickler: Zugriff verweigert.")
    # Format: /ad_add <Titel> || <Text> || <Link> [|| <BildURL>] [|| <CTA>] [|| <Weight>]
    raw = " ".join(context.args or [])
    parts = [p.strip() for p in raw.split("||")]
    if len(parts) < 3:
        return await update.effective_message.reply_text(
            "Format:\n/ad_add Titel || Text || Link [|| BildURL] [|| CTA] [|| Weight]"
        )
    title, body, link = parts[0], parts[1], parts[2]
    media = parts[3] if len(parts) >= 4 and parts[3] else None
    cta   = parts[4] if len(parts) >= 5 and parts[4] else "Mehr erfahren"
    try:
        weight = int(parts[5]) if len(parts) >= 6 and parts[5] else 1
    except:
        weight = 1
    cid = add_campaign(title, body, link, media, cta, weight=weight, created_by=update.effective_user.id)
    await update.effective_message.reply_text(f"✅ Kampagne #{cid} gespeichert & aktiv.")

async def ad_list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_developer(update.effective_user.id):
        return await update.effective_message.reply_text("Nur Entwickler: Zugriff verweigert.")
    rows = list_active_campaigns()
    if not rows:
        return await update.effective_message.reply_text("Keine aktiven Kampagnen.")
    lines = [f"#{cid} • {title} • weight={w} • link={link}" for (cid,title,body,media,link,cta,w) in rows]
    await update.effective_message.reply_text("Aktive Kampagnen:\n" + "\n".join(lines))

async def ad_set_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /ad_set gap=240 cap=2 nmsgs=0 label=Anzeige quiet=22:00-06:00 enabled=on|off
    Nur Developer.
    """
    if not is_developer(update.effective_user.id):
        return await update.effective_message.reply_text("Nur Entwickler: Zugriff verweigert.")
    chat = update.effective_chat
    args = context.args or []
    params = {k: v for a in args if "=" in a for k, v in [a.split("=", 1)]}
    fields = {}
    if "gap" in params:
        fields["min_gap_min"] = max(0, int(params["gap"]))
    if "cap" in params:
        fields["daily_cap"] = max(0, int(params["cap"]))
    if "nmsgs" in params:
        fields["every_n_messages"] = max(0, int(params["nmsgs"]))
    if "label" in params:
        fields["label"] = params["label"].strip()[:40]
    if "quiet" in params and "-" in params["quiet"]:
        a,b = params["quiet"].split("-",1)
        try:
            ah, am = [int(x) for x in a.split(":")]
            bh, bm = [int(x) for x in b.split(":")]
            fields["quiet_start_min"] = ah*60 + am
            fields["quiet_end_min"]   = bh*60 + bm
        except:
            pass
    if "enabled" in params:
        fields["adv_enabled"] = params["enabled"].lower() in ("on","true","1","yes","y")

    if not fields:
        s = get_adv_settings(chat.id)
        return await update.effective_message.reply_text(
            "Aktuelle Ads-Einstellungen:\n"
            f"- enabled: {s['adv_enabled']}\n"
            f"- gap: {s['min_gap_min']} min\n"
            f"- daily cap: {s['daily_cap']}\n"
            f"- every_n_messages: {s['every_n_messages']}\n"
            f"- label: {s['label']}\n"
            f"- quiet: {s['quiet_start_min']//60:02d}:{s['quiet_start_min']%60:02d}"
            f"-{s['quiet_end_min']//60:02d}:{s['quiet_end_min']%60:02d}"
        )

    set_adv_settings(chat.id, **fields)
    await update.effective_message.reply_text("✅ Einstellungen gespeichert.")

async def pro_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_developer(update.effective_user.id):
        return await update.effective_message.reply_text("Nur Entwickler: Zugriff verweigert.")
    chat = update.effective_chat
    args = context.args or []

    if not args:
        info = get_subscription_info(chat.id)
        until_str = info["valid_until"].strftime("%Y-%m-%d %H:%M:%S") if info["valid_until"] else "unbegrenzt/aus"
        return await update.effective_message.reply_text(
            f"Abo-Status:\n"
            f"- tier: {info['tier']}\n- aktiv: {info['active']}\n- gültig bis: {until_str}"
        )

    # Formate:
    # /pro on [tier=pro|pro_plus] [days=30]
    # /pro off
    # /pro until=2025-12-31 23:59
    sub = args[0].lower()
    if sub == "off":
        set_pro_until(chat.id, None, tier="free")
        return await update.effective_message.reply_text("✅ Pro-Abo deaktiviert (free).")

    if sub == "on":
        tier = "pro"
        days = 30
        for a in args[1:]:
            if a.startswith("tier="):
                tier = a.split("=",1)[1].strip()
            if a.startswith("days="):
                try:
                    days = int(a.split("=",1)[1])
                except:
                    pass
        until = datetime.utcnow() + timedelta(days=days)
        set_pro_until(chat.id, until, tier=tier)
        return await update.effective_message.reply_text(f"✅ Pro-Abo aktiv: {tier} bis {until:%Y-%m-%d %H:%M} UTC")

    m = None
    for a in args:
        if a.startswith("until="):
            m = a.split("=",1)[1].strip()
            break
    if m:
        # erlaubte Formate: YYYY-MM-DD oder YYYY-MM-DD HH:MM
        try:
            if re.match(r"^\d{4}-\d{2}-\d{2}$", m):
                dt = datetime.strptime(m, "%Y-%m-%d")
            else:
                dt = datetime.strptime(m, "%Y-%m-%d %H:%M")
            set_pro_until(chat.id, dt, tier="pro")
            return await update.effective_message.reply_text(f"✅ Pro-Abo bis {dt:%Y-%m-%d %H:%M} UTC")
        except Exception:
            return await update.effective_message.reply_text("Ungültiges Datum. Erlaubt: YYYY-MM-DD oder YYYY-MM-DD HH:MM")
    return await update.effective_message.reply_text("Nutzung: /pro on [tier=pro|pro_plus] [days=30] | /pro off | /pro until=YYYY-MM-DD[ HH:MM]")

# -----------------
# Scheduler / Logic
# -----------------

async def ad_scheduler(context: ContextTypes.DEFAULT_TYPE):
    """
    Läuft periodisch (z. B. alle 15 Min). Postet maximal 1 Anzeige je Chat,
    wenn alle Regeln erfüllt sind.
    """
    try:
        chats = [cid for (cid, _) in get_registered_groups()]
    except Exception:
        # Fallback: Chats aus message_logs
        chats = list_candidate_chats_for_ads()

    campaigns = list_active_campaigns()
    if not campaigns:
        return

    # Gewichteten Pool vorbereiten
    weighted_pool = []
    for (cid, title, body, media, link, cta, w) in campaigns:
        weighted_pool += [(cid, title, body, media, link, cta)] * max(1, int(w))

    if not weighted_pool:
        return

    now_local = _tz_now()

    for chat_id in chats:
        try:
            # Pro-Abo schaltet Werbung aus
            if is_pro_chat(chat_id):
                continue
                
            s = get_adv_settings(chat_id)
            if not s["adv_enabled"]:
                continue

            # Quiet Hours Check
            if _is_quiet_now(s, now_local):
                continue

            # Tages-Cap
            if count_ads_today(chat_id) >= s["daily_cap"]:
                continue

            # Zeitlicher Gap
            last = s["last_adv_ts"]
            if last and (datetime.utcnow() - last) < timedelta(minutes=s["min_gap_min"]):
                continue

            # Nachrichten-Gap
            nmsgs = s["every_n_messages"]
            if nmsgs > 0 and last:
                if messages_since(chat_id, last) < nmsgs:
                    continue

            # Kampagne auswählen
            camp_id, title, body, media, link, cta = random.choice(weighted_pool)

            # Caption
            label = s["label"] or "Anzeige"
            caption = f"📣 <b>{label}</b> — <b>{title}</b>\n{body}\n\n#ad"

            # Button
            kb = InlineKeyboardMarkup([[InlineKeyboardButton(cta or "Mehr erfahren", url=link)]]) if link else None

            # Ziel-Topic
            topic_id = s["adv_topic_id"]  # None => Default-Topic

            # Senden
            if media:
                msg = await context.bot.send_photo(
                    chat_id=chat_id, photo=media, caption=caption,
                    message_thread_id=topic_id, parse_mode="HTML",
                    reply_markup=kb
                )
            else:
                msg = await context.bot.send_message(
                    chat_id=chat_id, text=caption,
                    message_thread_id=topic_id, parse_mode="HTML",
                    reply_markup=kb
                )

            record_impression(chat_id, camp_id, msg.message_id)
            update_last_adv_ts(chat_id)

        except Exception as e:
            print(f"[ads] Fehler in Chat {chat_id}: {e}")
            import traceback
            traceback.print_exc()

# -----------------------
# Öffentliche API/Setup
# -----------------------

def init_ads_schema():
    """Nach DB-Init aufrufen."""
    ensure_adv_schema()

def register_ads(app: Application):
    """Kommandos registrieren (nur Developer haben Zugriff)."""
    app.add_handler(CommandHandler("set_adv_topic", set_adv_topic_command, filters=filters.ChatType.GROUPS))
    app.add_handler(CommandHandler("ad_add",        ad_add_command))
    app.add_handler(CommandHandler("ad_list",       ad_list_command))
    app.add_handler(CommandHandler("ad_set",        ad_set_command))
    app.add_handler(CommandHandler("pro",           pro_command))  # <- Fehlte!

def register_ads_jobs(app: Application, interval_minutes: int = 15, first_seconds: int = 30):
    """Scheduler registrieren (periodischer Werbe-Check)."""
    app.job_queue.run_repeating(
        ad_scheduler,
        interval=timedelta(minutes=interval_minutes),
        first=first_seconds,
        name="ads"
    )
