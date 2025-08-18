import os
import re
import datetime
import sys
import subprocess
import psutil
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters

from patchnotes import __version__
from database import get_registered_groups, is_daily_stats_enabled, _db_pool, _with_cursor
from ads import list_active_campaigns, get_subscription_info, set_pro_until

# Logger konfigurieren
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

async def dev_menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    # Dev-IDs robust lesen (vereinheitlicht: liest beide ENV-Varianten)
    dev_ids = set()
    for envkey in ["DEVELOPER_CHAT_ID", "DEVELOPER_CHAT_IDS"]:
        raw = os.getenv(envkey, "")
        for part in re.split(r'[,;\s]+', raw):
            if part.strip() and part.strip().lstrip('-').isdigit():
                dev_ids.add(int(part.strip()))
    if not dev_ids and os.getenv("ENVIRONMENT", "").lower() == "development":
        dev_ids.add(user_id)
    if user_id not in dev_ids:
        return await update.message.reply_text(f"❌ Nur für Entwickler verfügbar.\nDeine User-ID: {user_id}")

    # Scope-Label (Alle Gruppen oder konkrete Gruppe)
    scope_label = get_scope_label(context)

    kb = [
        [InlineKeyboardButton(f"🔽 Gruppenauswahl ({scope_label})", callback_data="dev_group_select_0")],
        [InlineKeyboardButton("📊 System-Stats", callback_data="dev_system_stats")],
        [InlineKeyboardButton("💰 Pro-Verwaltung", callback_data="dev_pro_management")],
        [InlineKeyboardButton("📢 Werbung-Dashboard", callback_data="dev_ads_dashboard")],
        [InlineKeyboardButton("🗄 DB-Management", callback_data="dev_db_management")],
        [InlineKeyboardButton("🔄 Bot neustarten", callback_data="dev_restart_bot")],
        [InlineKeyboardButton("📝 Logs anzeigen", callback_data="dev_show_logs")],
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
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def dev_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id

    # flexible Liste erlauben (Komma-getrennt)
    dev_ids = {int(x) for x in os.getenv("DEVELOPER_CHAT_IDS", "").split(",") if x.strip().isdigit()}
    if user_id not in dev_ids:
        return await query.answer("❌ Nur für Entwickler.", show_alert=True)
    
    if data == "dev_system_stats":
        groups = get_registered_groups()
        active_groups = len([g for g in groups if is_daily_stats_enabled(g[0])])
        text = (
            "📊 **System-Statistiken**\n\n"
            f"👥 Gruppen gesamt: {len(groups)}\n"
            f"✅ Aktive Gruppen: {active_groups}\n"
            f"💾 DB-Pool: {_db_pool.closed}/{_db_pool.maxconn}\n"
            f"⚡ Handler: {len(context.application.handlers)}\n"
            f"🧠 RAM: {psutil.Process().memory_info().rss / 1024 / 1024:.1f} MB"
        )
        kb = [[InlineKeyboardButton("🔙 Zurück", callback_data="dev_back_to_menu")]]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

    elif data.startswith("dev_group_select_"):
        # Seite aus Callback lesen
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

        kb = [
            [InlineKeyboardButton("🌐 Alle Gruppen (Aggregiert)", callback_data="dev_group_all")]
        ]

        # je Gruppe ein Button (Titel kürzen)
        row = []
        for chat_id, title in current:
            label = f"{title[:28]}{'…' if len(title)>28 else ''}"
            row = [InlineKeyboardButton(label, callback_data=f"dev_group_pick:{chat_id}")]
            kb.append(row)

        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀️", callback_data=f"dev_group_select_{page-1}"))
        if page < total_pages-1:
            nav.append(InlineKeyboardButton("▶️", callback_data=f"dev_group_select_{page+1}"))
        if nav:
            kb.append(nav)

        kb.append([InlineKeyboardButton("🔙 Zurück", callback_data="dev_back_to_menu")])

        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

    elif data == "dev_group_all":
        # Aggregierter Modus
        context.user_data['scope'] = {'type': 'all'}
        await query.answer("Datenquelle: Alle Gruppen (Aggregiert)")
        return await dev_callback_handler(update, context)  # Refresh aufrufend

    elif data.startswith("dev_group_pick:"):
        # Konkrete Gruppe wählen
        _, raw = data.split(":", 1)
        try:
            chat_id = int(raw)
        except:
            return await query.answer("Ungültige Auswahl.", show_alert=True)

        # Titel aus Liste holen (fallback: Chat-ID)
        title = next((t for cid, t in get_registered_groups() if cid == chat_id), str(chat_id))
        context.user_data['scope'] = {'type': 'group', 'chat_id': chat_id, 'title': title}
        await query.answer(f"Datenquelle gesetzt: {title}")
        # Zurück ins Menü (oder einfach System-Stats öffnen)
        kb = [
            [InlineKeyboardButton(f"🔽 Gruppenauswahl ({get_scope_label(context)})", callback_data="dev_group_select_0")],
            [InlineKeyboardButton("📊 System-Stats", callback_data="dev_system_stats")],
            [InlineKeyboardButton("🔙 Zurück", callback_data="dev_back_to_menu")],
        ]
        await query.edit_message_text(
            f"✅ **Datenquelle gesetzt:** {get_scope_label(context)}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(kb)
        )
    
    elif data == "dev_pro_management":
        # Pro-Verwaltung Dashboard - Alle Gruppen anzeigen, egal ob Pro oder nicht
        try:
            # Alle Gruppen holen, nicht gefiltert nach Pro-Status
            groups = get_registered_groups()
            total_groups = len(groups)
            
            page = context.user_data.get('pro_page', 0)
            page_size = 10
            
            # Stellen sicher, dass die Seite im gültigen Bereich liegt
            total_pages = max(1, (total_groups - 1) // page_size + 1)
            if page >= total_pages:
                page = 0
                context.user_data['pro_page'] = 0
                
            # Aktuelle Gruppen für diese Seite berechnen
            start_idx = page * page_size
            end_idx = min(start_idx + page_size, total_groups)
            current_groups = groups[start_idx:end_idx]
            
            group_status_lines = []
            pro_count = 0
            
            for chat_id, title in current_groups:
                info = get_subscription_info(chat_id)
                status = "✅ Ja" if info["active"] else "❌ Nein"
                if info["active"]:
                    pro_count += 1
                    tier = info["tier"]
                    until = info["valid_until"].strftime("%Y-%m-%d") if info["valid_until"] else "∞"
                    details = f" - {tier} (bis {until})"
                else:
                    details = ""
                
                group_status_lines.append(f"• {title[:20]}: {status}{details}")
            
            text = (
                f"💰 **Pro-Abonnements** (Seite {page+1}/{total_pages})\n\n"
                f"Zeige Gruppen {start_idx+1}-{end_idx} von {total_groups}\n"
                f"Pro-Gruppen auf dieser Seite: {pro_count}/{len(current_groups)}\n\n"
            )
            
            if group_status_lines:
                text += "\n".join(group_status_lines)
            else:
                text += "Keine Gruppen gefunden."
                
            # Pagination controls
            kb = []
            nav_buttons = []
            
            if page > 0:
                nav_buttons.append(InlineKeyboardButton("◀️ Zurück", callback_data="dev_pro_prev"))
            
            if page < total_pages - 1:
                nav_buttons.append(InlineKeyboardButton("▶️ Weiter", callback_data="dev_pro_next"))
            
            if nav_buttons:
                kb.append(nav_buttons)
                
            kb.append([InlineKeyboardButton("➕ Pro-Abo hinzufügen", callback_data="dev_pro_add")])
            kb.append([InlineKeyboardButton("🔙 Zurück", callback_data="dev_back_to_menu")])
            
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
        
        except Exception as e:
            logger.error(f"Fehler beim Anzeigen der Pro-Verwaltung: {e}", exc_info=True)
            await query.edit_message_text(
                f"⚠️ Fehler beim Laden der Gruppen-Daten: {e}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Zurück", callback_data="dev_back_to_menu")]]),
                parse_mode="Markdown"
            )

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
    
    elif data == "dev_system_stats":
        groups = get_registered_groups()
        active_groups = len([g for g in groups if is_daily_stats_enabled(g[0])])

        # Scope lesen (None = Aggregiert)
        scope = context.user_data.get('scope', {'type': 'all'})
        chat_id = scope.get('chat_id') if scope.get('type') == 'group' else None

        overview = get_global_overview(chat_id=chat_id)

        text = (
            "📊 **System-Statistiken**\n\n"
            f"🔎 Datenquelle: {get_scope_label(context)}\n"
            f"👥 Gruppen gesamt: {len(groups)}\n"
            f"✅ Aktive Gruppen: {active_groups}\n"
            f"💾 DB-Pool: {_db_pool.closed}/{_db_pool.maxconn}\n"
            f"⚡ Handler: {len(context.application.handlers)}\n"
            f"🧠 RAM: {psutil.Process().memory_info().rss / 1024 / 1024:.1f} MB\n\n"
            "🗂 **Datenbank (ausgewählte Quelle)**\n"
            f"• Nachrichten gesamt: {overview.get('messages_total','–')}\n"
            f"• Nachrichten heute: {overview.get('messages_today','–')}\n"
            f"• Eindeutige Nutzer (gesamt): {overview.get('unique_users','–')}\n"
            f"• Ad-Impressions heute: {overview.get('impr_today','–')}\n"
            f"• Ad-Impressions gesamt: {overview.get('impr_total','–')}\n"
        )
        kb = [[InlineKeyboardButton("🔙 Zurück", callback_data="dev_back_to_menu")]]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
    
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
        kb = [
            [InlineKeyboardButton(f"🔽 Gruppenauswahl ({get_scope_label(context)})", callback_data="dev_group_select_0")],
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
            f"🔎 Datenquelle: {get_scope_label(context)}"
        )
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

    # Zusätzliche Callback-Handler
    elif data == "dev_db_vacuum":
        # VACUUM Datenbank
        await query.edit_message_text("🔄 VACUUM wird ausgeführt...", parse_mode="Markdown")
        result = vacuum_database()
        text = f"✅ VACUUM abgeschlossen.\n\n{result}"
        
        kb = [[InlineKeyboardButton("🔙 Zurück", callback_data="dev_db_management")]]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
    
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
    
    else:
        # Platzhalter für weitere Dev-Callbacks
        await query.answer("ℹ️ Aktion wird implementiert.", show_alert=False)

