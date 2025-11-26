"""Trade Dex Bot - Message & Command Handlers"""

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
    """Willkommensbefehl für Trade Dex Bot"""
    user = update.effective_user
    chat = update.effective_chat
    
    if chat.type == "private":
        await update.message.reply_text(
            "🔄 **Emerald Trade DEX**\n\n"
            "Dezentralisierte Börsenintegration für TON & andere Blockchains.\n\n"
            "⚡ Funktionen:\n"
            "• DEX Swaps (STON.fi, Dedust, etc.)\n"
            "• Liquidity Pools\n"
            "• Price Impact Calculator\n"
            "• Automated Strategies\n",
            parse_mode="Markdown"
        )
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "💱 DEX öffnen",
                web_app=WebAppInfo(url="https://greeny187.github.io/EmeraldContentBots/miniapp/apptradedex.html")
            )],
            [InlineKeyboardButton("❓ Hilfe", callback_data="dex_help")]
        ])
        await update.message.reply_text("Wähle eine Option:", reply_markup=keyboard)
    else:
        await update.message.reply_text("🔄 Trade DEX Bot aktiv!")


async def cmd_swap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Token Swap Command"""
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔀 Swap durchführen", callback_data="dex_swap_start")],
        [InlineKeyboardButton("💧 Liquidity Pools", callback_data="dex_pools")],
    ])
    
    await update.message.reply_text(
        "💱 **DEX Trading**\n\n"
        "Wähle eine Aktion:",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )


async def cmd_pools(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show liquidity pools"""
    await update.message.reply_text(
        "💧 **Liquidity Pools**\n\n"
        "Top Pools:\n"
        "• EMRD/TON\n"
        "• USDT/TON\n"
        "• stTON/TON\n\n"
        "Öffne die Mini-App für Details.",
        parse_mode="Markdown"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help"""
    help_text = """
💱 **Trade DEX Bot - Hilfe**

*Befehle:*
/start - Willkommen
/swap - Token Tausch
/pools - Liquidity Pools
/strategies - Auto-Strategien

*Mini-App Features:*
💱 Swap Interface
💧 Pool Analytics
📊 Price Charts
⚙️ Custom Strategies

*Tipps:*
• Nutze slippage protection
• Check gas fees
• Verdiene EMRD durch Trades!
"""
    await update.message.reply_text(help_text, parse_mode="Markdown")


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Button callbacks"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "dex_help":
        await cmd_help(update, context)
    elif query.data == "dex_swap_start":
        await query.edit_message_text(
            "🔀 **Swap Vorbereitung**\n\n"
            "Öffne die Mini-App für den vollständigen Swap-Prozess.",
            parse_mode="Markdown"
        )


def register_handlers(app):
    """Register handlers"""
    app.add_handler(CommandHandler("start", cmd_start), group=0)
    app.add_handler(CommandHandler("dex", cmd_start), group=0)
    app.add_handler(CommandHandler("swap", cmd_swap), group=0)
    app.add_handler(CommandHandler("pools", cmd_pools), group=0)
    app.add_handler(CommandHandler("help", cmd_help), group=0)
    
    app.add_handler(CallbackQueryHandler(button_callback), group=1)
