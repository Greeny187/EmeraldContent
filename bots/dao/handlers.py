"""DAO Bot - Governance & Voting"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters, CallbackQueryHandler
from datetime import datetime, timedelta
import os

logger = logging.getLogger(__name__)

try:
    from shared.emrd_rewards_integration import award_points
except ImportError:
    async def award_points(*args, **kwargs):
        pass

from .database import (
    create_proposal, get_active_proposals, get_proposal_details, cast_vote,
    get_user_voting_power, delegate_voting_power, get_vote_statistics,
    get_treasury_balance, get_treasury_transactions, update_user_voting_power,
    create_treasury_transaction, get_user_voting_power_detailed, get_user_vote,
    get_delegations
)


# Emerald Farben und Styling
EMERALD_GREEN = "#00D084"
EMERALD_DARK = "#0A2E1F"
EMERALD_LIGHT = "#E8F5F0"


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """DAO Governance Bot Start"""
    user = update.effective_user
    chat = update.effective_chat
    
    if chat.type == "private":
        await update.message.reply_text(
            "🏛️ **Emerald DAO Governance**\n\n"
            "Willkommen zur Dezentralen Governance des Emerald Ökosystems!\n\n"
            "🌿 Features:\n"
            "• 🗳️ Abstimmungen an Proposals\n"
            "• 📝 Neue Proposals erstellen\n"
            "• 💰 Treasury Management\n"
            "• 🤝 Voting Power delegieren\n"
            "• 📊 Live Voting Statistiken\n\n"
            "_Öffne die Mini-App für die vollständige Governance Experience._",
            parse_mode="Markdown"
        )
        
        miniapp_url = os.getenv("DAO_MINIAPP_URL", "https://emerald-dao.example.com/app")
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "🏛️ DAO öffnen",
                web_app=WebAppInfo(url=miniapp_url)
            )],
            [
                InlineKeyboardButton("📋 Proposals", callback_data="dao_proposals"),
                InlineKeyboardButton("🗳️ Voting Power", callback_data="dao_voting_power")
            ],
            [
                InlineKeyboardButton("💰 Treasury", callback_data="dao_treasury"),
                InlineKeyboardButton("❓ Hilfe", callback_data="dao_help")
            ]
        ])
        await update.message.reply_text("Wähle eine Option:", reply_markup=keyboard)
    else:
        await update.message.reply_text("🏛️ **DAO Governance aktiv!**\nNutze den Bot im privaten Chat für volle Funktionalität.", parse_mode="Markdown")


async def cmd_proposals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List active proposals"""
    try:
        proposals = get_active_proposals()
        
        if not proposals:
            await update.message.reply_text("📋 Keine aktiven Proposals vorhanden.")
            return
        
        text = "📋 **Aktive Proposals**\n\n"
        
        for i, prop in enumerate(proposals[:5], 1):
            status_emoji = "🟢" if prop['status'] == 'active' else "🔴"
            text += f"{status_emoji} **{prop['title']}**\n"
            text += f"└ Votes: {prop['votes']} | Status: {prop['status']}\n\n"
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 Details", callback_data="dao_details")],
            [InlineKeyboardButton("🏛️ Zur Mini-App", callback_data="dao_open_app")],
        ])
        
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Proposals command error: {e}")
        await update.message.reply_text("❌ Fehler beim Laden der Proposals.")