# Hilfsfunktionen für Entwicklermenü

def get_scope_label(context: ContextTypes.DEFAULT_TYPE) -> str:
    scope = context.user_data.get('scope', {'type': 'all'})
    if scope.get('type') == 'group':
        return scope.get('title', f"Gruppe {scope.get('chat_id')}")
    return "Alle Gruppen"

@_with_cursor
def get_global_overview(cur, chat_id: int | None = None):
    """
    Liefert einfache, robuste Kennzahlen über message_logs & adv_impressions.
    Funktioniert aggregiert (chat_id=None) oder gruppenspezifisch (chat_id gesetzt).
    Fällt bei fehlenden Tabellen/Spalten robust zurück.
    """
    out = {
        'messages_total': 0,
        'messages_today': 0,
        'unique_users': 0,
        'impr_today': 0,
        'impr_total': 0,
    }

    # --- message_logs ---
    try:
        if chat_id is None:
            cur.execute("SELECT COUNT(*) FROM message_logs;")
        else:
            cur.execute("SELECT COUNT(*) FROM message_logs WHERE chat_id = %s;", (chat_id,))
        out['messages_total'] = cur.fetchone()[0]

        if chat_id is None:
            cur.execute("SELECT COUNT(*) FROM message_logs WHERE ts >= CURRENT_DATE;")
        else:
            cur.execute("SELECT COUNT(*) FROM message_logs WHERE chat_id = %s AND ts >= CURRENT_DATE;", (chat_id,))
        out['messages_today'] = cur.fetchone()[0]

        if chat_id is None:
            cur.execute("SELECT COUNT(DISTINCT user_id) FROM message_logs;")
        else:
            cur.execute("SELECT COUNT(DISTINCT user_id) FROM message_logs WHERE chat_id = %s;", (chat_id,))
        out['unique_users'] = cur.fetchone()[0]
    except Exception:
        # Tabelle/Spalten existieren ggf. (noch) nicht – einfach überspringen
        pass

    # --- adv_impressions ---
    try:
        if chat_id is None:
            cur.execute("SELECT COUNT(*) FROM adv_impressions;")
        else:
            cur.execute("SELECT COUNT(*) FROM adv_impressions WHERE chat_id = %s;", (chat_id,))
        out['impr_total'] = cur.fetchone()[0]

        if chat_id is None:
            cur.execute("SELECT COUNT(*) FROM adv_impressions WHERE ts >= CURRENT_DATE;")
        else:
            cur.execute("SELECT COUNT(*) FROM adv_impressions WHERE chat_id = %s AND ts >= CURRENT_DATE;", (chat_id,))
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
    cur.execute("""
        WITH group_names AS (
            SELECT DISTINCT chat_id, title FROM message_logs
        )
        SELECT 
            COALESCE(gn.title, 'Unknown Group') as group_name,
            COUNT(*) as impression_count
        FROM 
            adv_impressions ai
            LEFT JOIN group_names gn ON ai.chat_id = gn.chat_id
        GROUP BY 
            group_name
        ORDER BY 
            impression_count DESC
        LIMIT 5;
    """)
    top_groups = cur.fetchall()
    
    return {
        "campaign_count": campaign_count,
        "impressions_today": impressions_today,
        "total_impressions": total_impressions,
        "top_groups": top_groups or []
    }

def register_dev_handlers(app):
    app.add_handler(CommandHandler("devmenu", dev_menu_command))
    app.add_handler(CallbackQueryHandler(dev_callback_handler, pattern="^dev_"))
