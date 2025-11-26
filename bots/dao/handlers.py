"""DAO Bot - Governance & Voting"""

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
    """DAO Governance Bot"""
    user = update.effective_user
    chat = update.effective_chat
    
    if chat.type == "private":
        await update.message.reply_text(
            "🏛️ **Emerald DAO Governance**\n\n"
            "Beteilige dich an wichtigen Entscheidungen!\n\n"
            "🗳️ Features:\n"
            "• Abstimmungen\n"
            "• Proposals\n"
            "• Treasury Management\n"
            "• Voting Power\n",
            parse_mode="Markdown"
        )
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "🏛️ DAO öffnen",
                web_app=WebAppInfo(url="https://greeny187.github.io/EmeraldContentBots/miniapp/appdao.html")
            )],
            [InlineKeyboardButton("❓ Hilfe", callback_data="dao_help")]
        ])
        await update.message.reply_text("Wähle eine Option:", reply_markup=keyboard)
    else:
        await update.message.reply_text("🏛️ DAO Governance aktiv!")


async def cmd_proposals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List active proposals"""
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Abstimmungen", callback_data="dao_proposals")],
        [InlineKeyboardButton("📝 Neue Proposal", callback_data="dao_new_proposal")],
    ])
    
    await update.message.reply_text(
        "📋 **Governance**\n\n"
        "Aktive Abstimmungen: 3\n",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )


async def cmd_voting_power(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show voting power"""
    user = update.effective_user
    
    await update.message.reply_text(
        "🗳️ **Deine Voting Power**\n\n"
        "Öffne die Mini-App für Details.",
        parse_mode="Markdown"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help"""
    help_text = """
🏛️ **DAO Bot - Hilfe**

*Befehle:*
/start - Willkommen
/proposals - Abstimmungen
/voting - Voting Power
/treasury - Treasury Info

*Voting:*
1 EMRD = 1 Vote
Minimum: 100 EMRD
"""
    await update.message.reply_text(help_text, parse_mode="Markdown")


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Button callbacks"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "dao_help":
        await cmd_help(update, context)


def register_handlers(app):
    """Register handlers"""
    app.add_handler(CommandHandler("start", cmd_start), group=0)
    app.add_handler(CommandHandler("dao", cmd_start), group=0)
    app.add_handler(CommandHandler("proposals", cmd_proposals), group=0)
    app.add_handler(CommandHandler("voting", cmd_voting_power), group=0)
    app.add_handler(CommandHandler("help", cmd_help), group=0)
    
    app.add_handler(CallbackQueryHandler(button_callback), group=1)
