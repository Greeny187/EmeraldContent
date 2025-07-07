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

# ‒‒‒ Hauptmenü für eine Gruppe ‒‒‒

async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _handle_group_select(update, context)

async def _handle_group_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query or update.message
    if hasattr(query, "answer"):
        await query.answer()

    all_groups = get_registered_groups()
    visible    = await get_visible_groups(update.effective_user.id, context.bot, all_groups)
    target     = query.message if isinstance(query, Message) else query

    if not visible:
        return await target.reply_text("Keine sichtbaren Gruppen gefunden.")

    kb = [[InlineKeyboardButton(title, callback_data=f"group_{cid}")] for cid, title in visible]
    return await target.reply_text(
        "Bitte wähle eine Gruppe:",
        reply_markup=InlineKeyboardMarkup(kb),
    )

async def show_group_menu(query: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    await query.answer()
    stats_label = (
        "Statistiken aus" if is_daily_stats_enabled(chat_id) else "Statistiken an"
    )
    kb = [
        [InlineKeyboardButton("Begrüßung", callback_data=f"{chat_id}_submenu_welcome")],
        [InlineKeyboardButton("Regeln",    callback_data=f"{chat_id}_submenu_rules")],
        [InlineKeyboardButton("Farewell",  callback_data=f"{chat_id}_submenu_farewell")],
        [InlineKeyboardButton("RSS",       callback_data=f"{chat_id}_submenu_rss")],
        [InlineKeyboardButton("Linkschutz", callback_data=f"{chat_id}_submenu_links")],
        [InlineKeyboardButton(stats_label,    callback_data=f"{chat_id}_toggle_stats")],
        [InlineKeyboardButton("Mood",       callback_data=f"{chat_id}_edit_mood")],
        [InlineKeyboardButton("Sprache",    callback_data=f"{chat_id}_submenu_language")],
        [InlineKeyboardButton("Cleanup",    callback_data=f"{chat_id}_clean_delete")],
        [InlineKeyboardButton("Hilfe",      callback_data="help")],
        [InlineKeyboardButton("Gruppen-Auswahl", callback_data="group_select")]
    ]
    await query.edit_message_text(
        "Gruppen-Menü:",
        reply_markup=InlineKeyboardMarkup(kb),
    )

# ‒‒‒ Submenus ‒‒‒
async def submenu_welcome(query: CallbackQuery, context):
    await query.answer()
    chat_id = int(query.data.split('_submenu_welcome')[0])
    kb = [
        [InlineKeyboardButton("✏️ " + t(chat_id, 'WELCOME_PROMPT'),
                              callback_data=f"{chat_id}_welcome_edit")],
        [InlineKeyboardButton("👁️ " + t(chat_id, 'WELCOME_NONE'),
                              callback_data=f"{chat_id}_welcome_show")],
        [InlineKeyboardButton("🗑️ " + t(chat_id, 'WELCOME_NONE'),
                              callback_data=f"{chat_id}_welcome_delete")],
        [InlineKeyboardButton(t(chat_id, 'BACK'),
                              callback_data=f"{chat_id}_menu_back")],
    ]
    # Wenn die aktuelle Message ein Foto/Media ist, Caption editieren,
    # sonst ganz normal Text editieren:
    if isinstance(query.message, Message) and query.message.photo:
        await query.edit_message_caption(
            t(chat_id, 'WELCOME_MENU'),
            reply_markup=InlineKeyboardMarkup(kb)
        )
    else:
        await query.edit_message_text(
            t(chat_id, 'WELCOME_MENU'),
            reply_markup=InlineKeyboardMarkup(kb)
        )


async def submenu_rules(query: CallbackQuery, context):
    await query.answer()
    chat_id = int(query.data.split('_submenu_rules')[0])
    kb = [
        [InlineKeyboardButton("✏️ " + t(chat_id, 'RULES_PROMPT'),
                              callback_data=f"{chat_id}_rules_edit")],
        [InlineKeyboardButton("👁️ " + t(chat_id, 'RULES_NONE'),
                              callback_data=f"{chat_id}_rules_show")],
        [InlineKeyboardButton("🗑️ " + t(chat_id, 'RULES_NONE'),
                              callback_data=f"{chat_id}_rules_delete")],
        [InlineKeyboardButton(t(chat_id, 'BACK'),
                              callback_data=f"{chat_id}_menu_back")],
    ]
    if isinstance(query.message, Message) and query.message.photo:
        await query.edit_message_caption(
            t(chat_id, 'RULES_MENU'),
            reply_markup=InlineKeyboardMarkup(kb)
        )
    else:
        await query.edit_message_text(
            t(chat_id, 'RULES_MENU'),
            reply_markup=InlineKeyboardMarkup(kb)
        )


async def submenu_farewell(query: CallbackQuery, context):
    await query.answer()
    chat_id = int(query.data.split('_submenu_farewell')[0])
    kb = [
        [InlineKeyboardButton("✏️ " + t(chat_id, 'FAREWELL_PROMPT'),
                              callback_data=f"{chat_id}_farewell_edit")],
        [InlineKeyboardButton("👁️ " + t(chat_id, 'FAREWELL_NONE'),
                              callback_data=f"{chat_id}_farewell_show")],
        [InlineKeyboardButton("🗑️ " + t(chat_id, 'FAREWELL_NONE'),
                              callback_data=f"{chat_id}_farewell_delete")],
        [InlineKeyboardButton(t(chat_id, 'BACK'),
                              callback_data=f"{chat_id}_menu_back")],
    ]
    if isinstance(query.message, Message) and query.message.photo:
        await query.edit_message_caption(
            t(chat_id, 'FAREWELL_MENU'),
            reply_markup=InlineKeyboardMarkup(kb)
        )
    else:
        await query.edit_message_text(
            t(chat_id, 'FAREWELL_MENU'),
            reply_markup=InlineKeyboardMarkup(kb)
        )

async def submenu_rss(query: CallbackQuery, context):
    await query.answer()
    chat_id = int(query.data.split('_submenu_rss')[0])
    kb = [
    [InlineKeyboardButton("➕ " + t(chat_id, 'RSS_URL_PROMPT'), callback_data=f"{chat_id}_rss_add")],
    [InlineKeyboardButton("📋 " + t(chat_id, 'RSS_LIST'), callback_data=f"{chat_id}_rss_list")],
    [InlineKeyboardButton("🗑️ " + t(chat_id, 'RSS_NONE'), callback_data=f"{chat_id}_rss_remove")],
    [InlineKeyboardButton(t(chat_id, 'BACK'), callback_data=f"{chat_id}_menu_back")],
]
    await query.edit_message_text(t(chat_id, 'RSS_MENU'), reply_markup=InlineKeyboardMarkup(kb))

async def submenu_language(query: CallbackQuery, context):
    await query.answer()
    chat_id = int(query.data.split('_submenu_language')[0])
    kb = [
        [InlineKeyboardButton(code.upper(),
                              callback_data=f"{chat_id}_setlang_{code}")]
        for code in ('de','en','fr','ru')
    ]
    kb.append([InlineKeyboardButton(t(chat_id, 'BACK'),
                                   callback_data=f"{chat_id}_menu_back")])
    await query.edit_message_text(
        t(chat_id, 'LANG_SELECT_PROMPT'),
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def submenu_mood(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_')[1])
    current = get_mood_question(chat_id) or "(keine Frage gesetzt)"
    kb = [
        [InlineKeyboardButton("✏️ Bearbeiten", callback_data=f"{chat_id}_edit_mood")],
                [InlineKeyboardButton(t(chat_id, 'BACK'), callback_data=f"{chat_id}_menu_back")],
    ]
    return await query.edit_message_text(current, reply_markup=InlineKeyboardMarkup(kb))

async def submenu_links(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_submenu_links')[0])

    kb = [
        [InlineKeyboardButton("🔍 Ausnahmen anzeigen",
                              callback_data=f"{chat_id}_links_exceptions")],
        [InlineKeyboardButton(t(chat_id, 'BACK'),
                              callback_data=f"{chat_id}_menu_back")],
    ]
    text = (
        "🔗 Linkposting ist standardmäßig deaktiviert.\n\n"
        "⚙️ Hier siehst du, wer trotzdem Links posten darf:"
    )
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))


# ─── Callback: Ausnahmen anzeigen ────────────────────────────────────
async def links_exceptions(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_links_exceptions')[0])

    # 1) Admins
    admins = await context.bot.get_chat_administrators(chat_id)
    admin_names = [
        f"@{a.user.username}" if a.user.username else a.user.first_name
        for a in admins
        if a.status in ("administrator", "creator")
    ]

    # 2) Owner (creator)
    owner = next((a for a in admins if a.status == "creator"), None)
    owner_name = (
        f"@{owner.user.username}" 
        if owner and owner.user.username 
        else (owner.user.first_name if owner else "–")
    )

    # 3) Themenbesitzer
    topic_ids = get_topic_owners(chat_id)
    topic_names = []
    for uid in topic_ids:
        try:
            m = await context.bot.get_chat_member(chat_id, uid)
            topic_names.append(
                f"@{m.user.username}" if m.user.username else m.user.first_name
            )
        except:
            continue

    # 4) Nachricht zusammensetzen
    lines = ["🔓 <b>Ausnahmen der Link-Sperre:</b>"]
    lines.append(f"• <b>Administratoren:</b> {', '.join(admin_names) or '(keine)'}")
    lines.append(f"• <b>Inhaber:</b> {owner_name}")
    lines.append(f"• <b>Themenbesitzer:</b> {', '.join(topic_names) or '(keine)'}")
    text = "\n".join(lines)

    # Back-Button
    back_markup = InlineKeyboardMarkup([[
        InlineKeyboardButton(t(chat_id, 'BACK'),
                             callback_data=f"{chat_id}_submenu_links")
    ]])

    # Je nach Media-Status caption oder Text ersetzen
    msg = query.message
    if isinstance(msg, Message) and (msg.photo or msg.caption):
        await query.edit_message_text(text, reply_markup=back_markup, parse_mode="HTML")
    else:
        await query.edit_message_text(text, reply_markup=back_markup, parse_mode="HTML")

# ‒‒‒ Detail‐Show mit Foto-Unterstützung ‒‒‒
async def welcome_show(query: CallbackQuery, context):
    await query.answer()
    chat_id = int(query.data.split('_')[0])
    data = get_welcome(chat_id)
    kb = [[InlineKeyboardButton(t(chat_id, 'BACK'),
                                callback_data=f"{chat_id}_submenu_welcome")]]
    if not data:
        return await query.edit_message_text(
            t(chat_id, 'WELCOME_NONE'),
            reply_markup=InlineKeyboardMarkup(kb)
        )
    photo_id, text = data
    media = InputMediaPhoto(media=photo_id, caption=text or "")
    return await query.edit_message_media(
        media=media,
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def welcome_edit(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_')[0])
    context.user_data['last_edit'] = (chat_id, 'welcome_edit')
    await query.message.reply_text(
        t(chat_id, 'WELCOME_PROMPT'),
        reply_markup=ForceReply(selective=True)
    )

async def welcome_delete(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_')[0])
    delete_welcome(chat_id)
    return await submenu_welcome(query, context)


# mach’s analog für rules_show & farewell_show:
async def rules_show(query: CallbackQuery, context):
    await query.answer()
    chat_id = int(query.data.split('_')[0])
    data = get_rules(chat_id)
    kb = [[InlineKeyboardButton(t(chat_id, 'BACK'),
                                callback_data=f"{chat_id}_submenu_rules")]]
    if not data:
        return await query.edit_message_text(
            t(chat_id, 'RULES_NONE'),
            reply_markup=InlineKeyboardMarkup(kb)
        )
    photo_id, text = data
    media = InputMediaPhoto(media=photo_id, caption=text or "")
    return await query.edit_message_media(
        media=media,
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def rules_delete(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_')[0])
    delete_rules(chat_id)
    return await submenu_rules(query, context)

async def rules_edit(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_')[0])
    context.user_data['last_edit'] = (chat_id, 'rules_edit')
    await query.message.reply_text(
        t(chat_id, 'RULES_PROMPT'),
        reply_markup=ForceReply(selective=True)
    )

async def farewell_show(query: CallbackQuery, context):
    await query.answer()
    chat_id = int(query.data.split('_')[0])
    data = get_farewell(chat_id)
    kb = [[InlineKeyboardButton(t(chat_id, 'BACK'),
                                callback_data=f"{chat_id}_submenu_farewell")]]
    if not data:
        return await query.edit_message_text(
            t(chat_id, 'FAREWELL_NONE'),
            reply_markup=InlineKeyboardMarkup(kb)
        )
    photo_id, text = data
    media = InputMediaPhoto(media=photo_id, caption=text or "")
    return await query.edit_message_media(
        media=media,
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def farewell_delete(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_')[0])
    delete_farewell(chat_id)
    return await submenu_farewell(query, context)

async def farewell_edit(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_')[0])
    context.user_data['last_edit'] = (chat_id, 'farewell_edit')
    await query.message.reply_text(
        t(chat_id, 'FAREWELL_PROMPT'),
        reply_markup=ForceReply(selective=True)
    )

# ‒‒‒ RSS-Actions ‒‒‒
async def rss_list(query: CallbackQuery, context):
    await query.answer()
    chat_id = int(query.data.split('_')[0])
    feeds = list_rss_feeds(chat_id)
    if not feeds:
        text = t(chat_id, 'RSS_LIST')
    else:
        text = "\n".join(f"• {url} (Topic ID: {tid})" for url, tid in feeds)
    kb = [[InlineKeyboardButton(t(chat_id, 'BACK'),
                                callback_data=f"{chat_id}_submenu_rss")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))


async def rss_add(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_rss_add')[0])
    # prüfen, ob ein RSS-Topic gesetzt ist
    topic_id = get_rss_topic(chat_id)
    if not topic_id:
        return await query.message.reply_text(
            "⚠️ Kein RSS-Posting-Thema gesetzt. Bitte `/settopicrss` im gewünschten Forum-Thema ausführen.",
            parse_mode="Markdown"
        )
    # Prompt für URL und Flag setzen
    context.user_data['awaiting_rss_url'] = True
    context.user_data['rss_group_id'] = chat_id
    await query.message.reply_text(t(chat_id, 'RSS_URL_PROMPT'),
        reply_markup=ForceReply()
    )

async def rss_remove(query: CallbackQuery, context):
    await query.answer()
    chat_id = int(query.data.split('_')[0])
    remove_rss_feed(chat_id)
    return await submenu_rss(query, context)



# --- Einstellungen: Stats, Mood, Cleanup ---
async def toggle_stats(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_')[0])
    current = is_daily_stats_enabled(chat_id)
    set_daily_stats(chat_id, not current)
    key = 'STATS_ENABLED' if not current else 'STATS_DISABLED'
    await query.answer(t(chat_id, key), show_alert=True)
    return await show_group_menu(query, context, chat_id)


async def edit_mood(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_')[0])
    context.user_data['awaiting'] = 'mood'
    context.user_data['mood_id']  = chat_id
    await query.message.reply_text(
        t(chat_id, 'MOOD_PROMPT'),
        reply_markup=ForceReply(selective=True)
    )


async def clean_delete(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_')[0])
    count = await clean_delete_accounts_for_chat(chat_id, context.bot)
    await query.edit_message_text(t(chat_id, 'CLEANUP_DONE').format(count=count))




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



# ‒‒‒ Dispatcher ‒‒‒
async def menu_callback(update: ContextTypes.DEFAULT_TYPE, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data

    # === Gruppen-Callbackdaten ===
    if data == 'group_select':
        return await _handle_group_select(update, context)

    # Haupt-Gruppenmenü öffnen
    m_group = re.match(r'^group_(\d+)$', data)
    if m_group:
        group_id = int(m_group.group(1))
        return await show_group_menu(query, context, group_id)

    # Untermenüs: welcome, rules, farewell, rss, exceptions
    m_section = re.match(r'^(?P<gid>\d+)_(?P<section>welcome|rules|farewell|rss|exceptions)$', data)
    if m_section:
        gid = int(m_section.group('gid'))
        section = m_section.group('section')
        back_main = InlineKeyboardMarkup([[InlineKeyboardButton('⬅ Hauptmenü', callback_data=f'group_{gid}')]])

        # Begrüßung, Regeln, Farewell: Edit/Show/Delete
        if section in ('welcome', 'rules', 'farewell'):
            kb = [
                [InlineKeyboardButton('Bearbeiten', callback_data=f'{gid}_{section}_edit')],
                [InlineKeyboardButton('Anzeigen', callback_data=f'{gid}_{section}_show')],
                [InlineKeyboardButton('Löschen', callback_data=f'{gid}_{section}_delete')],
                [InlineKeyboardButton('⬅ Hauptmenü', callback_data=f'group_{gid}')]
            ]
            text = f'⚙ {section.capitalize()} verwalten:'
            markup = InlineKeyboardMarkup(kb)
            if query.message.photo or query.message.caption:
                await query.edit_message_caption(text, reply_markup=markup)
            else:
                await query.edit_message_text(text, reply_markup=markup)
            return

        # RSS-Verwaltung
        if section == 'rss':
            kb = [
                [InlineKeyboardButton('Auflisten', callback_data=f'{gid}_rss_list')],
                [InlineKeyboardButton('Feed hinzufügen', callback_data=f'{gid}_rss_setrss')],
                [InlineKeyboardButton('Stoppen', callback_data=f'{gid}_rss_stop')],
                [InlineKeyboardButton('⬅ Hauptmenü', callback_data=f'group_{gid}')]
            ]
            text = '📰 RSS verwalten'
            markup = InlineKeyboardMarkup(kb)
            if query.message.photo or query.message.caption:
                await query.edit_message_caption(text, reply_markup=markup)
            else:
                await query.edit_message_text(text, reply_markup=markup)
            return

        # Link-Ausnahmen (exceptions)
        if section == 'exceptions':
            admins = await context.bot.get_chat_administrators(gid)
            admin_names = [f"@{a.user.username}" if a.user.username else a.user.first_name for a in admins]
            owner = next((a for a in admins if a.status == 'creator'), None)
            owner_name = f"@{owner.user.username}" if owner and owner.user.username else (owner.user.first_name if owner else '–')
            topic_ids = get_topic_owners(gid)
            topic_names = []
            for uid in topic_ids:
                try:
                    m = await context.bot.get_chat_member(gid, uid)
                    topic_names.append(f"@{m.user.username}" if m.user.username else m.user.first_name)
                except:
                    continue
            lines = [
                '🔓 Ausnahmen der Link-Sperre:',
                f"- Administratoren: {', '.join(admin_names)}",
                f"- Inhaber: {owner_name}",
                f"- Themenbesitzer: {', '.join(topic_names) if topic_names else '(keine)'}"
            ]
            text = "\n".join(lines)
            if query.message.photo or query.message.caption:
                await query.edit_message_caption(text, reply_markup=back_main)
            else:
                await query.edit_message_text(text, reply_markup=back_main)
            return

    # Detail-Handler: edit/show/delete, RSS, clean_delete
    parts = data.split('_')
    if len(parts) == 3:
        chat_id = int(parts[0])
        func = parts[1]
        action = parts[2]
        back_func = InlineKeyboardMarkup([[InlineKeyboardButton('⬅ Zurück', callback_data=f'{chat_id}_{func}')]])

        # Mappings für welcome/rules/farewell
        get_map = {'welcome': get_welcome, 'rules': get_rules, 'farewell': get_farewell}
        set_map = {'welcome': set_welcome, 'rules': set_rules, 'farewell': set_farewell}
        del_map = {'welcome': delete_welcome, 'rules': delete_rules, 'farewell': delete_farewell}

        # Anzeigen
        if action == 'show' and func in get_map:
            record = get_map[func](chat_id)
            if not record:
                msg = f"Keine {func}-Nachricht gesetzt."
                if query.message.photo or query.message.caption:
                    await query.edit_message_caption(msg, reply_markup=back_func)
                else:
                    await query.edit_message_text(msg, reply_markup=back_func)
            else:
                pid, txt = record
                if pid:
                    await query.edit_message_media(InputMediaPhoto(pid, caption=txt or ''), reply_markup=back_func)
                else:
                    if query.message.photo or query.message.caption:
                        await query.edit_message_caption(txt or '(kein Text)', reply_markup=back_func)
                    else:
                        await query.edit_message_text(txt or '(kein Text)', reply_markup=back_func)
            return

        # Löschen
        if action == 'delete' and func in del_map:
            del_map[func](chat_id)
            msg = f"✅ {func.capitalize()} gelöscht."
            if query.message.photo or query.message.caption:
                await query.edit_message_caption(msg, reply_markup=back_func)
            else:
                await query.edit_message_text(msg, reply_markup=back_func)
            return

        # Bearbeiten
        if action == 'edit' and func in set_map:
            context.user_data['last_edit'] = (chat_id, f"{func}_edit")
            prompt = f"✏️ Sende nun das neue {func}"
            if query.message.photo or query.message.caption:
                await query.edit_message_caption(prompt, reply_markup=back_func)
            else:
                await query.edit_message_text(prompt, reply_markup=back_func)
            return

        # RSS: URL erwarten
        if func == 'rss' and action == 'setrss':
            context.user_data['awaiting_rss_url'] = True
            context.user_data['rss_group_id'] = chat_id
            return await query.message.reply_text(
                '➡ Bitte sende jetzt die RSS-URL für diese Gruppe:',
                reply_markup=ForceReply(selective=True)
            )

        # RSS listen
        if func == 'rss' and action == 'list':
            feeds = list_rss_feeds(chat_id)
            text = 'Keine RSS-Feeds gesetzt.' if not feeds else 'Aktive RSS-Feeds:\n' + '\n'.join(f'- {url} (Topic {tid})' for url, tid in feeds)
            if query.message.photo or query.message.caption:
                await query.edit_message_caption(text, reply_markup=back_func)
            else:
                await query.edit_message_text(text, reply_markup=back_func)
            return

        # RSS stoppen
        if func == 'rss' and action == 'stop':
            remove_rss_feed(chat_id)
            msg = '✅ Alle RSS-Feeds entfernt.'
            if query.message.photo or query.message.caption:
                await query.edit_message_caption(msg, reply_markup=back_func)
            else:
                await query.edit_message_text(msg, reply_markup=back_func)
            return

        # Gelöschte Accounts entfernen
        if action == 'clean_delete':
            await query.answer(text='⏳ Entferne gelöschte Accounts…')
            removed = await clean_delete_accounts_for_chat(chat_id, context.bot)
            return await query.edit_message_text(
                text=f"✅ In Gruppe {chat_id} wurden {removed} gelöschte Accounts entfernt.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('↩️ Zurück', callback_data=f'group_{chat_id}')]])
            )

    # Tagesstatistik umschalten
    m_stats = re.match(r'^(\d+)_toggle_stats$', data)
    if m_stats:
        gid = int(m_stats.group(1))
        enabled = is_daily_stats_enabled(gid)
        set_daily_stats(gid, not enabled)
        await query.answer(f"Tagesstatistik {'aktiviert' if not enabled else 'deaktiviert'}", show_alert=True)
        return await show_group_menu(query, context, gid)

    # Hilfetext
    if data == 'help':
        return await query.message.reply_text(HELP_TEXT, parse_mode='Markdown')

    # === Kanal-Callbackdaten ===
    # Kanal-Hauptmenü
    m_chan = re.match(r'^channel_(\d+)$', data)
    if m_chan:
        return await show_main_menu(query, context, int(m_chan.group(1)))

    # Kanal-Statistiken
    m_ch_stats = re.match(r'^ch_stats_(-?\d+)$', data)
    if m_ch_stats:
        return await channel_stats_menu(query, context, int(m_ch_stats.group(1)))

    # Kanal-Statistiken
    m_ch_stats = re.match(r'^ch_stats_(-?\d+)$', data)
    if m_ch_stats:
        return await channel_stats_menu(update, context)

    # Kanal-Einstellungen (Titel/Beschreibung)
    m_ch_settitle = re.match(r'^ch_settitle_(-?\d+)$', data)
    if m_ch_settitle:
        return await channel_settitle_menu(update, context)
    m_ch_setdesc = re.match(r'^ch_setdesc_(-?\d+)$', data)
    if m_ch_setdesc:
        return await channel_setdesc_menu(update, context)

    # Kanal-Broadcast
    m_ch_bcast = re.match(r'^ch_broadcast_(-?\d+)$', data)
    if m_ch_bcast:
        return await channel_broadcast_menu(update, context)

    # Kanal-Zeitplan
    m_ch_schedule = re.match(r'^ch_schedule_(-?\d+)$', data)
    if m_ch_schedule:
        return await channel_schedule_menu(update, context)
    m_ch_schedule_add = re.match(r'^ch_schedule_add_(-?\d+)$', data)
    if m_ch_schedule_add:
        return await channel_schedule_add_menu(update, context)

    # Kanal-Pins
    m_ch_pins = re.match(r'^ch_pins_(-?\d+)$', data)
    if m_ch_pins:
        return await channel_pins_menu(update, context)

    # Fallback: Kanal-Submenu (show menu für weitere Aktionen)
    if data.startswith('ch_') or data.startswith('channel_'):
        return await channel_mgmt_menu(update, context)

    # Unbekannte Callback-Daten
    logger.debug(f"Unbekannte callback_data: {data}")


# --- Registrierung der Handler ---

def register_menu(app):

    # Gruppen-Menü
    app.add_handler(CommandHandler('menu', menu_command), group=2)
    app.add_handler(CallbackQueryHandler(menu_callback), group=2)

    # Kanal-Menü
    app.add_handler(CommandHandler('channel', show_main_menu, filters=filters.ChatType.PRIVATE), group=2)
    app.add_handler(CallbackQueryHandler(show_main_menu, pattern=r'^channel_\d+$'), group=2)
    # ch_ Submenus
    app.add_handler(CallbackQueryHandler(channel_stats_menu,      pattern=r'^ch_stats_-?\d+$'),      group=2)
    app.add_handler(CallbackQueryHandler(channel_settings_menu,   pattern=r'^ch_settings_-?\d+$'),   group=2)
    app.add_handler(CallbackQueryHandler(channel_broadcast_menu,  pattern=r'^ch_broadcast_-?\d+$'),  group=2)
    app.add_handler(CallbackQueryHandler(channel_schedule_menu,   pattern=r'^ch_schedule_-?\d+$'),   group=2)
    app.add_handler(CallbackQueryHandler(channel_schedule_add_menu, pattern=r'^ch_schedule_add_-?\d+$'), group=2)
    app.add_handler(CallbackQueryHandler(channel_pins_menu,       pattern=r'^ch_pins_-?\d+$'),       group=2)
    # Back-to-main
    app.add_handler(CallbackQueryHandler(show_main_menu, pattern=r'^channel_main_menu$'), group=2)
    # Fallback Kanal-Callback
    app.add_handler(CallbackQueryHandler(channel_mgmt_menu, pattern=r'^(?:ch_|channel_)'), group=2)
    # Freitext für Titel/Beschreibung & Zeitplan
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, channel_edit_reply), group=2)
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, handle_schedule_input), group=2)