import logging
import re
from telegram import (InlineKeyboardButton, InlineKeyboardMarkup,
    Update, ForceReply, CallbackQuery,
    InputMediaPhoto, Message)
from telegram.ext import CallbackQueryHandler, ContextTypes, CommandHandler, filters, MessageHandler
from telegram.error import BadRequest
from database import (get_registered_groups, get_all_channels, list_scheduled_posts, add_scheduled_post,
    get_welcome, delete_welcome, set_welcome, set_rules, set_farewell,
    get_rules, delete_rules,
    get_farewell, delete_farewell,
    list_rss_feeds, remove_rss_feed, get_rss_topic,
    is_daily_stats_enabled, set_daily_stats, get_mood_question,
    set_group_language, get_topic_owners)
from handlers import clean_delete_accounts_for_chat
from channel_handlers import channel_edit_reply
from user_manual import HELP_TEXT
from access import get_visible_groups, get_visible_channels
from i18n import t

logger = logging.getLogger(__name__)

# --- Core Commands ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Zeigt das Gruppenauswahl-Menü bei /start"""
    await show_group_select(update, context)

async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Zeigt das Gruppenauswahl-Menü bei /menu"""
    await show_group_select(update, context)

async def channel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Zeigt das Kanalauswahl-Menü bei /channel"""
    await show_channel_select(update, context)

