from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup,
    Update, ForceReply, CallbackQuery,
    InputMediaPhoto, Message)
from telegram.ext import CallbackQueryHandler, ContextTypes, CommandHandler, filters
from database import (
    get_registered_groups, list_scheduled_posts,
    get_welcome, set_welcome, delete_welcome,
    get_rules, set_rules, delete_rules,
    get_farewell, set_farewell, delete_farewell,
    list_rss_feeds, add_rss_feed, remove_rss_feed, get_rss_topic,
    is_daily_stats_enabled, set_daily_stats, get_mood_question,
    set_group_language, get_group_setting)
from channel_menu import channel_mgmt_menu
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
        [InlineKeyboardButton(t(chat_id, 'ANTISPAM'), 
                              callback_data=f"{chat_id}_submenu_links")],
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

async def _handle_group_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query or update.message
    # ack
    if hasattr(query, 'answer'):
        await query.answer()
    all_groups = get_registered_groups()
    visible    = await get_visible_groups(update.effective_user.id, context.bot, all_groups)
    if not visible:
        return await (query.message if query.message else query).reply_text(t(0, 'NO_VISIBLE_GROUPS'))
    kb = [
        [InlineKeyboardButton(title, callback_data=f"group_{cid}")]
        for cid, title in visible
    ]
    return await (query.message if query.message else query).reply_text(
        t(0, 'SELECT_GROUP'),
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Leite weiter an den Group-Select-Handler:
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
            [InlineKeyboardButton("📋 RSS_LIST", callback_data=f"{chat_id}_rss_list")],
            [InlineKeyboardButton("🗑️ " + t(chat_id, 'RSS_NONE'), callback_data=f"{chat_id}_rss_remove")],
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

async def submenu_links(query: CallbackQuery, context):
    await query.answer()
    chat_id = int(query.data.split('_submenu_links')[0])
    kb = [[InlineKeyboardButton(t(chat_id, 'BACK'), callback_data=f"{chat_id}_menu_back")]]
    text = (
        "🔗 Linkposting ist standardmäßig deaktiviert.\n\n"
        "Erlaubt sind:\n"
        "• Admins & Owner\n"
        "• Anonyme Admins\n"
        "• Nutzer mit einem zugewiesenen *Thema*\n"
        "\n👉 Verwende /settopic im gewünschten Thema."
    )
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

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

# ‒‒‒ Dispatcher ‒‒‒
async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data:
        return
    data = query.data
    await query.answer()

    if data == 'group_select':
        return await _handle_group_select(update, context)

    if data.startswith('group_'):
        return await show_group_menu(query, context, int(data.split('_',1)[1]))

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

    if action == "edit":
        context.user_data['last_edit'] = (int(part[0]), f"{part[1]}_edit")
        await query.message.reply_text(t(int(part[0]), f"{part[1].upper()}_PROMPT"),
                                       reply_markup=ForceReply(selective=True))
    elif action == "delete":
        delete_fn = globals()[f"delete_{part[1]}"]
        delete_fn(int(part[0]))
        await query.answer(f"{part[1].capitalize()} gelöscht.", show_alert=True)
        return await globals()[f"submenu_{part[1]}"](query, context)

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


# --- Registrierung der Handler ---

def register_menu(app):
    # 1) /menu-Command im Privat-Chat startet den Gruppen-Auswahl-Flow
    app.add_handler(
        CommandHandler(
            'menu',
            menu_command,
            filters=filters.ChatType.PRIVATE
        ),
        group=1
    )

    # 2) Alle CallbackQueries für Gruppen- und Submenus
    #    (group_select, group_<id>, *_submenu_*, *_toggle_*, welcome_*, etc.)
    app.add_handler(
        CallbackQueryHandler(menu_callback),
        group=1
    )