async def cmd_voting_power(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show voting power"""
    user = update.effective_user
    
    try:
        voting_power = get_user_voting_power_detailed(user.id)
        
        if voting_power:
            text = f"🗳️ **Deine Voting Power**\n\n"
            text += f"💚 EMRD Balance: **{voting_power['emrd_balance']:,.0f}**\n"
            text += f"➡️  Delegiert: {voting_power['delegated_power']:,.0f}\n"
            text += f"⬅️  Erhalten: {voting_power['received_delegations']:,.0f}\n"
            text += f"━━━━━━━━━━━━━━\n"
            text += f"📈 **Gesamt: {voting_power['total_power']:,.0f}**\n\n"
            text += f"_Zuletzt aktualisiert: {voting_power['updated_at']}_"
        else:
            text = "🗳️ **Voting Power**\n\n"
            text += "Öffne die Mini-App um deine Voting Power zu sehen und zu verwalten."
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🏛️ Zur Mini-App", callback_data="dao_open_app")],
        ])
        
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Voting power command error: {e}")
        await update.message.reply_text("❌ Fehler beim Laden der Voting Power.")


async def cmd_treasury(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show treasury info"""
    try:
        balance = get_treasury_balance()
        transactions = get_treasury_transactions(limit=5)
        
        text = f"💰 **DAO Treasury**\n\n"
        text += f"💚 Verfügbar: **{balance:,.2f} EMRD**\n"
        text += f"━━━━━━━━━━━━━━━\n"
        
        if transactions:
            text += f"\n**Letzte Transaktionen:**\n"
            for tx in transactions[:3]:
                icon = "➕" if tx['type'] == 'deposit' else "➖"
                status_icon = "✅" if tx['status'] == 'approved' else "⏳"
                text += f"{status_icon} {icon} {tx['amount']:,.2f} EMRD\n"
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 Treasury Details", callback_data="dao_treasury_details")],
            [InlineKeyboardButton("🏛️ Zur Mini-App", callback_data="dao_open_app")],
        ])
        
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Treasury command error: {e}")
        await update.message.reply_text("❌ Fehler beim Laden des Treasury.")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help"""
    help_text = """🏛️ **DAO Bot - Hilfe**

**Befehle:**
/start - Willkommen
/proposals - Aktive Abstimmungen
/voting - Deine Voting Power
/treasury - Treasury Info
/help - Diese Hilfe

**Voting System:**
• 1 EMRD = 1 Vote
• Minimum: 100 EMRD zum Abstimmen
• Delegationen erhöhen deine Voting Power

**Proposal Typen:**
🔄 Parameter Change - Systemparameter ändern
💰 Treasury Spend - Geldausgaben
🏛️ Governance - Governance Regeln
📊 Analytics - Datenerfassung

**Quorum Requirements:**
• Minimum Votes: 100,000 EMRD
• Voting Duration: 3 Tage
• Gültig wenn YES > 50%

_Nutze die Mini-App für vollständige Kontrolle und Echtzeit-Updates!_"""
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏛️ Zur Mini-App", callback_data="dao_open_app")],
    ])
    
    await update.message.reply_text(help_text, reply_markup=keyboard, parse_mode="Markdown")


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Button callbacks"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "dao_help":
        await cmd_help(update, context)
    elif query.data == "dao_proposals":
        await cmd_proposals(update, context)
    elif query.data == "dao_voting_power":
        await cmd_voting_power(update, context)
    elif query.data == "dao_treasury":
        await cmd_treasury(update, context)
    elif query.data == "dao_open_app":
        miniapp_url = os.getenv("DAO_MINIAPP_URL", "https://emerald-dao.example.com/app")
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "🏛️ DAO öffnen",
                web_app=WebAppInfo(url=miniapp_url)
            )],
        ])
        await query.edit_message_text(
            "🏛️ **Öffne die DAO Mini-App**\n\nKlicke auf den Button um zur vollständigen Governance-Oberfläche zu gelangen.",
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
    elif query.data == "dao_treasury_details":
        await cmd_treasury(update, context)
    elif query.data == "dao_details":
        await cmd_proposals(update, context)


def register_handlers(app):
    """Register handlers"""
    app.add_handler(CommandHandler("start", cmd_start), group=0)
    app.add_handler(CommandHandler("dao", cmd_start), group=0)
    app.add_handler(CommandHandler("proposals", cmd_proposals), group=0)
    app.add_handler(CommandHandler("voting", cmd_voting_power), group=0)
    app.add_handler(CommandHandler("treasury", cmd_treasury), group=0)
    app.add_handler(CommandHandler("help", cmd_help), group=0)
    
    app.add_handler(CallbackQueryHandler(button_callback), group=1)
    
    logger.info("DAO handlers registered")
