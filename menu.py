import logging
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
    ForceReply,
    CallbackQuery,
)
from telegram.ext import CallbackQueryHandler, ContextTypes

from database import (
    get_registered_groups,
    get_welcome,    set_welcome,    delete_welcome,
    get_rules,      set_rules,      delete_rules,
    get_farewell,   set_farewell,   delete_farewell,
    list_rss_feeds, remove_rss_feed,
    is_daily_stats_enabled, set_daily_stats, get_mood_question,
    set_group_language, get_group_setting,
)
from handlers import (
    channel_broadcast_menu,
    channel_stats_menu,
    channel_pins_menu,
    channel_schedule_menu,
    channel_settings_menu,
)
from utils import clean_delete_accounts_for_chat
from user_manual import HELP_TEXT
from access import get_visible_groups
from i18n import t

logger = logging.getLogger(__name__)


# --- Hauptmenü für eine Gruppe ---
async def show_group_menu(
    query: CallbackQuery,
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int
):
    await query.answer()
    mood        = get_mood_question(chat_id)
    stats_label = "Aktiv" if is_daily_stats_enabled(chat_id) else "Inaktiv"

    keyboard = [
        [InlineKeyboardButton("Begrüßung", callback_data=f"{chat_id}_submenu_welcome")],
        [InlineKeyboardButton("Regeln",     callback_data=f"{chat_id}_submenu_rules")],
        [InlineKeyboardButton("Abschied",   callback_data=f"{chat_id}_submenu_farewell")],
        [InlineKeyboardButton("RSS",        callback_data=f"{chat_id}_submenu_rss")],
        [InlineKeyboardButton(f"📊 Statistiken: {stats_label}", callback_data=f"{chat_id}_toggle_stats")],
        [InlineKeyboardButton("✍️ Frage ändern",    callback_data=f"{chat_id}_edit_mood")],
        [InlineKeyboardButton("🌐 Sprache",          callback_data=f"{chat_id}_submenu_language")],
        [InlineKeyboardButton("🗑️ Cleanup",         callback_data=f"{chat_id}_clean_delete")],
        [InlineKeyboardButton("📖 Hilfe",            callback_data="help")],
        [InlineKeyboardButton("🔄 Gruppe wählen",    callback_data="group_select")],
    ]
    text = "🔧 Gruppe verwalten – wähle eine Funktion:"
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard))


