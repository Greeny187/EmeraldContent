import logging
from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup,
    Update, ForceReply, CallbackQuery,
    InputMediaPhoto, Message
)
from telegram.ext import CallbackQueryHandler, ContextTypes, MessageHandler, filters

from database import (
    get_registered_groups, list_scheduled_posts,
    get_welcome, set_welcome, delete_welcome,
    get_rules, set_rules, delete_rules,
    get_farewell, set_farewell, delete_farewell,
    list_rss_feeds, remove_rss_feed,
    is_daily_stats_enabled, set_daily_stats, get_mood_question,
    set_group_language, get_group_setting
)
from handlers import clean_delete_accounts_for_chat
from user_manual import HELP_TEXT
from access import get_visible_groups
from i18n import t


# ‒‒‒ Hauptmenü für eine Gruppe ‒‒‒
async def show_group_menu(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    await query.answer()
    mood_q = get_mood_question(chat_id)
    stats_label = t(chat_id, 'STATS_ENABLED') if is_daily_stats_enabled(chat_id) else t(chat_id, 'STATS_DISABLED')

    kb = [
        [InlineKeyboardButton(t(chat_id, 'MENU_WELCOME'),
                              callback_data=f"{chat_id}_submenu_welcome")],
        [InlineKeyboardButton(t(chat_id, 'MENU_RULES'),
                              callback_data=f"{chat_id}_submenu_rules")],
        [InlineKeyboardButton(t(chat_id, 'MENU_FAREWELL'),
                              callback_data=f"{chat_id}_submenu_farewell")],
        [InlineKeyboardButton(t(chat_id, 'MENU_RSS'),
                              callback_data=f"{chat_id}_submenu_rss")],
        [InlineKeyboardButton(f"{stats_label}",  # Icon + label kommen aus i18n
                              callback_data=f"{chat_id}_toggle_stats")],
        [InlineKeyboardButton(t(chat_id, 'MENU_MOOD'),
                              callback_data=f"{chat_id}_edit_mood")],
        [InlineKeyboardButton(t(chat_id, 'MENU_LANGUAGE'),
                              callback_data=f"{chat_id}_submenu_language")],
        [InlineKeyboardButton(t(chat_id, 'MENU_CLEANUP'),
                              callback_data=f"{chat_id}_clean_delete")],
        [InlineKeyboardButton(t(chat_id, 'MENU_HELP'),
                              callback_data="help")],
        [InlineKeyboardButton(t(chat_id, 'MENU_GROUP_SELECT'),
                              callback_data="group_select")],
    ]
    await query.edit_message_text(
        text=t(chat_id, 'MENU_HEADER'),
        reply_markup=InlineKeyboardMarkup(kb)
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
        [InlineKeyboardButton("➕ " + t(chat_id, 'RSS_URL_PROMPT'),
                              callback_data=f"{chat_id}_rss_add")],
        [InlineKeyboardButton("🗑️ " + t(chat_id, 'RSS_NONE'),
                              callback_data=f"{chat_id}_rss_remove")],
        [InlineKeyboardButton(t(chat_id, 'BACK'),
                              callback_data=f"{chat_id}_menu_back")],
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


# ‒‒‒ RSS-Actions ‒‒‒
async def rss_list(query: CallbackQuery, context):
    await query.answer()
    chat_id = int(query.data.split('_')[0])
    feeds = list_rss_feeds(chat_id)
    if not feeds:
        text = t(chat_id, 'RSS_NONE')
    else:
        text = "\n".join(f"• {url} (Topic ID: {tid})" for url, tid in feeds)
    kb = [[InlineKeyboardButton(t(chat_id, 'BACK'),
                                callback_data=f"{chat_id}_submenu_rss")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))


async def rss_add(query: CallbackQuery, context):
    await query.answer()
    chat_id = int(query.data.split('_')[0])
    context.user_data['last_edit'] = (chat_id, 'rss_add')
    await query.message.reply_text(
        t(chat_id, 'RSS_URL_PROMPT'),
        reply_markup=ForceReply(selective=True)
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


# ‒‒‒ Channel-Menü ‒‒‒
async def channel_mgmt_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    chan_id = int(q.data.split('_',1)[1])
    chat = await context.bot.get_chat(chan_id)
    title = chat.title or str(chan_id)

    kb = [
        [InlineKeyboardButton(t(chan_id, 'CHANNEL_STATS_MENU'),
                              callback_data=f"ch_stats_{chan_id}")],
        [InlineKeyboardButton(t(chan_id, 'CHANNEL_SETTINGS_MENU'),
                              callback_data=f"ch_settings_{chan_id}")],
        [InlineKeyboardButton(t(chan_id, 'CHANNEL_BROADCAST_MENU'),
                              callback_data=f"ch_broadcast_{chan_id}")],
        [InlineKeyboardButton(t(chan_id, 'CHANNEL_PINS_MENU'),
                              callback_data=f"ch_pins_{chan_id}")],
        [InlineKeyboardButton(t(chan_id, 'CHANNEL_SWITCH'),
                              callback_data="group_select")],
        [InlineKeyboardButton(t(chan_id, 'BACK'),
                              callback_data="group_select")],
    ]
    await q.edit_message_text(
        t(chan_id, 'CHANNEL_MENU_HEADER').format(title=title),
        reply_markup=InlineKeyboardMarkup(kb)
    )


# ‒‒‒ Dispatcher ‒‒‒
async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data:
        return
    data = query.data
    await query.answer()

    # Channel-Auswahl
    if data.startswith("channel_"):
        return await channel_mgmt_menu(update, context)

    # Submenu-Routes
    if "_submenu_" in data:
        submenu = data.split("_submenu_",1)[1]
        return await globals()[f"submenu_{submenu}"](query, context)

    # Einzel-Actions:
    # welcome_edit, welcome_delete, etc.
    part = data.split("_",2)
    if len(part)==3 and part[1] in ("welcome","rules","farewell"):
        action = part[2]
        return await globals()[f"{part[1]}_{action}"](query, context)

    # rss_list, rss_add, rss_remove
    if part[1]=="rss":
        return await globals()[f"rss_{part[2]}"](query, context)

    # lang set
    if "_setlang_" in data:
        chat_id, lang = data.split("_setlang_")
        set_group_language(int(chat_id), lang)
        await query.answer(t(int(chat_id),'LANGUAGE_SET').format(lang=lang), show_alert=True)
        return await show_group_menu(query, context, int(chat_id))

    # 9) Hilfe
    if data == 'help':
        return await query.message.reply_text(HELP_TEXT, parse_mode='Markdown')

async def channel_broadcast_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    chan_id = int(q.data.rsplit("_", 1)[1])
    # Frage nach Broadcast-Inhalt
    context.user_data["broadcast_chan"] = chan_id
    return await q.edit_message_text(
        "📝 Bitte sende jetzt den Broadcast-Inhalt (Text oder Foto + Text)."
    )

async def channel_stats_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    chan_id = int(q.data.rsplit("_", 1)[1])
    # Abonnenten-Zahl holen
    chat = await context.bot.get_chat(chan_id)
    subs = await chat.get_members_count()
    text = f"📈 Kanal-Statistiken:\n• Abonnenten: {subs}"
    return await q.edit_message_text(text)

async def channel_pins_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    chan_id = int(q.data.rsplit("_", 1)[1])
    # Aktuell gepinnte Nachricht
    pinned = (await context.bot.get_chat(chan_id)).pinned_message
    lines = ["📌 Aktuell angeheftete Nachricht:"]
    if pinned:
        lines.append(pinned.text or "(Medien-Medium)")
        lines.append(f"(ID: {pinned.message_id})")
    else:
        lines.append("– Keine –")
    kb = [[InlineKeyboardButton("🔙 Zurück", callback_data=f"channel_{chan_id}")]]
    return await q.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(kb))

async def channel_schedule_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    chan_id = int(q.data.rsplit("_", 1)[1])
    schedules = list_scheduled_posts(chan_id)
    lines = ["🗓️ Geplante Beiträge:"]
    for text, cron in schedules:
        lines.append(f"• {cron} → «{text[:30]}…»")
    kb = [
        [InlineKeyboardButton("➕ Neu planen", callback_data=f"ch_schedule_add_{chan_id}")],
        [InlineKeyboardButton("🔙 Zurück",      callback_data=f"channel_{chan_id}")]
    ]
    return await q.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(kb))

async def channel_settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    chan_id = int(q.data.rsplit("_", 1)[1])
    kb = [
        [InlineKeyboardButton("✏️ Titel ändern",      callback_data=f"ch_settitle_{chan_id}")],
        [InlineKeyboardButton("📝 Beschreibung ändern", callback_data=f"ch_setdesc_{chan_id}")],
        [InlineKeyboardButton("🔙 Zurück",             callback_data=f"channel_{chan_id}")]
    ]
    return await q.edit_message_text("⚙️ Kanal-Einstellungen:", reply_markup=InlineKeyboardMarkup(kb))


# --- Registrierung der Handler ---

def register_menu(app):
    # 1) Kanal-Auswahl: „channel_<id>“ → channel_mgmt_menu(update, context, channel_id)
    app.add_handler(CallbackQueryHandler(lambda update, context: channel_mgmt_menu(update, context, int(update.callback_query.data.split('_', 1)[1])), pattern=r"^channel_\d+$"), group=0)

     # 2) Kanal-spezifische Submenus
    app.add_handler(CallbackQueryHandler(channel_broadcast_menu,  pattern=r"^ch_broadcast_\d+$"),  group=0)
    app.add_handler(CallbackQueryHandler(channel_stats_menu,      pattern=r"^ch_stats_\d+$"),      group=0)
    app.add_handler(CallbackQueryHandler(channel_pins_menu,       pattern=r"^ch_pins_\d+$"),       group=0)
    app.add_handler(CallbackQueryHandler(channel_schedule_menu,   pattern=r"^ch_schedule_\d+$"),   group=0)
    app.add_handler(CallbackQueryHandler(channel_settings_menu,   pattern=r"^ch_settings_\d+$"),   group=0)
 
     # 3) Gruppen-Menüs (alles außer ch_* und mood_*)
    app.add_handler(CallbackQueryHandler(menu_callback, pattern=r'^(?!(?:mood_|ch_)).*'), group=1)
    app.add_handler(CallbackQueryHandler(menu_callback, pattern=r"^cleanup$"), group=1)