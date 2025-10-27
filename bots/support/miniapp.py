# miniapp.py — PTB v20
import os, json, logging
from typing import Optional
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, Application, filters

import sql  # nutzt deine vorhandene sql.py

log = logging.getLogger("bot.support.miniapp")

WEBAPP_URL = os.getenv(
    "SUPPORT_WEBAPP_URL",
    # z.B. GitHub Pages Pfad auf appsupport.html
    "https://greeny187.github.io/EmeraldContentBots/miniapp/appsupport.html"
)

async def cmd_support(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔧 Support Mini-App öffnen", web_app=WebAppInfo(url=WEBAPP_URL))
    ]])
    await update.message.reply_text(
        "👋 *Emerald Support*\n\nÖffne die Mini-App, um Einstellungen zu bearbeiten oder ein Ticket zu erstellen.",
        reply_markup=kb, parse_mode="Markdown"
    )

# Alias
async def cmd_miniapp(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    return await cmd_support(update, ctx)

async def on_web_app_data(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    Wird ausgelöst, wenn die MiniApp tg.sendData(JSON) sendet.
    Erwartet ein JSON mit Feldern wie in appsupport.html collect(): { cid, title, ... }.
    """
    msg = update.effective_message
    try:
        raw = msg.web_app_data.data
        payload = json.loads(raw)
    except Exception as e:
        log.exception("web_app_data parse error")
        return await msg.reply_text("❌ Konnte Daten nicht lesen.")

    # Sicherheits-/Fallbacks
    by_user = update.effective_user.id if update.effective_user else None
    chat_id = payload.get("cid") or (update.effective_chat.id if update.effective_chat else None)

    if not chat_id:
        return await msg.reply_text("❌ Keine Gruppen-ID (cid) übergeben.")

    # Speichern
    ok = await sql.save_group_settings(
        chat_id=int(chat_id),
        title=payload.get("title"),
        data=payload,
        updated_by=by_user
    )
    if ok:
        await msg.reply_text("✅ Einstellungen gespeichert.")
    else:
        await msg.reply_text("⚠️ Konnte nicht speichern.")

    tenant_id = await sql.ensure_tenant_for_chat(chat_id= int(chat_id), title= payload.get("title"))


def register(app: Application):
    app.add_handler(CommandHandler(["support", "miniapp"], cmd_support))
    app.add_handler(CommandHandler("miniapp_open", cmd_miniapp))
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, on_web_app_data))
