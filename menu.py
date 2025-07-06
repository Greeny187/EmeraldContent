import logging
import re
from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup,
    Update, ForceReply, CallbackQuery,
    InputMediaPhoto, Message
)
from telegram.ext import CallbackQueryHandler, ContextTypes, CommandHandler, filters
from database import (
    get_registered_groups,
    get_welcome, delete_welcome,
    get_rules, delete_rules,
    get_farewell, delete_farewell,
    list_rss_feeds, remove_rss_feed, get_rss_topic,
    is_daily_stats_enabled, set_daily_stats, get_mood_question,
    set_group_language, get_topic_owners
)
from handlers import clean_delete_accounts_for_chat
from user_manual import HELP_TEXT
from access import get_visible_groups
from i18n import t

logger = logging.getLogger(__name__)

# ‒‒‒ Hauptmenü für eine Gruppe ‒‒‒
async def show_group_menu(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    await query.answer()
    mood_q = get_mood_question(chat_id)
    stats_label = t(chat_id, 'STATS_ENABLED') if is_daily_stats_enabled(chat_id) else t(chat_id, 'STATS_DISABLED')

    kb = [
        [InlineKeyboardButton(t(chat_id, 'MENU_WELCOME'), callback_data=f"{chat_id}_submenu_welcome")],
        [InlineKeyboardButton(t(chat_id, 'MENU_RULES'), callback_data=f"{chat_id}_submenu_rules")],
        [InlineKeyboardButton(t(chat_id, 'MENU_FAREWELL'), callback_data=f"{chat_id}_submenu_farewell")],
        [InlineKeyboardButton(t(chat_id, 'MENU_RSS'), callback_data=f"{chat_id}_submenu_rss")],
        [InlineKeyboardButton(t(chat_id, 'ANTISPAM'), callback_data=f"{chat_id}_submenu_links")],
        [InlineKeyboardButton(stats_label, callback_data=f"{chat_id}_toggle_stats")],
        [InlineKeyboardButton(t(chat_id, 'MENU_MOOD'), callback_data=f"{chat_id}_edit_mood")],
        [InlineKeyboardButton(t(chat_id, 'MENU_LANGUAGE'), callback_data=f"{chat_id}_submenu_language")],
        [InlineKeyboardButton(t(chat_id, 'MENU_CLEANUP'), callback_data=f"{chat_id}_clean_delete")],
        [InlineKeyboardButton(t(chat_id, 'MENU_HELP'), callback_data="help")],
        [InlineKeyboardButton(t(chat_id, 'MENU_GROUP_SELECT'), callback_data="group_select")],
    ]
    await query.edit_message_text(
        text=t(chat_id, 'MENU_HEADER'),
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def _handle_group_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query or update.message
    if hasattr(query, 'answer'):
        await query.answer()

    all_groups = get_registered_groups()
    visible = await get_visible_groups(update.effective_user.id, context.bot, all_groups)

    target = query.message if isinstance(query, CallbackQuery) else query

    if not visible:
        return await target.reply_text(t(0, 'NO_VISIBLE_GROUPS'))

    kb = [
        [InlineKeyboardButton(title, callback_data=f"group_{cid}")]
        for cid, title in visible
    ]
    return await target.reply_text(
        t(0, 'SELECT_GROUP'),
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _handle_group_select(update, context)

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


# ‒‒‒ Dispatcher ‒‒‒
async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()

    # 0) Gruppen-Auswahl neu starten
    if data == 'group_select':
        return await _handle_group_select(update, context)

    # 1) Haupt-Gruppenmenü öffnen (via button “group_<id>”)
    m_group = re.match(r'^group_(\d+)$', data)
    if m_group:
        return await show_group_menu(query, context, int(m_group.group(1)))

    # 2) Back-Button aus jedem Sub-Menu (“<id>_menu_back”)
    m_back = re.match(r'^(\d+)_menu_back$', data)
    if m_back:
        return await show_group_menu(query, context, int(m_back.group(1)))

    # 3) Sub-Menus öffnen (“<id>_submenu_<name>”)
    m_sub = re.match(r'^(\d+)_submenu_(\w+)$', data)
    if m_sub:
        chat_id, submenu = int(m_sub.group(1)), m_sub.group(2)
        fn = globals().get(f"submenu_{submenu}")
        if fn:
            return await fn(query, context)
        logger.error("Unbekanntes Sub-Menu: %r", submenu)
        return

    # 4) Welcome/Rules/Farewell Show/Edit/Delete
    m_wrf = re.match(r'^(\d+)_(welcome|rules|farewell)_(show|edit|delete)$', data)
    if m_wrf:
        chat_id, obj, action = int(m_wrf.group(1)), m_wrf.group(2), m_wrf.group(3)
        return await globals()[f"{obj}_{action}"](query, context)

    # 5) RSS-Actions (“<id>_rss_add|list|remove”)
    m_rss = re.match(r'^(\d+)_rss_(add|list|remove)$', data)
    if m_rss:
        return await globals()[f"rss_{m_rss.group(2)}"](query, context)

    # 6) Toggle Stats, Edit Mood, Links Exceptions, Cleanup
    if re.match(r'^\d+_toggle_stats$', data):
        return await toggle_stats(query, context)
    if re.match(r'^\d+_edit_mood$', data):
        return await edit_mood(query, context)
    if re.match(r'^\d+_links_exceptions$', data):
        return await links_exceptions(query, context)
    if re.match(r'^\d+_clean_delete$', data):
        return await clean_delete(query, context)

    # 7) Sprache setzen (“<id>_setlang_<code>”)
    m_lang = re.match(r'^(\d+)_setlang_(\w+)$', data)
    if m_lang:
        cid, lang = int(m_lang.group(1)), m_lang.group(2)
        set_group_language(cid, lang)
        await query.answer(
            t(cid, 'LANGUAGE_SET').format(lang=lang),
            show_alert=True
        )
        return await show_group_menu(query, context, cid)

    # 8) Hilfe
    if data == 'help':
        return await query.message.reply_text(HELP_TEXT, parse_mode='Markdown')

    # Fallback
    logger.debug("Unbekannte callback_data im group menu: %r", data)

# --- Registrierung der Handler ---

def register_menu(app):
    # 1) /menu im privaten Chat startet den Gruppen-Auswahl-Flow
    app.add_handler(CommandHandler('menu', menu_command, filters=filters.ChatType.PRIVATE), group=1    )
    # 2) CallbackQueries nur für Gruppendaten, keine Channel-Callbacks
    app.add_handler(CallbackQueryHandler(menu_callback, pattern=r'^(?!ch_|channel_).+'), group=1)