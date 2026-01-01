"""Trade Dex Bot - Message & Command Handlers"""

import logging
from decimal import Decimal
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters, CallbackQueryHandler
import json

logger = logging.getLogger(__name__)

try:
    from shared.emrd_rewards_integration import award_points
except ImportError:
    async def award_points(*args, **kwargs):
        pass

try:
    from . import database
    from .exchange_service import create_exchange_service
except ImportError as e:
    logger.error(f"Import error: {e}")
    database = None


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Welcome command for Trade DEX Bot"""
    user = update.effective_user
    chat = update.effective_chat
    
    if chat.type == "private":
        await update.message.reply_text(
            "🔄 **Emerald Trade DEX**\n\n"
            "⚡ Dezentralisierte Börsenintegration mit:\n"
            "• 🥞 PancakeSwap (BSC)\n"
            "• 🌪️ Aerodome (Evmos)\n"
            "• 🏪 OKX (Price Data & Markets)\n\n"
            "💱 **Funktionen:**\n"
            "• Token Swaps mit bestem Kurs\n"
            "• Liquidity Pool Management\n"
            "• Price Alerts & Notifications\n"
            "• Automated Trading Strategies\n"
            "• Portfolio Analytics\n"
            "• 24h Volume & Stats",
            parse_mode="Markdown"
        )
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "💱 DEX App öffnen",
                web_app=WebAppInfo(url="https://greeny187.github.io/EmeraldContentBots/miniapp/apptradedex.html")
            )],
            [
                InlineKeyboardButton("💱 Swap", callback_data="dex_swap_start"),
                InlineKeyboardButton("💧 Pools", callback_data="dex_pools")
            ],
            [
                InlineKeyboardButton("📊 Markets", callback_data="dex_markets"),
                InlineKeyboardButton("⚠️ Alerts", callback_data="dex_alerts")
            ],
            [
                InlineKeyboardButton("⚙️ Strategies", callback_data="dex_strategies"),
                InlineKeyboardButton("❓ Hilfe", callback_data="dex_help")
            ]
        ])
        await update.message.reply_text("Wähle eine Option:", reply_markup=keyboard)
    else:
        await update.message.reply_text("🔄 Trade DEX Bot aktiv! Nutze /dex im privaten Chat.")


async def cmd_swap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Initiate token swap"""
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🥞 PancakeSwap", callback_data="swap_pancake")],
        [InlineKeyboardButton("🌪️ Aerodome", callback_data="swap_aerodome")],
        [InlineKeyboardButton("🏪 Vergleich", callback_data="swap_compare")]
    ])
    
    await update.message.reply_text(
        "💱 **Token Swap**\n\n"
        "Wähle ein DEX:",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    
    try:
        await award_points(update.effective_user.id, "swap_initiated", update.effective_chat.id)
    except:
        pass


async def cmd_pools(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show available liquidity pools"""
    try:
        if database:
            pools = database.get_top_pools(limit=10)
            
            if pools:
                pool_text = "💧 **Top Liquidity Pools**\n\n"
                for idx, pool in enumerate(pools, 1):
                    pool_text += (
                        f"{idx}. {pool['symbol_a']}/{pool['symbol_b']} ({pool['dex_name']})\n"
                        f"   TVL: ${pool['tvl_usd']:,.0f} | "
                        f"APR: {pool.get('apr', 0):.2f}%\n"
                    )
                
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("➕ Liquidity bereitstellen", callback_data="dex_add_liquidity")],
                    [InlineKeyboardButton("📊 Meine Positionen", callback_data="dex_my_positions")]
                ])
                
                await update.message.reply_text(pool_text, parse_mode="Markdown", reply_markup=keyboard)
            else:
                await update.message.reply_text("Keine Pools gefunden.")
    except Exception as e:
        logger.error(f"Error in pools command: {e}")
        await update.message.reply_text("❌ Fehler beim Laden der Pools")


async def cmd_markets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show market data from OKX"""
    try:
        service = await create_exchange_service()
        
        # Get some top tokens
        tokens = ["BTC", "ETH", "EMRD", "TON", "SOL"]
        prices = await service.get_prices_multi(tokens)
        
        market_text = "📊 **Marktübersicht (OKX)**\n\n"
        for token in tokens:
            if token.lower() in prices:
                market_text += f"{token}: ${prices[token.lower()]:,.2f}\n"
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📈 Charts", callback_data="dex_charts")],
            [InlineKeyboardButton("🔔 Price Alerts", callback_data="dex_alerts")]
        ])
        
        await update.message.reply_text(market_text, parse_mode="Markdown", reply_markup=keyboard)
        
        await service.close()
    except Exception as e:
        logger.error(f"Error in markets command: {e}")
        await update.message.reply_text("❌ Fehler beim Laden der Marktdaten")


