import logging
from telegram.error import BadRequest
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, CallbackQueryHandler, MessageHandler, filters, ForceReply
from access import get_visible_channels, get_visible_groups
from database import get_registered_groups, get_all_channels, list_scheduled_posts, add_scheduled_post
from channel_handlers import channel_edit_reply
from i18n import t

logger = logging.getLogger(__name__)

# --- Kanal-Hauptmenü ---
async def channel_mgmt_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    logger.info(f"🔄 Button empfangen: {query.data}")
    await query.answer()
    chan_id = int(query.data.rsplit("_", 1)[1])
    chat = await context.bot.get_chat(chan_id)
    title = chat.title or str(chan_id)
    kb = [
        [InlineKeyboardButton(t(chan_id, 'CHANNEL_STATS_MENU'), callback_data=f"ch_stats_{chan_id}")],
        [InlineKeyboardButton(t(chan_id, 'CHANNEL_SETTINGS_MENU'), callback_data=f"ch_settings_{chan_id}")],
        [InlineKeyboardButton(t(chan_id, 'CHANNEL_BROADCAST_MENU'), callback_data=f"ch_broadcast_{chan_id}")],
        [InlineKeyboardButton(t(chan_id, 'CHANNEL_SCHEDULE_MENU'),  callback_data=f"ch_schedule_{chan_id}")],
        [InlineKeyboardButton(t(chan_id, 'CHANNEL_PINS_MENU'), callback_data=f"ch_pins_{chan_id}")],
        [InlineKeyboardButton(t(chan_id, 'CHANNEL_SWITCH'), callback_data="main_menu")], 
        [InlineKeyboardButton(t(chan_id, 'BACK'), callback_data="main_menu")],
    ]
    try:
        await query.edit_message_text(
            t(chan_id, 'CHANNEL_MENU_HEADER').format(title=title),
            reply_markup=InlineKeyboardMarkup(kb)
        )
    except BadRequest as e:
        if "Message is not modified" in str(e):
            pass  # absichtlich ignorieren
        else:
            raise

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user

    # Alle Gruppen abrufen und filtern
    all_groups     = get_registered_groups()
    visible_groups = await get_visible_groups(user.id, context.bot, all_groups)

    # Alle Kanäle abrufen und filtern
    all_channels     = get_all_channels()
    visible_channels = await get_visible_channels(user.id, context.bot, all_channels)

    # Keyboard bauen
    kb = []
    for gid, title in visible_groups:
        kb.append([InlineKeyboardButton(f"👥 {title}", callback_data=f"group_{gid}")])
    for cid, title in visible_channels:
        kb.append([InlineKeyboardButton(f"📺 {title}", callback_data=f"channel_{cid}")])

    await query.edit_message_text(
        "🔧 Wähle eine Gruppe oder einen Kanal:",
        reply_markup=InlineKeyboardMarkup(kb)
    )

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
    await query.answer()
    chan_id = int(query.data.rsplit("_", 1)[1])
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

    app.add_handler(CallbackQueryHandler(channel_mgmt_menu, pattern=r"^channel_-?\d+$"))
    app.add_handler(CallbackQueryHandler(channel_stats_menu, pattern=r"^ch_stats_-?\d+$"))
    app.add_handler(CallbackQueryHandler(channel_settings_menu, pattern=r"^ch_settings_-?\d+$"))
    app.add_handler(CallbackQueryHandler(channel_broadcast_menu, pattern=r"^ch_broadcast_-?\d+$"))
    app.add_handler(CallbackQueryHandler(channel_pins_menu, pattern=r"^ch_pins_-?\d+$"))
    app.add_handler(CallbackQueryHandler(channel_schedule_menu, pattern=r"^ch_schedule_-?\d+$"))
    app.add_handler(CallbackQueryHandler(channel_schedule_add_menu, pattern=r"^ch_schedule_add_-?\d+$"), group=0)
    app.add_handler(CallbackQueryHandler(show_main_menu, pattern=r"^main_menu$"))
    app.add_handler(CallbackQueryHandler(channel_settitle_menu, pattern=r"^ch_settitle_-?\d+$"), group=0)
    app.add_handler(CallbackQueryHandler(channel_setdesc_menu,  pattern=r"^ch_setdesc_-?\d+$"), group=0)
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, channel_edit_reply), group=1)
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, handle_schedule_input), group=1)