# --- Submenus: Welcome / Rules / Farewell / RSS / Language ---
async def submenu_welcome(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_submenu_welcome')[0])
    kb = [
        [InlineKeyboardButton("Bearbeiten", callback_data=f"{chat_id}_welcome_edit")],
        [InlineKeyboardButton("Anzeigen",   callback_data=f"{chat_id}_welcome_show")],
        [InlineKeyboardButton("Löschen",    callback_data=f"{chat_id}_welcome_delete")],
        [InlineKeyboardButton("⬅ Hauptmenü", callback_data=f"{chat_id}_menu_back")],
    ]
    await query.edit_message_text(t(chat_id, 'WELCOME_MENU'), reply_markup=InlineKeyboardMarkup(kb))


async def submenu_rules(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_submenu_rules')[0])
    kb = [
        [InlineKeyboardButton("Bearbeiten", callback_data=f"{chat_id}_rules_edit")],
        [InlineKeyboardButton("Anzeigen",   callback_data=f"{chat_id}_rules_show")],
        [InlineKeyboardButton("Löschen",    callback_data=f"{chat_id}_rules_delete")],
        [InlineKeyboardButton("⬅ Hauptmenü", callback_data=f"{chat_id}_menu_back")],
    ]
    await query.edit_message_text(t(chat_id, 'RULES_MENU'), reply_markup=InlineKeyboardMarkup(kb))


async def submenu_farewell(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_submenu_farewell')[0])
    kb = [
        [InlineKeyboardButton("Bearbeiten", callback_data=f"{chat_id}_farewell_edit")],
        [InlineKeyboardButton("Anzeigen",   callback_data=f"{chat_id}_farewell_show")],
        [InlineKeyboardButton("Löschen",    callback_data=f"{chat_id}_farewell_delete")],
        [InlineKeyboardButton("⬅ Hauptmenü", callback_data=f"{chat_id}_menu_back")],
    ]
    await query.edit_message_text(t(chat_id, 'FAREWELL_MENU'), reply_markup=InlineKeyboardMarkup(kb))


async def submenu_rss(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_submenu_rss')[0])
    kb = [
        [InlineKeyboardButton("Anzeigen",    callback_data=f"{chat_id}_rss_list")],
        [InlineKeyboardButton("Hinzufügen",  callback_data=f"{chat_id}_rss_add")],
        [InlineKeyboardButton("Entfernen",   callback_data=f"{chat_id}_rss_remove")],
        [InlineKeyboardButton("⬅ Hauptmenü", callback_data=f"{chat_id}_menu_back")],
    ]
    await query.edit_message_text(t(chat_id, 'RSS_MENU'), reply_markup=InlineKeyboardMarkup(kb))


async def submenu_language(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_submenu_language')[0])
    kb = [
        [InlineKeyboardButton(lang.upper(), callback_data=f"{chat_id}_setlang_{lang}")]
        for lang in ('de','en','fr','ru')
    ]
    kb.append([InlineKeyboardButton("⬅ Hauptmenü", callback_data=f"{chat_id}_menu_back")])
    await query.edit_message_text(t(chat_id, 'LANG_SELECT_PROMPT'), reply_markup=InlineKeyboardMarkup(kb))


# --- Detail‐Actions for Welcome / Rules / Farewell ---
async def welcome_show(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_')[0])
    text = get_welcome(chat_id) or t(chat_id, 'WELCOME_NONE')
    kb = [[InlineKeyboardButton("⬅", callback_data=f"{chat_id}_submenu_welcome")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))


async def welcome_edit(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_')[0])
    context.user_data['awaiting'] = 'welcome'
    context.user_data['id']      = chat_id
    await query.message.reply_text(
        t(chat_id, 'WELCOME_PROMPT'),
        reply_markup=ForceReply(selective=True)
    )


async def welcome_delete(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    chat_id = int(query.data.split('_')[0])
    await query.answer(t(chat_id, 'WELCOME_DELETED'), show_alert=True)
    delete_welcome(chat_id)
    return await submenu_welcome(query, context)


# (Analog: rules_show, rules_edit, rules_delete; farewell_show, farewell_edit, farewell_delete)


# --- RSS Actions ---
async def rss_list(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_')[0])
    feeds = list_rss_feeds(chat_id)
    text = t(chat_id, 'RSS_NONE') if not feeds else "\n".join(feeds)
    kb = [[InlineKeyboardButton("⬅", callback_data=f"{chat_id}_submenu_rss")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))


async def rss_add(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_')[0])
    context.user_data['awaiting'] = 'rss'
    context.user_data['rss_id']   = chat_id
    await query.message.reply_text(
        t(chat_id, 'RSS_PROMPT'),
        reply_markup=ForceReply(selective=True)
    )


async def rss_remove(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_')[0])
    remove_rss_feed(chat_id)
    kb = [[InlineKeyboardButton("⬅", callback_data=f"{chat_id}_submenu_rss")]]
    await query.edit_message_text(t(chat_id, 'RSS_REMOVED'), reply_markup=InlineKeyboardMarkup(kb))


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


# --- Channel‐Dispatcher (group=0) ---
async def _handle_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data
    if data.startswith('ch_broadcast_'):
        return await channel_broadcast_menu(update, context)
    if data.startswith('ch_stats_'):
        return await channel_stats_menu(update, context)
    if data.startswith('ch_pins_'):
        return await channel_pins_menu(update, context)
    if data.startswith('ch_schedule_'):
        return await channel_schedule_menu(update, context)
    if data.startswith('ch_settings_'):
        return await channel_settings_menu(update, context)
    return  # kein Fallback nötig


# --- Group‐Select (erste Auswahl) ---
async def _handle_group_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    all_groups = get_registered_groups()
    visible    = await get_visible_groups(update.effective_user.id, context.bot, all_groups)
    if not visible:
        return await query.message.reply_text(t(0, 'NO_VISIBLE_GROUPS'))
    kb = [
        [InlineKeyboardButton(title, callback_data=f"group_{cid}")]
        for cid, title in visible
    ]
    return await query.message.reply_text(
        t(0, 'SELECT_GROUP'),
        reply_markup=InlineKeyboardMarkup(kb)
    )


# --- General‐Callback‐Dispatcher (group=1) ---
async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data:
        return

    data = query.data

    # 1) Erstmal ACK, damit kein Timeout (Buttons reagieren)
    await query.answer()

    # 2) Kanal‐Menüs (werden in group=0 schon abgefangen, hier nur Safety-Check)
    if data.startswith('ch_'):
        return await _handle_channel(update, context)

    # 3) „Gruppe wählen“ öffnen
    if data == 'group_select':
        return await _handle_group_select(update, context)

    # 4) Nach tatsächlicher Gruppenauswahl: show_group_menu
    if data.startswith('group_'):
        chat_id = int(data.split('_', 1)[1])
        return await show_group_menu(query, context, chat_id)

    # 5) Zurück ins Hauptmenü aus Submenus
    if data.endswith('_menu_back'):
        chat_id = int(data.split('_')[0])
        return await show_group_menu(query, context, chat_id)

    # 6) Submenus
    if '_submenu_' in data:
        submenu = data.split('_submenu_', 1)[1]
        return await globals()[f"submenu_{submenu}"](query, context)

    # 7) Standard‐Aktionen
    if '_welcome_'   in data: return await globals()[f"welcome_{data.split('_')[1]}"](query, context)
    if '_rules_'     in data: return await globals()[f"rules_{data.split('_')[1]}"](query, context)
    if '_farewell_'  in data: return await globals()[f"farewell_{data.split('_')[1]}"](query, context)
    if data.endswith('_edit_mood'):    return await edit_mood(query, context)
    if data.endswith('_clean_delete'): return await clean_delete(query, context)
    if '_rss_'       in data:
        action = data.split('_rss_',1)[1]
        return await globals()[f"rss_{action}"](query, context)
    if '_toggle_'    in data:          return await toggle_stats(query, context)

    # 8) Sprache setzen
    if '_setlang_' in data:
        prefix, lang = data.split('_setlang_')
        set_group_language(int(prefix), lang)
        await query.answer(t(int(prefix), 'LANGUAGE_SET').format(lang=lang), show_alert=True)
        return await show_group_menu(query, context, int(prefix))

    # 9) Hilfe
    if data == 'help':
        return await query.message.reply_text(HELP_TEXT, parse_mode='Markdown')


# --- Registrierung der Handler ---
def register_menu(app):
    # Channel‐Menus: group=0
    app.add_handler(CallbackQueryHandler(_handle_channel, pattern=r'^ch_'), group=0)
    # Alle übrigen CallbackQueries: group=1
    app.add_handler(CallbackQueryHandler(menu_callback), group=1)