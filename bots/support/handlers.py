"""Support Bot - Telegram Handlers (v1.0)"""

import logging
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters
import uuid

logger = logging.getLogger(__name__)

# Webapp URL
WEBAPP_URL = os.getenv(
    "SUPPORT_WEBAPP_URL",
    "https://greeny187.github.io/EmeraldContentBots/miniapp/appsupport.html"
)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Willkommensbefehl - Starte Support Bot"""
    user = update.effective_user
    chat = update.effective_chat
    
    text = (
        "🎫 **Emerald Support**\n\n"
        "Willkommen! Hier kannst du Support-Tickets erstellen und verwalten.\n\n"
        "**Wie es funktioniert:**\n"
        "1. Öffne die Mini-App\n"
        "2. Wähle eine Kategorie\n"
        "3. Beschreibe dein Problem\n"
        "4. Warte auf Support-Antwort\n"
    )
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔧 Support Mini-App öffnen", web_app=WebAppInfo(url=WEBAPP_URL))],
        [InlineKeyboardButton("❓ FAQ", callback_data="support_faq")],
        [InlineKeyboardButton("📋 Meine Tickets", callback_data="support_tickets")]
    ])
    
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)


async def cmd_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Erstelle Support-Ticket - öffne Mini-App"""
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Neues Ticket", web_app=WebAppInfo(url=WEBAPP_URL + "?tab=create"))]
    ])
    
    await update.message.reply_text(
        "📝 **Neues Support-Ticket**\n\nÖffne die Mini-App, um ein neues Ticket zu erstellen.",
        parse_mode="Markdown",
        reply_markup=keyboard
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Überprüfe meine Tickets"""
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Meine Tickets", web_app=WebAppInfo(url=WEBAPP_URL + "?tab=mine"))]
    ])
    
    await update.message.reply_text(
        "🔍 **Meine Tickets überprüfen**\n\nÖffne die Mini-App um deine Tickets zu sehen.",
        parse_mode="Markdown",
        reply_markup=keyboard
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Zeige Hilfe"""
    help_text = """
🎫 **Emerald Support Bot - Hilfe**

**Verfügbare Befehle:**
/start - Willkommen
/ticket - Neues Ticket erstellen
/status - Meine Tickets überprüfen
/help - Diese Hilfe

**Kategorien:**
• Allgemein - Allgemeine Fragen
• Technik - Technische Probleme
• Zahlungen - Rechnungs-/Zahlungsfragen
• Konto - Konto-Verwaltung
• Feedback - Dein Feedback

**Antwortzeiten (SLA):**
⚡ Kritisch: < 1 Stunde
🟠 Hoch: < 4 Stunden
🟡 Normal: < 24 Stunden

**Support-Zeiten:**
Montag–Freitag, 9–18 Uhr CET
"""
    await update.message.reply_text(help_text, parse_mode="Markdown")


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle Button Callbacks"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "support_faq":
        faq_text = """
❓ **Häufig gestellte Fragen**

**Wie erstelle ich ein Ticket?**
Nutze den /ticket Befehl oder öffne die Mini-App.

**Wie lange dauert Support?**
Normalerweise 24 Stunden (abhängig von Priorität).

**Kann ich mein Ticket nachverfolgen?**
Ja, mit dem /status Befehl oder in der Mini-App.

**Welche Kategorien gibt es?**
Allgemein, Technik, Zahlungen, Konto, Feedback.

**Was ist eine Antwort auf ein Ticket?**
Du kannst weitere Informationen ergänzen, bevor wir antworten.
"""
        await query.edit_message_text(faq_text, parse_mode="Markdown")
    
    elif query.data == "support_tickets":
        await query.edit_message_text(
            "📋 Öffne die Mini-App um deine Tickets zu sehen.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔧 Mini-App öffnen", web_app=WebAppInfo(url=WEBAPP_URL))]
            ])
        )


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle Text-Messages - Directed to Mini-App"""
    user = update.effective_user
    message = update.message
    
    text = (
        "Für Support nutze bitte die Mini-App oder die verfügbaren Befehle:\n\n"
        "/ticket - Neues Support-Ticket\n"
        "/status - Meine Tickets überprüfen\n"
        "/help - Hilfe\n"
    )
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔧 Support öffnen", web_app=WebAppInfo(url=WEBAPP_URL))]
    ])
    
    await message.reply_text(text, reply_markup=keyboard)


def register_handlers(app):
    """Registriere alle Handler"""
    # Commands
    app.add_handler(CommandHandler(["start", "support"], cmd_start), group=0)
    app.add_handler(CommandHandler("ticket", cmd_ticket), group=0)
    app.add_handler(CommandHandler("status", cmd_status), group=0)
    app.add_handler(CommandHandler("help", cmd_help), group=0)
    
    # Callbacks
    app.add_handler(CommandHandler("callback", button_callback), group=1)
    
    # Text messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler), group=2)


# Für Integration in main bot.py
def register(app):
    """Registriere Handler (wird aus bot.py aufgerufen)"""
    register_handlers(app)
    logger.info("Support handlers registered")


def register_jobs(app):
    """Registriere geplante Jobs (optional)"""
    logger.info("Support jobs registered (empty for v1.0)")
