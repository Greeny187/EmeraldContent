"""Trade API Bot - Message & Command Handlers"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters, CallbackQueryHandler
from telegram.constants import ChatType

logger = logging.getLogger(__name__)

# Importiere gemeinsame Funktionen
try:
    from shared.emrd_rewards_integration import award_points
    from shared.logger import setup_logger
except ImportError:
    logger.warning("Shared modules not available")
    
    async def award_points(*args, **kwargs):
        pass


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Willkommensbefehl für Trade API Bot"""
    user = update.effective_user
    chat = update.effective_chat
    
    if chat.type == "private":
        # Direkt zur Miniapp-Überleitung
        await update.message.reply_text(
            "🚀 **Emerald Trade API**\n\n"
            "Verbinde deine Trading-Strategien, analysiere Märkte und verwalte dein Portfolio.\n\n"
            "⚡ Funktionen:\n"
            "• Live Marktdaten & Charts\n"
            "• Portfolio Analytics\n"
            "• Signale & Alerts\n"
            "• On-Chain Proof\n",
            parse_mode="Markdown"
        )
        
        # Miniapp Button
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "📊 Trade API öffnen",
                web_app=WebAppInfo(url="https://greeny187.github.io/EmeraldContentBots/miniapp/apptradeapi.html")
            )],
            [InlineKeyboardButton("❓ Hilfe", callback_data="tradeapi_help")]
        ])
        await update.message.reply_text("Wähle eine Option:", reply_markup=keyboard)
    else:
        await update.message.reply_text(
            "👋 Trade API Bot aktiv in dieser Gruppe!\n"
            "Nutze /tradeapi in privaten Chat für vollständige Funktionen."
        )


async def cmd_portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Zeige Portfolio-Übersicht"""
    user = update.effective_user
    
    try:
        await update.message.reply_text(
            "📈 **Dein Portfolio**\n\n"
            "Öffne die Mini-App für detaillierte Analysen.",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Error in portfolio command: {e}")
        await update.message.reply_text("❌ Fehler beim Laden des Portfolios")


async def cmd_signals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Zeige aktuelle Trading Signale"""
    user = update.effective_user
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 BUY Signale", callback_data="signals_buy")],
        [InlineKeyboardButton("🔴 SELL Signale", callback_data="signals_sell")],
        [InlineKeyboardButton("⚠️ Alle Alerts", callback_data="signals_all")]
    ])
    
    await update.message.reply_text(
        "📡 **Trading Signale**\n\n"
        "Wähle einen Filter:",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Zeige Hilfeinformationen"""
    help_text = """
🔍 **Trade API Bot - Hilfe**

*Befehle:*
/start - Willkommen & Überblick
/portfolio - Portfolio-Status
/signals - Trading Signale
/settings - Einstellungen

*In der Mini-App:*
📊 Dashboard - Marktübersicht
💼 Portfolio - Deine Assets
🔔 Alerts - Preisbenachrichtigungen
⚙️ Settings - Konfiguration

*Rewards:*
Verdiene EMRD durch aktive Portfolio-Verwaltung!
"""
    await update.message.reply_text(help_text, parse_mode="Markdown")


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Button Callback Handler"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "tradeapi_help":
        await cmd_help(update, context)
    elif query.data.startswith("signals_"):
        signal_type = query.data.split("_")[1]
        await query.edit_message_text(
            f"📡 **{signal_type.upper()} Signale**\n\n"
            "Aktuell keine Signale verfügbar.",
            parse_mode="Markdown"
        )


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fallback Text Handler"""
    user = update.effective_user
    
    # Award points für Message
    try:
        await award_points(user.id, "message_sent", update.effective_chat.id)
    except Exception:
        pass
    
    # Einfache Antwort
    if "help" in update.message.text.lower():
        await cmd_help(update, context)
    else:
        await update.message.reply_text(
            "ℹ️ Nutze die Befehle oder öffne die Mini-App für alle Funktionen.\n"
            "Tippe /help für Informationen."
        )


def register_handlers(app):
    """Registriere alle Handler"""
    app.add_handler(CommandHandler("start", cmd_start), group=0)
    app.add_handler(CommandHandler("tradeapi", cmd_start), group=0)
    app.add_handler(CommandHandler("portfolio", cmd_portfolio), group=0)
    app.add_handler(CommandHandler("signals", cmd_signals), group=0)
    app.add_handler(CommandHandler("help", cmd_help), group=0)
    
    app.add_handler(CallbackQueryHandler(button_callback), group=1)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler), group=2)
