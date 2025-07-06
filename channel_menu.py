import logging
import re
from telegram.error import BadRequest
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, ForceReply
from telegram.ext import ContextTypes, CallbackQueryHandler, MessageHandler, filters, CommandHandler
from access import get_visible_channels, get_visible_groups
from database import get_registered_groups, get_all_channels, list_scheduled_posts, add_scheduled_post
from channel_handlers import channel_edit_reply
from i18n import t

logger = logging.getLogger(__name__)

# --- Kanal-Hauptmenü ---
def register_channel_menu(app):
    # 1) /channel startet das Kanal-Menu
    app.add_handler(
        CommandHandler('channel', show_main_menu, filters=filters.ChatType.PRIVATE),
        group=2,
    )
    # 2) Haupt-Channel-Buttons
    app.add_handler(
        CallbackQueryHandler(show_main_menu, pattern=r"^channel_\d+$"),
        group=2,
    )
    # 3) Spezifische ch_*-Handler
    app.add_handler(CallbackQueryHandler(channel_stats_menu,      pattern=r"^ch_stats_-?\d+$"),      group=2)
    app.add_handler(CallbackQueryHandler(channel_settings_menu,   pattern=r"^ch_settings_-?\d+$"),   group=2)
    app.add_handler(CallbackQueryHandler(channel_broadcast_menu,  pattern=r"^ch_broadcast_-?\d+$"),  group=2)
    app.add_handler(CallbackQueryHandler(channel_schedule_menu,   pattern=r"^ch_schedule_-?\d+$"),   group=2)
    app.add_handler(CallbackQueryHandler(channel_schedule_add_menu, pattern=r"^ch_schedule_add_-?\d+$"), group=2)
    app.add_handler(CallbackQueryHandler(channel_pins_menu,       pattern=r"^ch_pins_-?\d+$"),       group=2)
    # 4) Fallback für alle ch_/channel_-Callbacks
    app.add_handler(
        CallbackQueryHandler(channel_mgmt_menu, pattern=r"^(?:ch_|channel_)"),
        group=2,
    )
    # 5) Antworten auf Freitext im Privat-Chat
    app.add_handler(
        MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
                       channel_edit_reply),
        group=2,
    )
    app.add_handler(
        MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
                       handle_schedule_input),
        group=2,
    )

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    text = t(0, 'CHANNEL_SWITCH')  # Du kannst hier einen Key ergänzen oder hardcoden
    channels = await get_visible_channels(update.effective_user.id, context.bot, get_all_channels())
    kb = [
        [InlineKeyboardButton(ch.title, callback_data=f"channel_{ch.id}")]
        for ch in channels
    ]
    if query:
        return await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
    return await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))

async def channel_mgmt_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data  = query.data
    await query.answer()
    logger.info("channel_mgmt_menu received callback_data=%r", data)

    chan_id = int(data.rsplit("_", 1)[-1])
    chat    = await context.bot.get_chat(chan_id)
    title   = chat.title or str(chan_id)
    text    = t(chan_id, 'CHANNEL_MENU_HEADER').format(title=title)

    # Haupt vs. Submenu (Back)
    if re.match(rf"^channel_{chan_id}_menu_back$", data):
        kb = [
            [InlineKeyboardButton(t(chan_id, 'CHANNEL_STATS_MENU'),     callback_data=f"ch_stats_{chan_id}")],
            [InlineKeyboardButton(t(chan_id, 'CHANNEL_SETTINGS_MENU'),  callback_data=f"ch_settings_{chan_id}")],
            [InlineKeyboardButton(t(chan_id, 'CHANNEL_BROADCAST_MENU'), callback_data=f"ch_broadcast_{chan_id}")],
            [InlineKeyboardButton(t(chan_id, 'CHANNEL_SCHEDULE_MENU'),  callback_data=f"ch_schedule_{chan_id}")],
            [InlineKeyboardButton(t(chan_id, 'CHANNEL_PINS_MENU'),      callback_data=f"ch_pins_{chan_id}")],
            [InlineKeyboardButton(t(chan_id, 'CHANNEL_SWITCH'),         callback_data="channel_main_menu")],
        ]
    else:
        kb = [
            [InlineKeyboardButton(t(chan_id, 'CHANNEL_STATS_MENU'),     callback_data=f"ch_stats_{chan_id}")],
            [InlineKeyboardButton(t(chan_id, 'CHANNEL_SETTINGS_MENU'),  callback_data=f"ch_settings_{chan_id}")],
            [InlineKeyboardButton(t(chan_id, 'CHANNEL_BROADCAST_MENU'), callback_data=f"ch_broadcast_{chan_id}")],
            [InlineKeyboardButton(t(chan_id, 'CHANNEL_SCHEDULE_MENU'),  callback_data=f"ch_schedule_{chan_id}")],
            [InlineKeyboardButton(t(chan_id, 'CHANNEL_PINS_MENU'),      callback_data=f"ch_pins_{chan_id}")],
            [InlineKeyboardButton(t(chan_id, 'CHANNEL_SWITCH'),         callback_data="channel_main_menu")],
            [InlineKeyboardButton(t(chan_id, 'BACK'),                   callback_data=f"channel_{chan_id}_menu_back")],
        ]

    try:
        return await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode="Markdown",
        )
    except BadRequest as e:
        if "Message is not modified" in str(e):
            logger.info("Channel-Menu unverändert, skip edit")
        else:
            raise

