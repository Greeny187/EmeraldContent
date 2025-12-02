# miniapp.py — Support Bot WebApp Integration (v1.0)
"""
Handles MiniApp communication for Emerald Support Bot.
Processes WebApp data from appsupport.html and syncs settings.
"""

import os
import json
import logging
from typing import Optional
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, Application, filters

import sql  # Import SQL layer

log = logging.getLogger("bot.support.miniapp")

WEBAPP_URL = os.getenv(
    "SUPPORT_WEBAPP_URL",
    "https://greeny187.github.io/EmeraldContentBots/miniapp/appsupport.html"
)


async def cmd_support(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Open Support MiniApp"""
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔧 Support öffnen", web_app=WebAppInfo(url=WEBAPP_URL))
    ]])
    await update.message.reply_text(
        "👋 **Emerald Support MiniApp**\n\n"
        "Öffne die Mini-App um:\n"
        "• Neue Support-Anfragen zu erstellen\n"
        "• Deine Tickets zu verwalten\n"
        "• Gruppeneinstellungen anzupassen",
        reply_markup=kb, 
        parse_mode="Markdown"
    )


async def on_web_app_data(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    Handle WebApp data from MiniApp (appsupport.html).
    Expects JSON payload with ticket creation or settings data.
    """
    msg = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    
    try:
        # Parse WebApp data
        raw = msg.web_app_data.data
        payload = json.loads(raw)
        
        log.info(f"WebApp data received from user {user.id}: keys={list(payload.keys())}")
        
        chat_id = payload.get("cid") or chat.id
        
        if not chat_id:
            return await msg.reply_text("❌ Keine Chat-ID gefunden.")
        
        # Case 1: Settings update
        if "welcome_on" in payload or "rules_on" in payload:
            log.info(f"Saving group settings for chat {chat_id}")
            
            # Ensure tenant exists
            tenant_id = await sql.ensure_tenant_for_chat(
                chat_id=int(chat_id),
                title=payload.get("title") or chat.title
            )
            
            # Save settings
            ok = await sql.save_group_settings(
                chat_id=int(chat_id),
                title=payload.get("title"),
                data=payload,
                updated_by=user.id
            )
            
            if ok:
                await msg.reply_text("✅ Gruppeneinstellungen gespeichert!")
                log.info(f"Settings saved for chat {chat_id} by user {user.id}")
            else:
                await msg.reply_text("⚠️ Konnte Einstellungen nicht speichern.")
        
        # Case 2: Ticket creation (for future use)
        else:
            await msg.reply_text("📝 Ticket-Daten empfangen. Nutze die Mini-App zum Erstellen.")
        
    except json.JSONDecodeError:
        log.exception("Failed to parse WebApp data")
        await msg.reply_text("❌ Konnte Daten nicht verarbeiten.")
    except Exception as e:
        log.exception(f"WebApp error: {e}")
        await msg.reply_text(f"❌ Fehler: {str(e)[:100]}")


def register(app: Application):
    """Register MiniApp handlers"""
    log.info("Registering MiniApp handlers...")
    
    app.add_handler(CommandHandler(["miniapp", "settings"], cmd_support))
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, on_web_app_data))
    
    log.info("✅ MiniApp handlers registered")
