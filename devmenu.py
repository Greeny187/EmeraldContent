import os
import re
import datetime
import sys
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from ads import register_ads
from patchnotes import __version__
from database import get_registered_groups, is_daily_stats_enabled, _db_pool, _with_cursor, add_campaign

try:
    import psutil
except Exception as e:
    psutil = None
    logging.warning("psutil nicht verfügbar: %s", e)

try:
    from ads import (
        list_active_campaigns, get_subscription_info, set_pro_until,
        get_adv_settings, set_adv_settings, set_adv_topic
    )
except Exception as e:
    logging.warning("ads-Modul nicht verfügbar: %s", e)
    def list_active_campaigns(): return []
    def get_subscription_info(_): return {"active": False, "valid_until": None}
    def set_pro_until(_chat_id, _until): raise RuntimeError("ads-API nicht verfügbar")
    def get_adv_settings(_chat_id):
        return {"adv_enabled": True, "adv_topic_id": None, "min_gap_min": 240,
                "daily_cap": 2, "every_n_messages": 0, "label": "Anzeige",
                "quiet_start_min": 1320, "quiet_end_min": 360, "last_adv_ts": None}
    def set_adv_settings(_chat_id, **fields): raise RuntimeError("ads-API nicht verfügbar")
    def set_adv_topic(_chat_id, _topic_id): raise RuntimeError("ads-API nicht verfügbar")

logger = logging.getLogger(__name__)

@_with_cursor
def _adv_list_all(cur, limit:int=20, offset:int=0):
    cur.execute("""
      SELECT campaign_id, title, body_text, media_url, link_url, cta_label, weight, enabled
        FROM adv_campaigns
       ORDER BY campaign_id DESC
       LIMIT %s OFFSET %s;
    """, (limit, offset))
    return cur.fetchall() or []

@_with_cursor
def _adv_set_enabled(cur, campaign_id:int, enabled:bool):
    cur.execute("UPDATE adv_campaigns SET enabled=%s WHERE campaign_id=%s;", (enabled, campaign_id))

@_with_cursor
def _adv_get(cur, campaign_id:int):
    cur.execute("""
      SELECT campaign_id, title, body_text, media_url, link_url, cta_label, weight, enabled
        FROM adv_campaigns WHERE campaign_id=%s;
    """, (campaign_id,))
    return cur.fetchone()

@_with_cursor
def _adv_update(cur, campaign_id:int, **fields):
    allowed = {"title","body_text","media_url","link_url","cta_label","weight"}
    sets, vals = [], []
    for k,v in fields.items():
        if k in allowed:
            sets.append(f"{k}=%s"); vals.append(v)
    if not sets: return
    vals.append(campaign_id)
    cur.execute(f"UPDATE adv_campaigns SET {', '.join(sets)} WHERE campaign_id=%s;", tuple(vals))

@_with_cursor
def _adv_soft_delete(cur, campaign_id:int):
    # Soft-Delete: deaktivieren + Endzeit setzen
    cur.execute("UPDATE adv_campaigns SET enabled=FALSE, end_ts=NOW() WHERE campaign_id=%s;", (campaign_id,))


def _dev_ids_from_env(user_id_hint: int | None = None) -> set[int]:
    """
    Liest DEVELOPER_CHAT_ID, DEVELOPER_CHAT_IDS, DEV_IDS (Komma/Leerzeichen/Semikolon),
    akzeptiert auch "[-100123, 456]". Fallback: ENV in {dev, development, local}.
    """
    ids = set()
    for key in ("DEVELOPER_CHAT_ID", "DEVELOPER_CHAT_IDS", "DEV_IDS"):
        raw = (os.getenv(key, "") or "").strip().strip("[]")
        for part in re.split(r"[,\s;]+", raw):
            part = part.strip().strip('"').strip("'")
            if not part or part.startswith("@"):
                continue
            if part.lstrip("-").isdigit():
                try:
                    ids.add(int(part))
                except Exception:
                    pass
    if not ids and os.getenv("ENVIRONMENT", "").lower() in {"dev", "development", "local"} and user_id_hint:
        ids.add(user_id_hint)
    return ids

def get_scope_label(context: ContextTypes.DEFAULT_TYPE) -> str:
    scope = context.user_data.get('scope', {'type': 'all'})
    if scope.get('type') == 'group':
        title = scope.get('title')
        return title or f"Gruppe {scope.get('chat_id')}"
    return "Alle Gruppen"