async def cmd_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manage price alerts"""
    user_id = update.effective_user.id
    
    try:
        if database:
            alerts = database.get_user_alerts(user_id)
            
            if alerts:
                alert_text = "⚠️ **Deine Alerts**\n\n"
                for alert in alerts:
                    alert_text += (
                        f"• {alert['symbol']}: {alert['condition_type']} "
                        f"${alert['condition_value']}\n"
                    )
                alert_text += "\n"
            else:
                alert_text = "⚠️ **Keine Alerts erstellt**\n\n"
            
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Neuer Alert", callback_data="dex_alert_add")],
                [InlineKeyboardButton("🗑️ Alert löschen", callback_data="dex_alert_delete")]
            ])
            
            await update.message.reply_text(alert_text, parse_mode="Markdown", reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Error in alerts command: {e}")
        await update.message.reply_text("❌ Fehler beim Laden der Alerts")


async def cmd_strategies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manage automated trading strategies"""
    user_id = update.effective_user.id
    
    try:
        if database:
            strategies = database.get_user_strategies(user_id)
            
            if strategies:
                strat_text = "⚙️ **Deine Strategien**\n\n"
                for strat in strategies:
                    status = "🟢 Aktiv" if strat['active'] else "🔴 Inaktiv"
                    strat_text += (
                        f"• {strat['name']} ({strat['strategy_type']}) {status}\n"
                        f"  DEX: {strat['dex_name']} | "
                        f"{strat['token_from']} → {strat['token_to']}\n"
                    )
                strat_text += "\n"
            else:
                strat_text = "⚙️ **Keine Strategien erstellt**\n\n"
            
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Neue Strategie", callback_data="dex_strat_add")],
                [InlineKeyboardButton("🔧 Verwalten", callback_data="dex_strat_manage")]
            ])
            
            await update.message.reply_text(strat_text, parse_mode="Markdown", reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Error in strategies command: {e}")
        await update.message.reply_text("❌ Fehler beim Laden der Strategien")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help information"""
    help_text = """
💱 **Trade DEX Bot - Hilfe**

*Verfügbare Befehle:*
/start - Willkommen & Überblick
/dex - DEX App öffnen
/swap - Token tauschen
/pools - Liquidity Pools
/markets - Marktübersicht
/alerts - Price Alerts
/strategies - Trading Strategien
/settings - Einstellungen
/portfolio - Portfolio Übersicht

*Unterstützte Börsen:*
🥞 **PancakeSwap** (BSC) - DEX Swaps, Liquidity Pools
🌪️ **Aerodome** (Evmos) - DEX Swaps, Yield Farming
🏪 **OKX** - Marktdaten, Charts, Spot Trading

*Strategien:*
• 💰 DCA (Dollar Cost Averaging)
• 📊 Grid Trading
• ⏰ Scheduled Swaps
• 🎯 Limit Orders
• 💧 Auto-Compounding (Liquidity)

*Gebühren:*
• PancakeSwap: 0.25% - 1%
• Aerodome: 0.3% - 0.5%
• Netzwerk-Gebühren variabel

*Rewards:*
🎁 Verdiene EMRD durch:
• Swaps durchführen
• Liquidity bereitstellen
• Strategien nutzen
• Portfolio-Verwaltung

*Support:*
❓ Fragen? /help
🆘 Probleme? Support-Team kontaktieren
"""
    await update.message.reply_text(help_text, parse_mode="Markdown")


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    callback_data = query.data
    
    # Swap callbacks
    if callback_data == "dex_swap_start":
        await query.edit_message_text(
            "💱 **Token Swap - Auswahl**\n\n"
            "Wähle ein DEX für den besten Kurs.",
            parse_mode="Markdown"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🥞 PancakeSwap", callback_data="swap_pancake")],
            [InlineKeyboardButton("🌪️ Aerodome", callback_data="swap_aerodome")],
            [InlineKeyboardButton("🔄 Vergleichen", callback_data="swap_compare")]
        ])
        await query.edit_message_reply_markup(keyboard)
    
    # Pools callbacks
    elif callback_data == "dex_pools":
        try:
            if database:
                pools = database.get_top_pools(limit=5)
                if pools:
                    pool_text = "💧 **Top 5 Pools**\n\n"
                    for pool in pools:
                        pool_text += f"• {pool['symbol_a']}/{pool['symbol_b']} (TVL: ${pool['tvl_usd']:,.0f})\n"
                    await query.edit_message_text(pool_text, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Error in pools callback: {e}")
    
    # Markets callbacks
    elif callback_data == "dex_markets":
        await query.edit_message_text(
            "📊 **Marktdaten werden geladen...**",
            parse_mode="Markdown"
        )
        await cmd_markets(update, context)
    
    # Help callback
    elif callback_data == "dex_help":
        await cmd_help(update, context)
    
    # Alerts callbacks
    elif callback_data == "dex_alerts":
        await cmd_alerts(update, context)
    
    # Strategies callbacks
    elif callback_data == "dex_strategies":
        await cmd_strategies(update, context)
    
    try:
        await award_points(user_id, "dex_interaction", update.effective_chat.id)
    except:
        pass


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fallback text handler"""
    user = update.effective_user
    
    try:
        await award_points(user.id, "message_sent", update.effective_chat.id)
    except:
        pass
    
    if "help" in update.message.text.lower():
        await cmd_help(update, context)
    else:
        await update.message.reply_text(
            "ℹ️ Nutze /dex für die DEX Mini-App oder /help für alle Befehle.",
            parse_mode="Markdown"
        )


def register_handlers(app):
    """Register all handlers"""
    app.add_handler(CommandHandler("start", cmd_start), group=0)
    app.add_handler(CommandHandler("dex", cmd_start), group=0)
    app.add_handler(CommandHandler("swap", cmd_swap), group=0)
    app.add_handler(CommandHandler("pools", cmd_pools), group=0)
    app.add_handler(CommandHandler("markets", cmd_markets), group=0)
    app.add_handler(CommandHandler("alerts", cmd_alerts), group=0)
    app.add_handler(CommandHandler("strategies", cmd_strategies), group=0)
    app.add_handler(CommandHandler("help", cmd_help), group=0)
    
    app.add_handler(CallbackQueryHandler(button_callback), group=1)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler), group=2)
    
    logger.info("DEX handlers registered successfully")


async def button_callback(update, context):
    """Handle button callbacks"""
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
