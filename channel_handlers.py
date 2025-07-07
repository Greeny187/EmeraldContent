from telegram import Update, Chat
from telegram.ext import ContextTypes, CommandHandler, filters, MessageHandler, CallbackQueryHandler
from database import add_channel, remove_channel, list_channels
from i18n import t

async def channel_ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text('Pong from channel')

async def handle_broadcast_content(update, context):
    if "broadcast_chan" not in context.user_data:
        return  # nicht im Broadcast-Modus
    chan_id = context.user_data.pop("broadcast_chan")
    msg = update.effective_message
    try:
        if msg.photo:
            photo_id = msg.photo[-1].file_id
            caption = msg.caption or ""
            await context.bot.send_photo(chan_id, photo_id, caption=caption, parse_mode="HTML")
        else:
            await context.bot.send_message(chan_id, msg.text, parse_mode="HTML")
    
    except Exception as e:
        # z. B. fehlende Rechte o. ä.
        return await update.message.reply_text(f"❌ Broadcast fehlgeschlagen: {e}")

    await update.message.reply_text("✅ Broadcast gesendet.")

async def channel_edit_reply(update, context):
    msg = update.message
    text = msg.text or ""
    bot = context.bot

    # Titel ändern
    if "awaiting_title" in context.user_data:
        chan_id = context.user_data.pop("awaiting_title")
        # Rechte checken
        member = await bot.get_chat_member(chat_id=chan_id, user_id=bot.id)
        if not getattr(member, "can_change_info", False):
            return await msg.reply_text("❌ Ich habe nicht genügend Rechte, um den Titel zu ändern.")
        try:
            await bot.set_chat_title(chat_id=chan_id, title=text)
        except Exception as e:
            return await msg.reply_text(f"❌ Fehler beim Ändern des Titels: {e}")
        await msg.reply_text("✅ Titel geändert.")
        return

    # Beschreibung ändern
    if "awaiting_desc" in context.user_data:
        chan_id = context.user_data.pop("awaiting_desc")
        member = await bot.get_chat_member(chat_id=chan_id, user_id=bot.id)
        if not getattr(member, "can_change_info", False):
            return await msg.reply_text("❌ Ich habe nicht genügend Rechte, um die Beschreibung zu ändern.")
        try:
            await bot.set_chat_description(chat_id=chan_id, description=text)
        except Exception as e:
            return await msg.reply_text(f"❌ Fehler beim Ändern der Beschreibung: {e}")
        await msg.reply_text("✅ Beschreibung geändert.")
        return

def register_channel_handlers(app):

    app.add_handler(CommandHandler('ping', channel_ping, filters=filters.ChatType.CHANNEL))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, handle_broadcast_content), group=1)
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, channel_edit_reply), group=1)