# --- Auswahl-Menüs ---
async def show_group_select(update: Update|CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    groups = get_registered_groups()
    text = t(None, 'SELECT_GROUP_PROMPT')  # Schlüssel ohne chat_id
    if not groups:
        text = t(None, 'NO_GROUPS')
        if isinstance(update, CallbackQuery):
            await update.edit_message_text(text)
        else:
            await update.message.reply_text(text)
        return
    kb = [[InlineKeyboardButton(name, callback_data=f"group_{gid}")] for gid, name in groups]
    kb.append([InlineKeyboardButton(t(None,'REFRESH'), callback_data="group_select")])
    markup = InlineKeyboardMarkup(kb)
    if isinstance(update, CallbackQuery):
        await update.edit_message_text(text, reply_markup=markup)
    else:
        await update.message.reply_text(text, reply_markup=markup)

async def show_channel_select(update: Update|CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    channels = get_all_channels()
    text = t(None, 'SELECT_CHANNEL_PROMPT')
    if not channels:
        text = t(None, 'NO_CHANNELS')
        if isinstance(update, CallbackQuery):
            await update.edit_message_text(text)
        else:
            await update.message.reply_text(text)
        return
    kb = [[InlineKeyboardButton(name, callback_data=f"channel_{cid}")] for cid, name in channels]
    kb.append([InlineKeyboardButton(t(None,'REFRESH'), callback_data="channel_select")])
    markup = InlineKeyboardMarkup(kb)
    if isinstance(update, CallbackQuery):
        await update.edit_message_text(text, reply_markup=markup)
    else:
        await update.message.reply_text(text, reply_markup=markup)

# --- Haupt-Callback-Router ---
async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()

    # Gruppen-Auswahl
    if data in ('group_select', 'group'):  # 'group' alias
        return await show_group_select(query, context)
    m = re.match(r'^group_(\d+)$', data)
    if m:
        return await show_group_menu(query, context, int(m.group(1)))

    # Kanäle-Auswahl
    if data in ('channel_select', 'channel'):
        return await show_channel_select(query, context)
    if data.startswith('channel_'):
        return await channel_mgmt_menu(update, context)

    # Gruppen-Submenus
    action_map = {
        'welcome': submenu_welcome,
        'rules': submenu_rules,
        'farewell': submenu_farewell,
        'rss': submenu_rss,
        'exceptions': submenu_links,
        'mood': submenu_mood,
        'language': submenu_language,
        'clean_delete': clean_delete,
    }
    for key, handler in action_map.items():
        m = re.match(rf'^(\d+)_{key}$', data)
        if m:
            return await handler(query, context)
    m = re.match(r'^(\d+)_toggle_stats$', data)
    if m:
        gid = int(m.group(1))
        set_daily_stats(gid, not is_daily_stats_enabled(gid))
        return await show_group_menu(query, context, gid)
    # RSS-Aktionen
    rss_map = {'rss_add': rss_add, 'rss_list': rss_list, 'rss_remove': rss_remove}
    for key, handler in rss_map.items():
        m = re.match(rf'^(\d+)_{key}$', data)
        if m:
            return await handler(query, context)
    # Sprachwechsel
    m = re.match(r'^(\d+)_setlang_([a-z]{2})$', data)
    if m:
        gid, lang = int(m.group(1)), m.group(2)
        set_group_language(gid, lang)
        return await show_group_menu(query, context, gid)
    # Hilfe
    if data == 'help':
        return await query.edit_message_text(HELP_TEXT, parse_mode='Markdown')

    logging.debug(f"Unhandled callback_data: {data}")


async def show_group_menu(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Zeigt das Hauptmenü für eine Gruppe."""
    await query.answer()
    stats_label = 'Statistiken aus' if t('daily_stats_enabled', chat_id) else 'Statistiken an'
    buttons = [
        InlineKeyboardButton('Begrüßung', callback_data=f"{chat_id}_welcome"),
        InlineKeyboardButton('Regeln', callback_data=f"{chat_id}_rules"),
        InlineKeyboardButton('Farewell', callback_data=f"{chat_id}_farewell"),
        InlineKeyboardButton('RSS', callback_data=f"{chat_id}_rss"),
        InlineKeyboardButton('Linkschutz', callback_data=f"{chat_id}_exceptions"),
        InlineKeyboardButton(stats_label, callback_data=f"{chat_id}_toggle_stats"),
        InlineKeyboardButton('Stimmung', callback_data=f"{chat_id}_mood"),
        InlineKeyboardButton('Sprache', callback_data=f"{chat_id}_language"),
        InlineKeyboardButton('Cleanup', callback_data=f"{chat_id}_clean_delete"),
        InlineKeyboardButton('Hilfe', callback_data='help'),
        InlineKeyboardButton('Gruppen-Auswahl', callback_data='group_select')
    ]
    # Zwei Spalten
    kb = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
    return await query.edit_message_text('Gruppen-Menü:', reply_markup=InlineKeyboardMarkup(kb))


# --- Submenus ---
async def submenu_welcome(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_')[0])
    kb = [
        [InlineKeyboardButton("✏️ " + t(chat_id, 'WELCOME_PROMPT'), callback_data=f"{chat_id}_welcome_edit")],
        [InlineKeyboardButton("👁️ " + t(chat_id, 'WELCOME_NONE'), callback_data=f"{chat_id}_welcome_show")],
        [InlineKeyboardButton("🗑️ " + t(chat_id, 'WELCOME_NONE'), callback_data=f"{chat_id}_welcome_delete")],
        [InlineKeyboardButton(t(chat_id, 'BACK'), callback_data=f"{chat_id}_menu_back")],
    ]
    if isinstance(query.message, Message) and query.message.photo:
        await query.edit_message_caption(t(chat_id, 'WELCOME_MENU'), reply_markup=InlineKeyboardMarkup(kb))
    else:
        await query.edit_message_text(t(chat_id, 'WELCOME_MENU'), reply_markup=InlineKeyboardMarkup(kb))

async def submenu_rules(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_')[0])
    kb = [
        [InlineKeyboardButton("✏️ " + t(chat_id, 'RULES_PROMPT'), callback_data=f"{chat_id}_rules_edit")],
        [InlineKeyboardButton("👁️ " + t(chat_id, 'RULES_NONE'), callback_data=f"{chat_id}_rules_show")],
        [InlineKeyboardButton("🗑️ " + t(chat_id, 'RULES_NONE'), callback_data=f"{chat_id}_rules_delete")],
        [InlineKeyboardButton(t(chat_id, 'BACK'), callback_data=f"{chat_id}_menu_back")],
    ]
    if isinstance(query.message, Message) and query.message.photo:
        await query.edit_message_caption(t(chat_id, 'RULES_MENU'), reply_markup=InlineKeyboardMarkup(kb))
    else:
        await query.edit_message_text(t(chat_id, 'RULES_MENU'), reply_markup=InlineKeyboardMarkup(kb))

async def submenu_farewell(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_')[0])
    kb = [
        [InlineKeyboardButton("✏️ " + t(chat_id, 'FAREWELL_PROMPT'), callback_data=f"{chat_id}_farewell_edit")],
        [InlineKeyboardButton("👁️ " + t(chat_id, 'FAREWELL_NONE'), callback_data=f"{chat_id}_farewell_show")],
        [InlineKeyboardButton("🗑️ " + t(chat_id, 'FAREWELL_NONE'), callback_data=f"{chat_id}_farewell_delete")],
        [InlineKeyboardButton(t(chat_id, 'BACK'), callback_data=f"{chat_id}_menu_back")],
    ]
    if isinstance(query.message, Message) and query.message.photo:
        await query.edit_message_caption(t(chat_id, 'FAREWELL_MENU'), reply_markup=InlineKeyboardMarkup(kb))
    else:
        await query.edit_message_text(t(chat_id, 'FAREWELL_MENU'), reply_markup=InlineKeyboardMarkup(kb))

async def submenu_rss(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_')[0])
    kb = [
        [InlineKeyboardButton("➕ " + t(chat_id, 'RSS_URL_PROMPT'), callback_data=f"{chat_id}_rss_add")],
        [InlineKeyboardButton("📋 " + t(chat_id, 'RSS_LIST'), callback_data=f"{chat_id}_rss_list")],
        [InlineKeyboardButton("🗑️ " + t(chat_id, 'RSS_NONE'), callback_data=f"{chat_id}_rss_remove")],
        [InlineKeyboardButton(t(chat_id, 'BACK'), callback_data=f"{chat_id}_menu_back")],
    ]
    await query.edit_message_text(t(chat_id, 'RSS_MENU'), reply_markup=InlineKeyboardMarkup(kb))

async def rss_list(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_')[0])
    feeds = list_rss_feeds(chat_id)
    text = "<b>Aktive RSS-Feeds:</b>\n" + "\n".join(f"• {u}" for u in feeds or ["(keine)"])
    kb = [[InlineKeyboardButton(t(chat_id, 'BACK'), callback_data=f"{chat_id}_rss")]]
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))

async def rss_add(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_')[0])
    topic = get_rss_topic(chat_id)
    if not topic:
        return await query.message.reply_text(
            "⚠️ Kein RSS-Topic gesetzt. Bitte `/settopicrss` ausführen.", parse_mode="Markdown"
        )
    context.user_data['awaiting_rss_url'] = True
    context.user_data['rss_group_id'] = chat_id
    await query.message.reply_text(t(chat_id, 'RSS_URL_PROMPT'), reply_markup=ForceReply())

async def rss_remove(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_')[0])
    remove_rss_feed(chat_id)
    return await submenu_rss(query, context)

async def submenu_links(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_')[0])
    admin_names = [a.user.first_name for a in await context.bot.get_chat_administrators(chat_id)]
    owner_ids = get_topic_owners(chat_id)
    # rest der Logik unverändert...

async def submenu_language(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_')[0])
    kb = [[InlineKeyboardButton(code.upper(), callback_data=f"{chat_id}_setlang_{code}")] for code in ('de','en','fr','ru')]
    kb.append([InlineKeyboardButton(t(chat_id, 'BACK'), callback_data=f"{chat_id}_menu_back")])
    return await query.edit_message_text(t(chat_id, 'LANG_SELECT_PROMPT'), reply_markup=InlineKeyboardMarkup(kb))

async def submenu_mood(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_')[0])
    current = get_mood_question(chat_id) or "(keine Frage gesetzt)"
    kb = [
        [InlineKeyboardButton("✏️ Bearbeiten", callback_data=f"{chat_id}_edit_mood")],
        [InlineKeyboardButton(t(chat_id, 'BACK'), callback_data=f"{chat_id}_menu_back")],
    ]
    return await query.edit_message_text(current, reply_markup=InlineKeyboardMarkup(kb))

async def clean_delete(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_')[0])
    count = await clean_delete_accounts_for_chat(chat_id, context.bot)
    return await query.edit_message_text(t(chat_id, 'CLEANUP_DONE').format(count=count))

# --- Kanalmenü ---
async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    text = "Bitte wähle einen Kanal:"  # Übersetzung von t(0, "CHANNEL_SWITCH")
    chans = await get_visible_channels(update.effective_user.id, context.bot, get_all_channels())
    kb = [[InlineKeyboardButton(ch[1], callback_data=f"channel_{ch[0]}")] for ch in chans]
    if query:
        return await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
    return await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))

async def channel_mgmt_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data  = query.data
    await query.answer()
    logger.info("channel_mgmt_menu received callback_data=%r", data)

    chan_id = int(data.rsplit("_",1)[-1])
    chat    = await context.bot.get_chat(chan_id)
    title   = chat.title or str(chan_id)
    header  = t(chan_id, "CHANNEL_MENU_HEADER").format(title=title)

    # Back aus Submenus?
    if re.match(rf"^channel_{chan_id}_menu_back$", data):
        kb = [
            [InlineKeyboardButton(t(chan_id, "CHANNEL_STATS_MENU"),    callback_data=f"ch_stats_{chan_id}")],
            [InlineKeyboardButton(t(chan_id, "CHANNEL_SETTINGS_MENU"), callback_data=f"ch_settings_{chan_id}")],
            [InlineKeyboardButton(t(chan_id, "CHANNEL_BROADCAST_MENU"),callback_data=f"ch_broadcast_{chan_id}")],
            [InlineKeyboardButton(t(chan_id, "CHANNEL_SCHEDULE_MENU"), callback_data=f"ch_schedule_{chan_id}")],
            [InlineKeyboardButton(t(chan_id, "CHANNEL_PINS_MENU"),     callback_data=f"ch_pins_{chan_id}")],
            [InlineKeyboardButton(t(chan_id, "CHANNEL_SWITCH"),        callback_data="channel_main_menu")],
        ]
    else:
        # Erstaufruf: mit Back-Button
        kb = [
            [InlineKeyboardButton(t(chan_id, "CHANNEL_STATS_MENU"),    callback_data=f"ch_stats_{chan_id}")],
            [InlineKeyboardButton(t(chan_id, "CHANNEL_SETTINGS_MENU"), callback_data=f"ch_settings_{chan_id}")],
            [InlineKeyboardButton(t(chan_id, "CHANNEL_BROADCAST_MENU"),callback_data=f"ch_broadcast_{chan_id}")],
            [InlineKeyboardButton(t(chan_id, "CHANNEL_SCHEDULE_MENU"), callback_data=f"ch_schedule_{chan_id}")],
            [InlineKeyboardButton(t(chan_id, "CHANNEL_PINS_MENU"),     callback_data=f"ch_pins_{chan_id}")],
            [InlineKeyboardButton(t(chan_id, "CHANNEL_SWITCH"),        callback_data="channel_main_menu")],
            [InlineKeyboardButton(t(chan_id, "BACK"),                  callback_data=f"channel_{chan_id}_menu_back")],
        ]

    try:
        await query.edit_message_text(
            header,
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode="Markdown"
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

# --- Registrierung der Handler ---

def register_menu(app):
    # /start, /menu, /channel
    app.add_handler(CommandHandler('start', start_command), group=1)
    app.add_handler(CommandHandler('menu', menu_command), group=1)
    app.add_handler(CommandHandler('channel', channel_command), group=1)

    # Callback Router
    app.add_handler(CallbackQueryHandler(menu_callback), group=1)

    # Kanal-spezifische Handler
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, channel_edit_reply), group=1)
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, handle_schedule_input), group=1)

    logging.info("Menu handlers registered")