"""Support Bot - Ticket System & Command Handlers"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters, CallbackQueryHandler
import uuid

logger = logging.getLogger(__name__)

try:
    from shared.emrd_rewards_integration import award_points
    from . import database
except ImportError:
    database = None
    async def award_points(*args, **kwargs):
        pass


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Willkommensbefehl"""
    user = update.effective_user
    chat = update.effective_chat
    
    if chat.type == "private":
        await update.message.reply_text(
            "🎫 **Emerald Support**\n\n"
            "Brauchst du Hilfe? Erstelle ein Support-Ticket!\n\n"
            "📋 Services:\n"
            "• Technical Support\n"
            "• Account Issues\n"
            "• Billing & Payments\n"
            "• General Inquiries\n",
            parse_mode="Markdown"
        )
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "🎫 Support öffnen",
                web_app=WebAppInfo(url="https://greeny187.github.io/EmeraldContentBots/miniapp/appsupport.html")
            )],
            [InlineKeyboardButton("❓ FAQ", callback_data="support_faq")]
        ])
        await update.message.reply_text("Wähle eine Option:", reply_markup=keyboard)
    else:
        await update.message.reply_text("🎫 Support Bot aktiv!")


async def cmd_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Create new support ticket"""
    user = update.effective_user
    
    await update.message.reply_text(
        "🎫 **Neues Support-Ticket**\n\n"
        "Beschreibe dein Problem kurz:",
        parse_mode="Markdown"
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check ticket status"""
    user = update.effective_user
    
    if database:
        try:
            tickets = database.get_user_tickets(user.id)
            if tickets:
                text = "🎫 **Deine Tickets:**\n\n"
                for ticket in tickets:
                    status = "✅ Gelöst" if ticket['status'] == 'closed' else "⏳ Offen"
                    text += f"#{ticket['ticket_id']}: {status}\n"
                await update.message.reply_text(text, parse_mode="Markdown")
            else:
                await update.message.reply_text("Du hast noch keine Tickets.")
        except Exception as e:
            logger.error(f"Error fetching tickets: {e}")
            await update.message.reply_text("Fehler beim Abrufen der Tickets.")
    else:
        await update.message.reply_text("Ticketsystem nicht verfügbar.")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help"""
    help_text = """
🎫 **Support Bot - Hilfe**

*Befehle:*
/start - Willkommen
/ticket - Neues Ticket
/status - Ticket-Status
/faq - Häufig gestellte Fragen

*Antwortzeitr:*
⚡ Critical: < 1 Stunde
🟠 High: < 4 Stunden
🟡 Normal: < 24 Stunden

*Kontakt:*
📧 support@emerald.com
💬 Telegram Support Group
"""
    await update.message.reply_text(help_text, parse_mode="Markdown")


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Button callbacks"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "support_faq":
        faq_text = """
❓ **Häufig Gestellte Fragen**

**Wie lange dauert Support?**
Normalerweise 24 Stunden, kritische Fälle schneller.

**Kann ich mein Ticket eskalieren?**
Ja, durch die Mini-App oder /ticket command.

**Wo finde ich meine Tickets?**
Nutze /status oder öffne die Mini-App.
"""
        await query.edit_message_text(faq_text, parse_mode="Markdown")


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle support messages"""
    user = update.effective_user
    message = update.message
    
    try:
        await award_points(user.id, "message_sent", update.effective_chat.id)
    except Exception:
        pass
    
    # Check if this is a reply to a ticket
    if message.reply_to_message:
        if database:
            try:
                # Get or create ticket
                ticket_id = str(uuid.uuid4())[:8]
                database.create_ticket(user.id, "General", message.text, ticket_id)
                
                await message.reply_text(
                    f"✅ Ticket #{ticket_id} erstellt!\n\n"
                    f"Wir antworten bald.\n"
                    f"Nutze /status um den Status zu prüfen.",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Error creating ticket: {e}")
                await message.reply_text("Fehler beim Erstellen des Tickets.")
    else:
        if "help" in message.text.lower():
            await cmd_help(update, context)
        else:
            await message.reply_text(
                "Nutze /ticket um ein neues Support-Ticket zu erstellen.\n"
                "Tippe /help für Informationen."
            )


def register_handlers(app):
    """Register handlers"""
    app.add_handler(CommandHandler("start", cmd_start), group=0)
    app.add_handler(CommandHandler("support", cmd_start), group=0)
    app.add_handler(CommandHandler("ticket", cmd_ticket), group=0)
    app.add_handler(CommandHandler("status", cmd_status), group=0)
    app.add_handler(CommandHandler("help", cmd_help), group=0)
    
    app.add_handler(CallbackQueryHandler(button_callback), group=1)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler), group=2)