async def channel_settitle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chan_id = int(query.data.rsplit("_", 2)[2])
    # 1) Aufforderung per Privatchat
    await context.bot.send_message(
        chat_id=update.effective_user.id,
        text=t(chan_id, 'CHANNEL_SET_TITLE_PROMPT'),
        reply_markup=ForceReply(selective=True)
    )
    # 2) Optionale Bestätigung im Kanal-Menü
    await query.edit_message_text(
        t(chan_id, 'CHANNEL_SET_TITLE_HEADING'),
        reply_markup=query.message.reply_markup
    )
    # 3) Merken, dass wir auf eine Titel-Antwort warten
    context.user_data["awaiting_title"] = chan_id

async def channel_setdesc_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chan_id = int(query.data.rsplit("_", 2)[2])
    # 1) Aufforderung per Privatchat
    await context.bot.send_message(
        chat_id=update.effective_user.id,
        text=t(chan_id, 'CHANNEL_SET_DESC_PROMPT'),
        reply_markup=ForceReply(selective=True)
    )
    # 2) Optionale Bestätigung im Kanal-Menü
    await query.edit_message_text(
        t(chan_id, 'CHANNEL_SET_DESC_HEADING'),
        reply_markup=query.message.reply_markup
    )
    # 3) Merken, dass wir auf eine Beschreibung-Antwort warten
    context.user_data["awaiting_desc"] = chan_id

# --- Kanal-Submenus ---
async def channel_broadcast_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chan_id = int(query.data.rsplit("_", 1)[1])
    context.user_data["broadcast_chan"] = chan_id
    kb = [[InlineKeyboardButton(t(chan_id, 'BACK'), callback_data=f"channel_{chan_id}")]]
    return await query.edit_message_text(
        t(chan_id, 'CHANNEL_BROADCAST_PROMPT'),
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def channel_stats_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chan_id = int(query.data.rsplit("_", 1)[1])
    chat = await context.bot.get_chat(chan_id)
    subs = await context.bot.get_chat_member_count(chan_id)
    text = t(chan_id, 'CHANNEL_STATS_HEADER').format(count=subs)
    kb = [[InlineKeyboardButton(t(chan_id, 'BACK'), callback_data=f"channel_{chan_id}")]]
    return await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

async def channel_pins_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chan_id = int(query.data.rsplit("_", 1)[1])
    pinned = (await context.bot.get_chat(chan_id)).pinned_message
    lines = ["📌 " + t(chan_id, 'CHANNEL_PINS_HEADER')]
    if pinned:
        lines.append(pinned.text or "(Media)")
        lines.append(f"(ID: {pinned.message_id})")
    else:
        lines.append(t(chan_id, 'CHANNEL_PINS_NONE'))
    kb = [[InlineKeyboardButton(t(chan_id, 'BACK'),
                                callback_data=f"channel_{chan_id}")]]
    return await query.edit_message_text("\n".join(lines),
                                         reply_markup=InlineKeyboardMarkup(kb))

async def channel_schedule_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data                  # ← hier
    chat_id_str = data.split('_',1)[1]
    chan_id = int(chat_id_str)
    schedules = list_scheduled_posts(chan_id)
    lines = [t(chan_id, 'CHANNEL_SCHEDULE_HEADER')]
    for post_text, cron in schedules:
        lines.append(f"• `{cron}` → «{post_text[:30]}…»")
    kb = [
        [InlineKeyboardButton(t(chan_id, 'CHANNEL_SCHEDULE_ADD'),
                              callback_data=f"ch_schedule_add_{chan_id}")],
        [InlineKeyboardButton(t(chan_id, 'BACK'),
                              callback_data=f"channel_{chan_id}")]
    ]
    await query.edit_message_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode="Markdown"
    )

