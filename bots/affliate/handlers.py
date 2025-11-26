"""Affiliate Bot - Referral System"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler

logger = logging.getLogger(__name__)

try:
    from shared.emrd_rewards_integration import award_points
except ImportError:
    async def award_points(*args, **kwargs):
        pass


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiliate Bot - Referral Program"""
    user = update.effective_user
    chat = update.effective_chat
    
    if chat.type == "private":
        await update.message.reply_text(
            "💰 **Emerald Affiliate Program**\n\n"
            "Verdiene EMRD mit Referrals!\n\n"
            "🎁 Features:\n"
            "• Einzigartige Links\n"
            "• Tracking\n"
            "• Provisionen\n"
            "• Statistiken\n",
            parse_mode="Markdown"
        )
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "💰 Dashboard",
                web_app=WebAppInfo(url="https://greeny187.github.io/EmeraldContentBots/miniapp/appaffiliate.html")
            )],
            [InlineKeyboardButton("📊 Stats", callback_data="aff_stats")],
            [InlineKeyboardButton("❓ Hilfe", callback_data="aff_help")]
        ])
        await update.message.reply_text("Wähle eine Option:", reply_markup=keyboard)
    else:
        await update.message.reply_text("💰 Affiliate Program aktiv!")


async def cmd_my_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Get affiliate link"""
    user = update.effective_user
    
    link = f"https://t.me/emerald_bot?start=aff_{user.id}"
    
    await update.message.reply_text(
        f"🔗 **Dein Affiliate Link**\n\n"
        f"`{link}`\n\n"
        f"Teile ihn und verdiene Provisionen!",
        parse_mode="Markdown"
    )


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Get referral stats"""
    user = update.effective_user
    
    await update.message.reply_text(
        "📊 **Deine Affiliate Stats**\n\n"
        "Öffne das Dashboard für Details.",
        parse_mode="Markdown"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help"""
    help_text = """
💰 **Affiliate Bot - Hilfe**

*Befehle:*
/start - Willkommen
/link - Affiliate Link
/stats - Statistiken
/payouts - Auszahlungen

*Provisionen:*
• Neue User: 10 EMRD
• Aktive User: 5% Rewards
• Premium: 15% Rewards

*Minimum Payout:* 1000 EMRD
"""
    await update.message.reply_text(help_text, parse_mode="Markdown")


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Button callbacks"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "aff_help":
        await cmd_help(update, context)
    elif query.data == "aff_stats":
        await cmd_stats(update, context)


def register_handlers(app):
    """Register handlers"""
    app.add_handler(CommandHandler("start", cmd_start), group=0)
    app.add_handler(CommandHandler("affiliate", cmd_start), group=0)
    app.add_handler(CommandHandler("link", cmd_my_link), group=0)
    app.add_handler(CommandHandler("stats", cmd_stats), group=0)
    app.add_handler(CommandHandler("help", cmd_help), group=0)
    
    app.add_handler(CallbackQueryHandler(button_callback), group=1)
