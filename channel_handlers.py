from telegram import Update, Chat
from telegram.ext import ContextTypes, CommandHandler, filters, MessageHandler, CallbackQueryHandler
from database import add_channel, remove_channel, get_all_channels
from i18n import t

async def add_channel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        return await update.message.reply_text(t(chat.id, 'ERROR_PRIV_CMD'))
    args = context.args
    if not args:
        return await update.message.reply_text(t(chat.id, 'USAGE_ADDCHANNEL'))
    channel_id = int(args[0])
    add_channel(chat.id, channel_id)
    await update.message.reply_text(t(chat.id, 'CHANNEL_ADDED'))

async def remove_channel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        return await update.message.reply_text(t(chat.id, 'ERROR_PRIV_CMD'))
    args = context.args
    if not args:
        return await update.message.reply_text(t(chat.id, 'USAGE_REMCHANNEL'))
    channel_id = int(args[0])
    remove_channel(chat.id, channel_id)
    await update.message.reply_text(t(chat.id, 'CHANNEL_REMOVED'))

async def list_channels_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        return await update.message.reply_text(t(chat.id, 'ERROR_PRIV_CMD'))
    channels = get_all_channels(chat.id)
    if not channels:
        return await update.message.reply_text(t(chat.id, 'NO_CHANNELS'))
    text = "\n".join(f"• `{cid}`" for cid in channels)
    await update.message.reply_text(t(chat.id, 'CHANNEL_LIST').format(list=text), parse_mode="Markdown")

async def handle_broadcast_content(update, context):
    if "broadcast_chan" not in context.user_data:
        return  # nicht im Broadcast-Modus

    chan_id = context.user_data.pop("broadcast_chan")
    msg = update.effective_message

    if msg.photo:
        photo_id = msg.photo[-1].file_id
        caption = msg.caption or ""
        await context.bot.send_photo(chan_id, photo_id, caption=caption, parse_mode="HTML")
    else:
        await context.bot.send_message(chan_id, msg.text, parse_mode="HTML")

    await update.message.reply_text("✅ Broadcast gesendet.")

async def channel_edit_reply(update, context):
    msg = update.message
    if "awaiting_title" in context.user_data:
        chan_id = context.user_data.pop("awaiting_title")
        await context.bot.set_chat_title(chan_id, msg.text)
        await msg.reply_text("✅ Titel geändert.")
    elif "awaiting_desc" in context.user_data:
        chan_id = context.user_data.pop("awaiting_desc")
        await context.bot.set_chat_description(chan_id, msg.text)
        await msg.reply_text("✅ Beschreibung geändert.")

def register_channel_handlers(app):

    app.add_handler(CommandHandler("addchannel",   add_channel_cmd,   filters=filters.ChatType.GROUPS), group=0)
    app.add_handler(CommandHandler("removechannel",remove_channel_cmd,filters=filters.ChatType.GROUPS), group=0)
    app.add_handler(CommandHandler("listchannels", list_channels_cmd,filters=filters.ChatType.GROUPS), group=0)
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, handle_broadcast_content), group=1)