async def channel_schedule_add_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chan_id = int(query.data.rsplit("_", 1)[1])
    context.user_data["awaiting_schedule"] = chan_id
    await query.message.reply_text(t(chan_id, 'CHANNEL_SCHEDULE_ADD_PROMPT'))

async def handle_schedule_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chan_id = context.user_data.pop("awaiting_schedule")
    msg     = update.effective_message
    # Foto-File-ID holen (falls vorhanden)
    if msg.photo:
        file_id = msg.photo[-1].file_id
        raw     = msg.caption or ""
    else:
        file_id = None
        raw     = msg.text or ""
    lines = [l.strip() for l in raw.splitlines() if l.strip()]
    if not lines:
        return await msg.reply_text(t(chan_id, 'CHANNEL_SCHEDULE_ADD_PROMPT'))
    saved = 0
    for line in lines:
        parts = line.split()
        # jede Zeile muss min. 6 Tokens haben
        if len(parts) < 6:
            continue
        cron      = " ".join(parts[:5])
        post_text = " ".join(parts[5:])
        add_scheduled_post(chan_id, post_text, cron, file_id)
        saved += 1
    if saved == 0:
        return await msg.reply_text(t(chan_id, 'CHANNEL_SCHEDULE_ADD_PROMPT'))
    # Rückmeldung mit Anzahl
    await msg.reply_text(
        t(chan_id, 'CHANNEL_SCHEDULE_ADD_OK_MULTI').format(count=saved)
    )

async def channel_settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chan_id = int(query.data.rsplit("_", 1)[1])
    chat = await context.bot.get_chat(chan_id)
    title = chat.title or "–"
    desc  = chat.description or "–"
    kb = [
        [InlineKeyboardButton(t(chan_id, 'CHANNEL_SETTINGS_TITLE'),
                              callback_data=f"ch_settitle_{chan_id}")],
        [InlineKeyboardButton(t(chan_id, 'CHANNEL_SETTINGS_DESC'),
                              callback_data=f"ch_setdesc_{chan_id}")],
        [InlineKeyboardButton(t(chan_id, 'BACK'),
                              callback_data=f"channel_{chan_id}")]
    ]
    text = (
        f"{t(chan_id, 'CHANNEL_SETTINGS_HEADER')}\n\n"
        f"*Titel:* {title}\n"
        f"*Beschreibung:* {desc}"
    )
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

def register_channel_menu(app):

    # 1) /channel startet dein Channel-Management
    app.add_handler(
        CommandHandler('channel', channel_mgmt_menu,
                       filters=filters.ChatType.PRIVATE),
        group=2)
    app.add_handler(CallbackQueryHandler(channel_mgmt_menu, pattern=r'^(?:ch_|channel_).+'), group=2)
    app.add_handler(CallbackQueryHandler(channel_stats_menu, pattern=r"^ch_stats_-?\d+$"), group=2)
    app.add_handler(CallbackQueryHandler(channel_settings_menu, pattern=r"^ch_settings_-?\d+$"), group=2)
    app.add_handler(CallbackQueryHandler(channel_broadcast_menu, pattern=r"^ch_broadcast_-?\d+$"), group=2)
    app.add_handler(CallbackQueryHandler(channel_pins_menu, pattern=r"^ch_pins_-?\d+$"), group=2)
    app.add_handler(CallbackQueryHandler(channel_schedule_menu, pattern=r"^ch_schedule_-?\d+$"), group=2)
    app.add_handler(CallbackQueryHandler(channel_schedule_add_menu, pattern=r"^ch_schedule_add_-?\d+$"), group=2)
    app.add_handler(CallbackQueryHandler(show_main_menu, pattern=r"^main_menu$"), group=2)
    app.add_handler(CallbackQueryHandler(channel_settitle_menu, pattern=r"^ch_settitle_-?\d+$"), group=2)
    app.add_handler(CallbackQueryHandler(channel_setdesc_menu,  pattern=r"^ch_setdesc_-?\d+$"), group=2)
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, channel_edit_reply), group=1)
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, handle_schedule_input), group=1)