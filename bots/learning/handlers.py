"""Learning Bot - Message & Command Handlers"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters, CallbackQueryHandler

logger = logging.getLogger(__name__)

try:
    from shared.emrd_rewards_integration import award_points
except ImportError:
    async def award_points(*args, **kwargs):
        pass


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Willkommensbefehl"""
    user = update.effective_user
    chat = update.effective_chat
    
    if chat.type == "private":
        await update.message.reply_text(
            "📚 **Emerald Academy**\n\n"
            "Lerne alles über Blockchain, DeFi und Crypto-Trading.\n\n"
            "🎓 Lernpfade:\n"
            "• Blockchain Basics\n"
            "• DeFi Strategien\n"
            "• Smart Contracts\n"
            "• Token Economics\n",
            parse_mode="Markdown"
        )
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "📖 Academy öffnen",
                web_app=WebAppInfo(url="https://greeny187.github.io/EmeraldContentBots/miniapp/applearning.html")
            )],
            [InlineKeyboardButton("❓ Hilfe", callback_data="learning_help")]
        ])
        await update.message.reply_text("Wähle eine Option:", reply_markup=keyboard)
    else:
        await update.message.reply_text("📚 Emerald Academy Bot aktiv!")


async def cmd_courses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List available courses"""
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 Anfänger", callback_data="courses_beginner")],
        [InlineKeyboardButton("🟡 Mittelstufe", callback_data="courses_intermediate")],
        [InlineKeyboardButton("🔴 Fortgeschrittene", callback_data="courses_advanced")]
    ])
    
    await update.message.reply_text(
        "📚 **Verfügbare Kurse**\n\n"
        "Wähle dein Level:",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )


async def cmd_progress(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user's learning progress"""
    user = update.effective_user
    
    await update.message.reply_text(
        "📊 **Dein Lernfortschritt**\n\n"
        "Öffne die Mini-App für detaillierte Statistiken.",
        parse_mode="Markdown"
    )


async def cmd_rewards(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show learning rewards"""
    user = update.effective_user
    
    await update.message.reply_text(
        "🏆 **Learning Rewards**\n\n"
        "Verdiene EMRD durch:\n"
        "✅ Kurse abschließen\n"
        "✅ Quizze lösen\n"
        "✅ Zertifikate erhalten\n\n"
        "Öffne die Mini-App zum Claimen.",
        parse_mode="Markdown"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help"""
    help_text = """
📚 **Learning Bot - Hilfe**

*Befehle:*
/start - Willkommen
/courses - Verfügbare Kurse
/progress - Dein Fortschritt
/rewards - Verdiente EMRD

*Mini-App Features:*
📖 Kursvideos
❓ Interaktive Quizze
🏆 Abzeichen & Zertifikate
💰 Reward-System
"""
    await update.message.reply_text(help_text, parse_mode="Markdown")


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Button callbacks"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "learning_help":
        await cmd_help(update, context)
    elif query.data.startswith("courses_"):
        level = query.data.split("_")[1]
        await query.edit_message_text(
            f"📚 **{level.capitalize()} Kurse**\n\n"
            "Öffne die Mini-App für den vollständigen Katalog.",
            parse_mode="Markdown"
        )


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Text handler"""
    user = update.effective_user
    
    try:
        await award_points(user.id, "message_sent", update.effective_chat.id)
    except Exception:
        pass
    
    if "help" in update.message.text.lower():
        await cmd_help(update, context)
    else:
        await update.message.reply_text(
            "ℹ️ Nutze /courses oder öffne die Mini-App.\n"
            "Tippe /help für Informationen."
        )


def register_handlers(app):
    """Register handlers"""
    app.add_handler(CommandHandler("start", cmd_start), group=0)
    app.add_handler(CommandHandler("learning", cmd_start), group=0)
    app.add_handler(CommandHandler("courses", cmd_courses), group=0)
    app.add_handler(CommandHandler("progress", cmd_progress), group=0)
    app.add_handler(CommandHandler("rewards", cmd_rewards), group=0)
    app.add_handler(CommandHandler("help", cmd_help), group=0)
    
    app.add_handler(CallbackQueryHandler(button_callback), group=1)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler), group=2)