async def dev_menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    dev_ids = _dev_ids_from_env(user_id)

    # Hilfreiche Debug-Antworten statt "stumm"
    msg = update.effective_message or update.message
    if not dev_ids:
        return await (msg.reply_text)(
            "❌ Dev-Zugang nicht konfiguriert.\n"
            "Setze ENV `DEVELOPER_CHAT_ID` oder `DEVELOPER_CHAT_IDS` (deine Telegram User-ID)."
        )
    if user_id not in dev_ids:
        return await (msg.reply_text)(
            f"❌ Nur für Entwickler. Deine User-ID: {user_id}\n"
            f"Erlaubte IDs: {', '.join(map(str, sorted(dev_ids)))}"
        )

    # Aggregat-Scope immer setzen (damit nirgendwo 'Keine Gruppe' hochkommt)
    _set_dev_aggregate_scope(context)
    scope_label = get_scope_label(context)

    kb = [
        [InlineKeyboardButton("📊 System-Stats", callback_data="dev_system_stats")],
        [InlineKeyboardButton("💰 Pro-Verwaltung", callback_data="dev_pro_management")],
        [InlineKeyboardButton("📢 Werbung-Dashboard", callback_data="dev_ads_dashboard")],
        [InlineKeyboardButton("🗄 DB-Management", callback_data="dev_db_management")],
        [InlineKeyboardButton("🔄 Bot neustarten", callback_data="dev_restart_bot")],
        [InlineKeyboardButton("📝 Logs anzeigen", callback_data="dev_show_logs")]
    ]

    start_time = context.bot_data.get('start_time', datetime.datetime.now())
    uptime = datetime.datetime.now() - start_time
    text = (
        "⚙️ **Entwickler-Menü**\n\n"
        f"🤖 Bot-Version: {__version__}\n"
        f"⏰ Uptime: {uptime}\n"
        f"👥 Registrierte Gruppen: {len(get_registered_groups())}\n"
        f"🔎 Datenquelle: {scope_label}"
    )
    return await (msg.reply_text)(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def dev_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id

    dev_ids = _dev_ids_from_env(user_id)
    if user_id not in dev_ids:
        return await query.answer("❌ Nur für Entwickler.", show_alert=True)

    _set_dev_aggregate_scope(context)  # Aggregat für Dev erzwingen

    # --- Gruppenauswahl (paginierte Liste + Aggregat) ---
    if data.startswith("dev_group_select_"):
        try:
            page = int(data.rsplit("_", 1)[-1])
        except:
            page = 0

        groups = get_registered_groups()
        page_size = 10
        total = len(groups)
        total_pages = max(1, (total - 1)//page_size + 1)
        page = max(0, min(page, total_pages-1))
        start_idx = page * page_size
        end_idx = min(start_idx + page_size, total)
        current = groups[start_idx:end_idx]

        text = (
            "🔽 **Gruppenauswahl**\n\n"
            "Wähle eine einzelne Gruppe **oder** „Alle Gruppen (Aggregiert)“.\n"
            f"Seite {page+1}/{total_pages} · Zeige {start_idx+1}-{end_idx} von {total}\n"
        )

        kb = [[InlineKeyboardButton("🌐 Alle Gruppen (Aggregiert)", callback_data="dev_group_all")]]

        for chat_id, title in current:
            label = f"{title[:28]}{'…' if len(title)>28 else ''}"
            kb.append([InlineKeyboardButton(label, callback_data=f"dev_group_pick:{chat_id}")])

        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀️", callback_data=f"dev_group_select_{page-1}"))
        if page < total_pages-1:
            nav.append(InlineKeyboardButton("▶️", callback_data=f"dev_group_select_{page+1}"))
        if nav:
            kb.append(nav)
        kb.append([InlineKeyboardButton("🔙 Zurück", callback_data="dev_back_to_menu")])

        return await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

    elif data == "dev_group_all":
        context.user_data['scope'] = {'type': 'all'}
        await query.answer("Datenquelle: Alle Gruppen (Aggregiert)")
        data = "dev_back_to_menu"  # fall-through zur Menü-Ansicht

    elif data.startswith("dev_group_pick:"):
        _, raw = data.split(":", 1)
        try:
            chat_id = int(raw)
        except:
            return await query.answer("Ungültige Auswahl.", show_alert=True)
        title = next((t for cid, t in get_registered_groups() if cid == chat_id), str(chat_id))
        context.user_data['scope'] = {'type': 'group', 'chat_id': chat_id, 'title': title}
        await query.answer(f"Datenquelle gesetzt: {title}")
        data = "dev_back_to_menu"  # fall-through

    
    elif data == "dev_pro_management":
        # Seite merken/initialisieren
        page = context.user_data.get('pro_page', 0)
        page_size = 8

        groups = get_registered_groups()
        total = len(groups)
        total_pages = max(1, (total - 1)//page_size + 1)
        page = max(0, min(page, total_pages-1))
        context.user_data['pro_page'] = page

        start_idx = page * page_size
        end_idx = min(start_idx + page_size, total)
        current = groups[start_idx:end_idx]

        lines = []
        for chat_id, title in current:
            info = get_subscription_info(chat_id)
            if info.get("active"):
                until = info.get("valid_until")
                until_str = until.strftime("%Y-%m-%d") if until else "∞"
                lines.append(f"• {title[:28]}: ✅ PRO (bis {until_str})")
            else:
                lines.append(f"• {title[:28]}: ❌ PRO")

        scope_label = get_scope_label(context)
        text = (
            f"💰 **Pro-Verwaltung**\n\n"
            f"🔎 Datenquelle: {scope_label}\n"
            f"Seite {page+1}/{total_pages} · Gruppen {start_idx+1}-{end_idx} von {total}\n\n" +
            ("\n".join(lines) if lines else "Keine Gruppen auf dieser Seite.")
            + "\n\nWähle eine Gruppe und setze PRO manuell:"
        )

        # Tastatur: Für jede Gruppe zwei Buttons ( +30d | Entfernen )
        kb = []
        for chat_id, title in current:
            kb.append([
                InlineKeyboardButton(f"⭐ +30d: {title[:14]}", callback_data=f"dev_pro_set:{chat_id}:30"),
                InlineKeyboardButton("🧹 Entfernen", callback_data=f"dev_pro_clear:{chat_id}")
            ])

        # Navigations- & Bulk-Buttons
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀️ Zurück", callback_data="dev_pro_prev"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("▶️ Weiter", callback_data="dev_pro_next"))
        if nav:
            kb.append(nav)

        # Bulk auf Seite (optional)
        kb.append([InlineKeyboardButton("⭐ PRO +30d (alle auf Seite)", callback_data="dev_pro_page_extend_30d")])

        kb.append([InlineKeyboardButton("🔙 Zurück", callback_data="dev_back_to_menu")])
        return await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

    elif data == "dev_ads_dashboard":
        # Werbung-Dashboard
        campaigns = list_active_campaigns()
        text = "📢 **Werbung-Dashboard**\n\n"
        
        if campaigns:
            for cid, title, _, _, _, _, weight in campaigns[:5]:
                text += f"• #{cid} {title[:30]} (Gewicht: {weight})\n"
            if len(campaigns) > 5:
                text += f"\n...und {len(campaigns) - 5} weitere Kampagnen"
        else:
            text += "Keine aktiven Werbekampagnen."
        
        kb = [
            [InlineKeyboardButton("➕ Neue Kampagne", callback_data="dev_ad_new")],
            [InlineKeyboardButton("🟢/🔴 Aktiv/Deaktiv", callback_data="dev_ad_toggle_menu")],
            [InlineKeyboardButton("✏️ Bearbeiten", callback_data="dev_ad_edit_menu")],
            [InlineKeyboardButton("🗑 Löschen", callback_data="dev_ad_delete_menu")],
            [InlineKeyboardButton("⚙️ Einstellungen", callback_data="dev_ad_settings")],   # ← NEU
            [InlineKeyboardButton("📊 Statistiken", callback_data="dev_ad_stats")],
            [InlineKeyboardButton("🔙 Zurück", callback_data="dev_back_to_menu")]
        ]

        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
    
    elif data == "dev_db_management":
        # DB-Management
        db_stats = get_db_stats()
        text = (
            "🗄 **Datenbank-Management**\n\n"
            f"Tabellen: {db_stats['table_count']}\n"
            f"Größe: {db_stats['db_size']}\n"
            f"Aktive Connections: {db_stats['connections']}\n"
        )
        
        kb = [
            [InlineKeyboardButton("🔄 Vacuum", callback_data="dev_db_vacuum")],
            [InlineKeyboardButton("📊 Table Stats", callback_data="dev_db_tables")],
            [InlineKeyboardButton("🔙 Zurück", callback_data="dev_back_to_menu")]
        ]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
    
    elif data == "dev_restart_bot":
        # Bot neustarten - Confirmation
        text = "🔄 **Bot neustarten**\n\nBist du sicher? Der Bot wird für kurze Zeit nicht verfügbar sein."
        kb = [
            [
                InlineKeyboardButton("✅ Ja", callback_data="dev_restart_confirm"),
                InlineKeyboardButton("❌ Nein", callback_data="dev_back_to_menu")
            ]
        ]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
    
    elif data == "dev_restart_confirm":
        await query.edit_message_text("🔄 Bot wird neugestartet... Einen Moment bitte.", parse_mode="Markdown")
        logger.info(f"Bot-Neustart durch Admin {user_id} initiiert.")
        restart_bot(context)
    
    if data == "dev_system_stats":
        groups = get_registered_groups()
        active_groups = len([g for g in groups if is_daily_stats_enabled(g[0])])

        scope = context.user_data.get('scope', {'type': 'all'})
        chat_id = scope.get('chat_id') if scope.get('type') == 'group' else None
        overview = _get_global_overview(chat_id=chat_id)

        ram_line = f"🧠 RAM: {psutil.Process().memory_info().rss / 1024 / 1024:.1f} MB" if psutil else "🧠 RAM: n/a"

        text = (
            "📊 **System-Statistiken**\n\n"
            f"🔎 Datenquelle: {get_scope_label(context)}\n"
            f"👥 Gruppen gesamt: {len(groups)}\n"
            f"✅ Aktive Gruppen: {active_groups}\n"
            f"💾 DB-Pool: {_db_pool.closed}/{_db_pool.maxconn}\n"
            f"⚡ Handler: {len(context.application.handlers)}\n"
            f"{ram_line}\n\n"
            "🗂 **Datenbank (aggregiert)**\n"
            f"• Nachrichten gesamt: {overview.get('messages_total','–')}\n"
            f"• Nachrichten heute: {overview.get('messages_today','–')}\n"
            f"• Eindeutige Nutzer (gesamt): {overview.get('unique_users','–')}\n"
            f"• Ad-Impressions heute: {overview.get('impr_today','–')}\n"
            f"• Ad-Impressions gesamt: {overview.get('impr_total','–')}\n"
        )
        kb = [[InlineKeyboardButton("🔙 Zurück", callback_data="dev_back_to_menu")]]
        return await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

    
    elif data == "dev_show_logs":
        # Logs anzeigen
        logs = get_recent_logs()
        text = "📝 **Letzte Log-Einträge**\n\n```\n"
        text += logs + "\n```"
        
        kb = [
            [InlineKeyboardButton("🔄 Aktualisieren", callback_data="dev_show_logs")],
            [InlineKeyboardButton("🔙 Zurück", callback_data="dev_back_to_menu")]
        ]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
    
    elif data == "dev_back_to_menu":
        _set_dev_aggregate_scope(context)
        scope_label = get_scope_label(context)
        kb = [
            [InlineKeyboardButton(f"🔽 Gruppenauswahl ({scope_label})", callback_data="dev_group_select_0")],
            [InlineKeyboardButton("📊 System-Stats", callback_data="dev_system_stats")],
            [InlineKeyboardButton("💰 Pro-Verwaltung", callback_data="dev_pro_management")],
            [InlineKeyboardButton("📢 Werbung-Dashboard", callback_data="dev_ads_dashboard")],
            [InlineKeyboardButton("🗄 DB-Management", callback_data="dev_db_management")],
            [InlineKeyboardButton("🔄 Bot neustarten", callback_data="dev_restart_bot")],
            [InlineKeyboardButton("📝 Logs anzeigen", callback_data="dev_show_logs")]
        ]

        start_time = context.bot_data.get('start_time', datetime.datetime.now())
        uptime = datetime.datetime.now() - start_time
        text = (
            "⚙️ **Entwickler-Menü**\n\n"
            f"🤖 Bot-Version: {__version__}\n"
            f"⏰ Uptime: {uptime}\n"
            f"👥 Registrierte Gruppen: {len(get_registered_groups())}\n"
            f"🔎 Datenquelle: {scope_label}"
        )
        return await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

    elif data == "dev_ad_new":
        # Wizard: Titel → Text → Link → BildURL(optional) → CTA(optional) → Gewicht
        context.user_data['ad_wizard'] = {'mode':'new', 'stage':'title', 'data':{}}
        kb = [[InlineKeyboardButton("❌ Abbrechen", callback_data="dev_ad_cancel")]]
        return await query.edit_message_text(
            "➕ **Neue Kampagne**\n\nBitte *Titel* senden.",
            parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb)
        )

    elif data == "dev_ad_toggle_menu":
        rows = _adv_list_all(limit=20, offset=0)
        text = "🟢/🔴 **Aktivieren / Deaktivieren**\n\n"
        kb = []
        if not rows:
            text += "Keine Kampagnen vorhanden."
        else:
            for (cid, title, _body, _media, _link, _cta, weight, enabled) in rows:
                state = "🟢 aktiv" if enabled else "🔴 aus"
                btn = InlineKeyboardButton("Deaktivieren" if enabled else "Aktivieren",
                                        callback_data=f"dev_ad_toggle:{cid}:{'off' if enabled else 'on'}")
                kb.append([InlineKeyboardButton(f"#{cid} {title[:30]} • w={weight} • {state}", callback_data="dev_nop")])
                kb.append([btn])
        kb.append([InlineKeyboardButton("🔙 Zurück", callback_data="dev_ads_dashboard")])
        return await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

    elif data == "dev_ad_edit_menu":
        rows = _adv_list_all(limit=20, offset=0)
        text = "✏️ **Kampagne bearbeiten**\n\nWähle eine Kampagne:"
        kb = []
        for (cid, title, *_rest) in rows:
            kb.append([InlineKeyboardButton(f"#{cid} {title[:28]}", callback_data=f"dev_ad_edit:{cid}")])
        kb.append([InlineKeyboardButton("🔙 Zurück", callback_data="dev_ads_dashboard")])
        return await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

    elif data == "dev_ad_delete_menu":
        rows = _adv_list_all(limit=20, offset=0)
        text = "🗑 **Kampagne löschen**\n\nWähle eine Kampagne:"
        kb = []
        for (cid, title, *_rest) in rows:
            kb.append([InlineKeyboardButton(f"#{cid} {title[:28]}", callback_data=f"dev_ad_delete:{cid}")])
        kb.append([InlineKeyboardButton("🔙 Zurück", callback_data="dev_ads_dashboard")])
        return await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
    
    elif data.startswith("dev_ad_toggle:"):
        # dev_ad_toggle:<cid>:on|off
        _, cid, action = data.split(":")
        _adv_set_enabled(int(cid), action == "on")
        await query.answer("Gespeichert.")
        # zurück in die Liste
        return await dev_callback_handler(update, context)

    elif data.startswith("dev_ad_edit:"):
        # Feldauswahl
        _, cid = data.split(":")
        context.user_data['ad_wizard'] = {'mode':'edit', 'cid': int(cid), 'stage':'field'}
        kb = [
            [InlineKeyboardButton("Titel",   callback_data=f"dev_ad_edit_field:{cid}:title"),
            InlineKeyboardButton("Text",    callback_data=f"dev_ad_edit_field:{cid}:body_text")],
            [InlineKeyboardButton("Link",    callback_data=f"dev_ad_edit_field:{cid}:link_url"),
            InlineKeyboardButton("BildURL", callback_data=f"dev_ad_edit_field:{cid}:media_url")],
            [InlineKeyboardButton("CTA",     callback_data=f"dev_ad_edit_field:{cid}:cta_label"),
            InlineKeyboardButton("Gewicht", callback_data=f"dev_ad_edit_field:{cid}:weight")],
            [InlineKeyboardButton("🔙 Zurück", callback_data="dev_ad_edit_menu")]
        ]
        return await query.edit_message_text("✏️ Feld wählen:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

    elif data.startswith("dev_ad_edit_field:"):
        # Eingabewert erfragen
        _, cid, field = data.split(":")
        context.user_data['ad_wizard'] = {'mode':'edit', 'cid': int(cid), 'stage':'value', 'field': field}
        kb = [[InlineKeyboardButton("❌ Abbrechen", callback_data="dev_ad_cancel")]]
        hint = "neuen Wert senden"
        if field == "weight": hint = "neue Zahl (1..10) senden"
        return await query.edit_message_text(f"✏️ **{field}** – bitte {hint}.", parse_mode="Markdown",
                                            reply_markup=InlineKeyboardMarkup(kb))

    elif data.startswith("dev_ad_delete:") and not data.startswith("dev_ad_delete_confirm:"):
        _, cid = data.split(":")
        kb = [
            [InlineKeyboardButton("✅ Ja, löschen", callback_data=f"dev_ad_delete_confirm:{cid}")],
            [InlineKeyboardButton("❌ Abbrechen", callback_data="dev_ad_delete_menu")]
        ]
        return await query.edit_message_text("Wirklich löschen (Soft-Delete)?", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

    elif data.startswith("dev_ad_delete_confirm:"):
        _, cid = data.split(":")
        _adv_soft_delete(int(cid))
        await query.answer("Gelöscht.")
        return await dev_callback_handler(update, context)

    elif data == "dev_ad_cancel":
        context.user_data.pop('ad_wizard', None)
        return await query.edit_message_text("Abgebrochen.", parse_mode="Markdown",
                                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Zurück", callback_data="dev_ads_dashboard")]]))

    elif data == "dev_db_vacuum":
        # VACUUM Datenbank
        await query.edit_message_text("🔄 VACUUM wird ausgeführt...", parse_mode="Markdown")
        result = vacuum_database()
        text = f"✅ VACUUM abgeschlossen.\n\n{result}"
        
        kb = [[InlineKeyboardButton("🔙 Zurück", callback_data="dev_db_management")]]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
    
    elif data == "dev_pro_prev":
        context.user_data['pro_page'] = max(0, context.user_data.get('pro_page', 0) - 1)
        return await dev_callback_handler(update, context)

    elif data == "dev_pro_next":
        context.user_data['pro_page'] = context.user_data.get('pro_page', 0) + 1
        return await dev_callback_handler(update, context)

    elif data.startswith("dev_pro_set:"):
        # Format: dev_pro_set:<chat_id>:<days>
        try:
            _, chat_id_str, days_str = data.split(":")
            chat_id = int(chat_id_str)
            days = int(days_str)
        except Exception:
            return await query.answer("Ungültige Eingabe.", show_alert=True)

        until = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=days)).replace(microsecond=0)
        try:
            set_pro_until(chat_id, until)
        except Exception as e:
            return await query.answer(f"Fehler: {e}", show_alert=True)

        await query.answer(f"PRO bis {until.date().isoformat()} gesetzt.")
        return await dev_callback_handler(update, context)

    elif data.startswith("dev_pro_clear:"):
        # Format: dev_pro_clear:<chat_id>
        try:
            _, chat_id_str = data.split(":")
            chat_id = int(chat_id_str)
        except Exception:
            return await query.answer("Ungültige Eingabe.", show_alert=True)

        now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
        try:
            set_pro_until(chat_id, now)
        except Exception as e:
            return await query.answer(f"Fehler: {e}", show_alert=True)

        await query.answer("PRO entfernt.")
        return await dev_callback_handler(update, context)

    elif data == "dev_pro_page_extend_30d":
        page = context.user_data.get('pro_page', 0)
        page_size = 8
        groups = get_registered_groups()
        start_idx = page * page_size
        end_idx = min(start_idx + page_size, len(groups))
        until = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=30)).replace(microsecond=0)

        changed = 0
        for chat_id, _title in groups[start_idx:end_idx]:
            try:
                set_pro_until(chat_id, until)
                changed += 1
            except Exception:
                pass

        await query.answer(f"PRO +30d gesetzt für {changed} Gruppen (Seite).")
        return await dev_callback_handler(update, context)
    
    elif data == "dev_db_tables":
        # Tabellen-Statistiken
        table_stats = get_table_stats()
        text = "📊 **Tabellen-Statistiken**\n\n"
        for name, rows, size in table_stats[:10]:
            text += f"• {name}: {rows:,} Zeilen, {size}\n"
        
        kb = [[InlineKeyboardButton("🔙 Zurück", callback_data="dev_db_management")]]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
    
    elif data == "dev_ad_stats":
        # Werbeanzeigen-Statistiken
        stats = get_ad_stats()
        text = (
            "📊 **Werbestatistiken**\n\n"
            f"Kampagnen: {stats['campaign_count']}\n"
            f"Impressionen heute: {stats['impressions_today']}\n"
            f"Impressionen gesamt: {stats['total_impressions']}\n"
            f"Top Gruppen:\n"
        )
        for group_name, count in stats['top_groups']:
            text += f"• {group_name[:20]}: {count} Impressionen\n"
        
        kb = [[InlineKeyboardButton("🔙 Zurück", callback_data="dev_ads_dashboard")]]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

    elif data == "dev_ad_settings":
        scope = context.user_data.get('scope', {'type':'all'})
        chat_id = scope.get('chat_id') if scope.get('type') == 'group' else None
        bulk = chat_id is None

        if bulk:
            # Aggregat – keine konkrete Zeile, daher nur Info
            s = {"adv_enabled":"—","min_gap_min":"—","daily_cap":"—","every_n_messages":"—",
                "label":"—","quiet":"—","topic":"—"}
        else:
            S = get_adv_settings(chat_id)
            s = {
                "adv_enabled": "✅ an" if S["adv_enabled"] else "🔴 aus",
                "min_gap_min": f'{S["min_gap_min"]} min',
                "daily_cap":   str(S["daily_cap"]),
                "every_n_messages": str(S["every_n_messages"]),
                "label": S["label"],
                "quiet": f'{_fmt_hhmm(S["quiet_start_min"])}–{_fmt_hhmm(S["quiet_end_min"])}',
                "topic": str(S["adv_topic_id"]) if S["adv_topic_id"] is not None else "Default"
            }

        header = "⚙️ **Werbung-Einstellungen**\n"
        scope_line = f'🔎 Quelle: {get_scope_label(context)}'
        warn = "\n\n⚠️ Aggregat: Änderungen wirken auf **ALLE** Gruppen." if bulk else ""
        text = (
            f"{header}\n{scope_line}{warn}\n\n"
            f"• Status: {s['adv_enabled']}\n"
            f"• Mindestabstand: {s['min_gap_min']}\n"
            f"• Tages-Limit: {s['daily_cap']}\n"
            f"• Nach N Nachrichten: {s['every_n_messages']}\n"
            f"• Label: {s['label']}\n"
            f"• Ruhezeit: {s['quiet']}\n"
            f"• Topic: {s['topic']}\n"
        )

        kb = [
            [InlineKeyboardButton("🟢 Aktiv", callback_data="dev_ad_en:on"),
            InlineKeyboardButton("🔴 Aus",   callback_data="dev_ad_en:off")],
            [InlineKeyboardButton("Gap −30", callback_data="dev_ad_gap:-30"),
            InlineKeyboardButton("Gap +30", callback_data="dev_ad_gap:+30")],
            [InlineKeyboardButton("Cap −1",  callback_data="dev_ad_cap:-1"),
            InlineKeyboardButton("Cap +1",  callback_data="dev_ad_cap:+1")],
            [InlineKeyboardButton("Nmsgs −5",callback_data="dev_ad_nmsgs:-5"),
            InlineKeyboardButton("Nmsgs +5",callback_data="dev_ad_nmsgs:+5")],
            [InlineKeyboardButton("Label: Anzeige",   callback_data="dev_ad_label:Anzeige"),
            InlineKeyboardButton("Label: Sponsored", callback_data="dev_ad_label:Sponsored")],
            [InlineKeyboardButton("Quiet 22–06", callback_data="dev_ad_quiet:1320-360"),
            InlineKeyboardButton("Quiet aus",   callback_data="dev_ad_quiet:0-0")],
            [InlineKeyboardButton("🧹 Topic entfernen", callback_data="dev_ad_topic:clear")],
            [InlineKeyboardButton("❓ Topic setzen – Anleitung", callback_data="dev_ad_topic_help")],
            [InlineKeyboardButton("🔙 Zurück", callback_data="dev_ads_dashboard")]
        ]
        return await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

# --- einzelne Aktionen ---

    elif data.startswith("dev_ad_en:"):
        on = data.split(":",1)[1] == "on"
        scope = context.user_data.get('scope', {'type':'all'})
        chat_id = scope.get('chat_id') if scope.get('type') == 'group' else None
        if chat_id is None:
            n = _apply_adv_settings_bulk(context, {"adv_enabled": on})
            await query.answer(f"Gesetzt für {n} Gruppen.")
        else:
            set_adv_settings(chat_id, adv_enabled=on)
            await query.answer("Gespeichert.")
        return await dev_callback_handler(update, context)

    elif data.startswith("dev_ad_gap:"):
        delta = int(data.split(":",1)[1])
        scope = context.user_data.get('scope', {'type':'all'})
        chat_id = scope.get('chat_id') if scope.get('type') == 'group' else None
        if chat_id is None:
            changed = 0
            for cid,_ in get_registered_groups():
                s = get_adv_settings(cid); new = max(0, int(s["min_gap_min"]) + delta)
                set_adv_settings(cid, min_gap_min=new); changed += 1
            await query.answer(f"Gap angepasst ({changed} Gruppen).")
        else:
            s = get_adv_settings(chat_id); new = max(0, int(s["min_gap_min"]) + delta)
            set_adv_settings(chat_id, min_gap_min=new)
            await query.answer("Gespeichert.")
        return await dev_callback_handler(update, context)

    elif data.startswith("dev_ad_cap:"):
        delta = int(data.split(":",1)[1])
        scope = context.user_data.get('scope', {'type':'all'})
        chat_id = scope.get('chat_id') if scope.get('type') == 'group' else None
        if chat_id is None:
            changed = 0
            for cid,_ in get_registered_groups():
                s = get_adv_settings(cid); new = max(0, int(s["daily_cap"]) + delta)
                set_adv_settings(cid, daily_cap=new); changed += 1
            await query.answer(f"Cap angepasst ({changed} Gruppen).")
        else:
            s = get_adv_settings(chat_id); new = max(0, int(s["daily_cap"]) + delta)
            set_adv_settings(chat_id, daily_cap=new)
            await query.answer("Gespeichert.")
        return await dev_callback_handler(update, context)

    elif data.startswith("dev_ad_nmsgs:"):
        delta = int(data.split(":",1)[1])
        scope = context.user_data.get('scope', {'type':'all'})
        chat_id = scope.get('chat_id') if scope.get('type') == 'group' else None
        if chat_id is None:
            changed = 0
            for cid,_ in get_registered_groups():
                s = get_adv_settings(cid); new = max(0, int(s["every_n_messages"]) + delta)
                set_adv_settings(cid, every_n_messages=new); changed += 1
            await query.answer(f"Nmsgs angepasst ({changed} Gruppen).")
        else:
            s = get_adv_settings(chat_id); new = max(0, int(s["every_n_messages"]) + delta)
            set_adv_settings(chat_id, every_n_messages=new)
            await query.answer("Gespeichert.")
        return await dev_callback_handler(update, context)

    elif data.startswith("dev_ad_label:"):
        label = data.split(":",1)[1][:40]
        scope = context.user_data.get('scope', {'type':'all'})
        chat_id = scope.get('chat_id') if scope.get('type') == 'group' else None
        if chat_id is None:
            n = _apply_adv_settings_bulk(context, {"label": label})
            await query.answer(f"Label gesetzt ({n} Gruppen).")
        else:
            set_adv_settings(chat_id, label=label)
            await query.answer("Gespeichert.")
        return await dev_callback_handler(update, context)

    elif data.startswith("dev_ad_quiet:"):
        mins = data.split(":",1)[1]
        a,b = mins.split("-",1)
        qs, qe = int(a), int(b)
        scope = context.user_data.get('scope', {'type':'all'})
        chat_id = scope.get('chat_id') if scope.get('type') == 'group' else None
        fields = {"quiet_start_min": qs, "quiet_end_min": qe}
        if chat_id is None:
            n = _apply_adv_settings_bulk(context, fields)
            await query.answer(f"Quiet gesetzt ({n} Gruppen).")
        else:
            set_adv_settings(chat_id, **fields)
            await query.answer("Gespeichert.")
        return await dev_callback_handler(update, context)

    elif data == "dev_ad_topic:clear":
        scope = context.user_data.get('scope', {'type':'all'})
        chat_id = scope.get('chat_id') if scope.get('type') == 'group' else None
        if chat_id is None:
            n = 0
            for cid,_ in get_registered_groups():
                set_adv_topic(cid, None); n += 1
            await query.answer(f"Topic entfernt ({n} Gruppen).")
        else:
            set_adv_topic(chat_id, None)
            await query.answer("Topic entfernt.")
        return await dev_callback_handler(update, context)

    elif data == "dev_ad_topic_help":
        kb = [[InlineKeyboardButton("🔙 Zurück", callback_data="dev_ad_settings")]]
        help_text = (
            "📍 **Topic setzen (Gruppen-Thread)**\n\n"
            "1) Öffne im ZIEL-Gruppenchat den gewünschten Thread.\n"
            "2) Sende dort: `/set_adv_topic`\n"
            "→ Ab dann wird Werbung in genau diesem Thread gepostet (oder Default, wenn entfernt).\n"
        )
        return await query.edit_message_text(help_text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

    # --- einzelne Aktionen ---

def _fmt_hhmm(total_min: int) -> str:
    h = (total_min // 60) % 24
    m = total_min % 60
    return f"{h:02d}:{m:02d}"

def _apply_adv_settings_bulk(context, fields: dict) -> int:
    changed = 0
    for chat_id, _name in get_registered_groups():
        try:
            set_adv_settings(chat_id, **fields)
            changed += 1
        except Exception:
            pass
    return changed

# Hilfsfunktionen für Entwicklermenü

async def dev_wizard_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Nur aktiv, wenn Wizard-State existiert
    state = context.user_data.get('ad_wizard')
    if not state:
        return

    # Dev-Guard
    user_id = update.effective_user.id
    if user_id not in _dev_ids_from_env(user_id):
        return

    txt = (update.effective_message.text or "").strip()
    mode = state.get('mode')
    stage = state.get('stage')

    # --- Neuerstellung ---
    if mode == 'new':
        data = state['data']
        if stage == 'title':
            data['title'] = txt[:120]
            state['stage'] = 'body_text'
            return await update.effective_message.reply_text("Bitte *Text* senden.", parse_mode="Markdown")
        if stage == 'body_text':
            data['body_text'] = txt[:1000]
            state['stage'] = 'link_url'
            return await update.effective_message.reply_text("Bitte *Link-URL* senden (https://…)", parse_mode="Markdown")
        if stage == 'link_url':
            data['link_url'] = txt
            state['stage'] = 'media_url'
            return await update.effective_message.reply_text("Optional: *Bild-URL* senden oder `-` für ohne.", parse_mode="Markdown")
        if stage == 'media_url':
            if txt != "-":
                data['media_url'] = txt
            state['stage'] = 'cta_label'
            return await update.effective_message.reply_text("Optional: *CTA-Label* senden (Standard: „Mehr erfahren“) oder `-`.", parse_mode="Markdown")
        if stage == 'cta_label':
            if txt != "-":
                data['cta_label'] = txt[:30]
            state['stage'] = 'weight'
            return await update.effective_message.reply_text("Optional: *Gewicht* (1..10) senden oder `-`.", parse_mode="Markdown")
        if stage == 'weight':
            weight = 1
            if txt != "-":
                try:
                    weight = max(1, min(10, int(txt)))
                except:
                    return await update.effective_message.reply_text("Bitte Zahl 1..10 oder `-` senden.")
            cid = add_campaign(
                data.get('title'), data.get('body_text'), data.get('link_url'),
                data.get('media_url'), data.get('cta_label', "Mehr erfahren"),
                weight=weight
            )
            context.user_data.pop('ad_wizard', None)
            kb = [[InlineKeyboardButton("🔙 Zurück", callback_data="dev_ads_dashboard")]]
            return await update.effective_message.reply_text(f"✅ Kampagne **#{cid}** gespeichert & aktiv.", parse_mode="Markdown",
                                                             reply_markup=InlineKeyboardMarkup(kb))

    # --- Bearbeitung ---
    if mode == 'edit':
        if stage == 'value':
            cid = int(state['cid']); field = state['field']
            val = txt
            if field == 'weight':
                try:
                    val = max(1, min(10, int(txt)))
                except:
                    return await update.effective_message.reply_text("Bitte Zahl 1..10 senden.")
            _adv_update(cid, **{field: val})
            context.user_data['ad_wizard'] = {'mode':'edit', 'cid': cid, 'stage':'field'}
            kb = [[InlineKeyboardButton("🔙 Zurück", callback_data="dev_ad_edit_menu")]]
            return await update.effective_message.reply_text("✅ Gespeichert. Feld erneut wählen oder zurück.", parse_mode="Markdown",
                                                             reply_markup=InlineKeyboardMarkup(kb))

def _set_dev_aggregate_scope(context: ContextTypes.DEFAULT_TYPE):
    """
    Erzwingt für den Dev ein 'Aggregat' als gewählte Gruppe und schreibt
    kompatible Felder in user_data *und* chat_data, damit vorhandene
    Guards wie 'require_selected_group' zufrieden sind.
    """
    scope = {'type': 'all'}  # Aggregierte Datenquelle
    context.user_data['scope'] = scope
    # Kompatibilität zu evtl. vorhandenen Checks:
    for store in (context.user_data, context.chat_data):
        store['selected_group'] = 'ALL'
        store['selected_group_title'] = 'Alle Gruppen'
        store['chat_id'] = None  # None signalisiert Aggregat

@_with_cursor
def _get_global_overview(cur, chat_id: int | None = None):
    """
    Liefert robuste Kennzahlen aus message_logs & adv_impressions.
    chat_id=None => Aggregat über alle Gruppen.
    """
    out = {'messages_total': 0, 'messages_today': 0, 'unique_users': 0, 'impr_today': 0, 'impr_total': 0}
    try:
        cur.execute("SELECT COUNT(*) FROM message_logs" + ("" if chat_id is None else " WHERE chat_id=%s") + ";",
                    (() if chat_id is None else (chat_id,)))
        out['messages_total'] = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM message_logs WHERE ts >= CURRENT_DATE" +
                    ("" if chat_id is None else " AND chat_id=%s") + ";",
                    (() if chat_id is None else (chat_id,)))
        out['messages_today'] = cur.fetchone()[0]

        cur.execute("SELECT COUNT(DISTINCT user_id) FROM message_logs" +
                    ("" if chat_id is None else " WHERE chat_id=%s") + ";",
                    (() if chat_id is None else (chat_id,)))
        out['unique_users'] = cur.fetchone()[0]
    except Exception:
        pass
    try:
        cur.execute("SELECT COUNT(*) FROM adv_impressions" + ("" if chat_id is None else " WHERE chat_id=%s") + ";",
                    (() if chat_id is None else (chat_id,)))
        out['impr_total'] = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM adv_impressions WHERE ts >= CURRENT_DATE" +
                    ("" if chat_id is None else " AND chat_id=%s") + ";",
                    (() if chat_id is None else (chat_id,)))
        out['impr_today'] = cur.fetchone()[0]
    except Exception:
        pass
    return out

def restart_bot(context):
    """Bot neustarten durch Skript-Neustart"""
    # Stelle sicher, dass alle offenen Verbindungen geschlossen werden
    if hasattr(context.application, 'shutdown'):
        context.application.shutdown()
    
    # Starte Prozess neu (funktioniert nur wenn als Skript gestartet)
    try:
        python = sys.executable
        script_path = sys.argv[0]
        os.execl(python, python, script_path, *sys.argv[1:])
    except Exception as e:
        logger.error(f"Fehler beim Neustart: {e}")

def get_recent_logs(lines=20):
    """Letzte Log-Einträge holen"""
    try:
        log_file = os.getenv("LOG_FILE", "bot.log")
        if not os.path.exists(log_file):
            return "Keine Log-Datei gefunden."
        
        with open(log_file, 'r', encoding='utf-8') as f:
            logs = f.readlines()
        
        # Letzten n Zeilen zurückgeben
        return ''.join(logs[-lines:])
    except Exception as e:
        return f"Fehler beim Lesen der Logs: {e}"

@_with_cursor
def get_db_stats(cur):
    """Datenbankstatistiken holen"""
    # Anzahl der Tabellen
    cur.execute("SELECT COUNT(*) FROM pg_catalog.pg_tables WHERE schemaname = 'public';")
    table_count = cur.fetchone()[0]
    
    # DB-Größe
    cur.execute("SELECT pg_size_pretty(pg_database_size(current_database()));")
    db_size = cur.fetchone()[0]
    
    # Aktive Verbindungen
    cur.execute("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database();")
    connections = cur.fetchone()[0]
    
    return {
        "table_count": table_count,
        "db_size": db_size,
        "connections": connections
    }

@_with_cursor
def get_table_stats(cur):
    """Statistiken zu Tabellen holen"""
    cur.execute("""
        SELECT 
            relname as "Table",
            n_live_tup as "Rows",
            pg_size_pretty(pg_total_relation_size(relid)) as "Size"
        FROM 
            pg_stat_user_tables
        ORDER BY 
            n_live_tup DESC;
    """)
    return cur.fetchall()

@_with_cursor
def vacuum_database(cur):
    """VACUUM ausführen"""
    # ANALYZE durchführen (nicht VACUUM FULL, da das die DB sperrt)
    cur.execute("VACUUM ANALYZE;")
    return "VACUUM ANALYZE ausgeführt."

@_with_cursor
def get_ad_stats(cur):
    """Werbestatistiken holen"""
    # Anzahl Kampagnen
    cur.execute("SELECT COUNT(*) FROM adv_campaigns WHERE enabled=TRUE;")
    campaign_count = cur.fetchone()[0]
    
    # Impressionen heute
    cur.execute("SELECT COUNT(*) FROM adv_impressions WHERE ts >= CURRENT_DATE;")
    impressions_today = cur.fetchone()[0]
    
    # Impressionen gesamt
    cur.execute("SELECT COUNT(*) FROM adv_impressions;")
    total_impressions = cur.fetchone()[0]
    
    # Top Gruppen (mit Join zu Gruppennamen)
    # Top Gruppen (ohne Abhängigkeit von message_logs-Titeln)
    cur.execute("""
        SELECT chat_id, COUNT(*) AS impression_count
        FROM adv_impressions
        GROUP BY chat_id
        ORDER BY impression_count DESC
        LIMIT 5;
    """)
    rows = cur.fetchall() or []

    # Namen aus der registrierten Gruppenliste mappen
    name_map = dict(get_registered_groups())  # -> {chat_id: title}
    top_groups = [(name_map.get(cid, f"Group {cid}"), cnt) for (cid, cnt) in rows]

    return {
    "campaign_count": campaign_count,
    "impressions_today": impressions_today,
    "total_impressions": total_impressions,
    "top_groups": top_groups
}


def register_dev_handlers(app):
    app.add_handler(CommandHandler("devmenu", dev_menu_command), group=-1)
    app.add_handler(CallbackQueryHandler(dev_callback_handler, pattern="^dev_", block=True), group=1)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, dev_wizard_router), group=2)  # << neu
    register_ads(app)