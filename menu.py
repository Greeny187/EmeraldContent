import logging
import os
from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup,
    Update, ForceReply, InputMediaPhoto, CallbackQuery
)
from telegram.ext import CallbackQueryHandler, ContextTypes, CommandHandler
from database import (
    get_registered_groups, get_welcome, set_welcome, delete_welcome,
    get_rules, set_rules, delete_rules,
    get_farewell, set_farewell, delete_farewell,
    list_rss_feeds, remove_rss_feed,
    is_daily_stats_enabled, set_daily_stats, get_mood_question,
    set_group_language, get_group_setting
)
from handlers import (
    channel_broadcast_menu, channel_stats_menu,
    channel_pins_menu, channel_schedule_menu,
    channel_settings_menu
)
from utils import clean_delete_accounts_for_chat
from user_manual import HELP_TEXT
from access import get_visible_groups
from i18n import t

logger = logging.getLogger(__name__)

# --- Main Group Menu ---
async def show_group_menu(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    await query.answer()
    mood = get_mood_question(chat_id)
    stats_label = "Aktiv" if is_daily_stats_enabled(chat_id) else "Inaktiv"
    keyboard = [
        [InlineKeyboardButton("Begrüßung", callback_data=f"{chat_id}_submenu_welcome")],
        [InlineKeyboardButton("Regeln", callback_data=f"{chat_id}_submenu_rules")],
        [InlineKeyboardButton("Abschied", callback_data=f"{chat_id}_submenu_farewell")],
        [InlineKeyboardButton("RSS", callback_data=f"{chat_id}_submenu_rss")],
        [InlineKeyboardButton(f"📊 Statistiken: {stats_label}", callback_data=f"{chat_id}_toggle_stats")],
        [InlineKeyboardButton("✍️ Frage ändern", callback_data=f"{chat_id}_edit_mood")],
        [InlineKeyboardButton("🌐 Sprache", callback_data=f"{chat_id}_submenu_language")],
        [InlineKeyboardButton("🗑️ Cleanup", callback_data=f"{chat_id}_clean_delete")],
        [InlineKeyboardButton("📖 Hilfe", callback_data="help")],
        [InlineKeyboardButton("🔄 Gruppe wählen", callback_data="group_select")]
    ]
    text = "🔧 Gruppe verwalten – wähle eine Funktion:"  
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard))

# ------------------ Submenus ------------------
async def submenu_welcome(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_submenu_welcome')[0])
    buttons = [
        [InlineKeyboardButton('Bearbeiten', callback_data=f"{chat_id}_welcome_edit")],
        [InlineKeyboardButton('Anzeigen',  callback_data=f"{chat_id}_welcome_show")],
        [InlineKeyboardButton('Löschen',   callback_data=f"{chat_id}_welcome_delete")],
        [InlineKeyboardButton('⬅ Hauptmenü', callback_data=f"{chat_id}_menu_back")]
    ]
    await query.edit_message_text(t(chat_id,'WELCOME_MENU'), reply_markup=InlineKeyboardMarkup(buttons))

async def submenu_rules(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_submenu_rules')[0])
    buttons = [
        [InlineKeyboardButton('Bearbeiten', callback_data=f"{chat_id}_rules_edit")],
        [InlineKeyboardButton('Anzeigen',  callback_data=f"{chat_id}_rules_show")],
        [InlineKeyboardButton('Löschen',   callback_data=f"{chat_id}_rules_delete")],
        [InlineKeyboardButton('⬅ Hauptmenü', callback_data=f"{chat_id}_menu_back")]
    ]
    await query.edit_message_text(t(chat_id,'RULES_MENU'), reply_markup=InlineKeyboardMarkup(buttons))

async def submenu_farewell(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_submenu_farewell')[0])
    buttons = [
        [InlineKeyboardButton('Bearbeiten', callback_data=f"{chat_id}_farewell_edit")],
        [InlineKeyboardButton('Anzeigen',  callback_data=f"{chat_id}_farewell_show")],
        [InlineKeyboardButton('Löschen',   callback_data=f"{chat_id}_farewell_delete")],
        [InlineKeyboardButton('⬅ Hauptmenü', callback_data=f"{chat_id}_menu_back")]
    ]
    await query.edit_message_text(t(chat_id,'FAREWELL_MENU'), reply_markup=InlineKeyboardMarkup(buttons))

# ------------------ Detail Actions ------------------
async def welcome_show(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_')[0])
    text = get_welcome(chat_id) or t(chat_id,'WELCOME_NONE')
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅', callback_data=f"{chat_id}_submenu_welcome")]]))

async def welcome_edit(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_')[0])
    context.user_data['awaiting'] = 'welcome'
    context.user_data['id'] = chat_id
    await query.message.reply_text(t(chat_id,'WELCOME_PROMPT'), reply_markup=ForceReply(selective=True))

async def welcome_delete(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer(t(int(query.data.split('_')[0]),'WELCOME_DELETED'), show_alert=True)
    delete_welcome(int(query.data.split('_')[0]))
    return await submenu_welcome(query, context)

# Regeln
async def rules_show(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_')[0])
    text = get_rules(chat_id) or t(chat_id,'RULES_NONE')
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅', callback_data=f"{chat_id}_submenu_rules")]]))

async def rules_edit(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_')[0])
    context.user_data['awaiting'] = 'rules'
    context.user_data['id'] = chat_id
    await query.message.reply_text(t(chat_id,'RULES_PROMPT'), reply_markup=ForceReply(selective=True))

async def rules_delete(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer(t(int(query.data.split('_')[0]),'RULES_DELETED'), show_alert=True)
    delete_rules(int(query.data.split('_')[0]))
    return await submenu_rules(query, context)

# Abschied
async def farewell_show(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_')[0])
    text = get_farewell(chat_id) or t(chat_id,'FAREWELL_NONE')
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅', callback_data=f"{chat_id}_submenu_farewell")]]))

async def farewell_edit(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_')[0])
    context.user_data['awaiting'] = 'farewell'
    context.user_data['id'] = chat_id
    await query.message.reply_text(t(chat_id,'FAREWELL_PROMPT'), reply_markup=ForceReply(selective=True))

async def farewell_delete(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer(t(int(query.data.split('_')[0]),'FAREWELL_DELETED'), show_alert=True)
    delete_farewell(int(query.data.split('_')[0]))
    return await submenu_farewell(query, context)

# --- Language Submenu ---
async def submenu_language(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_')[0])
    keyboard = [[InlineKeyboardButton(lang.upper(), callback_data=f"{chat_id}_setlang_{lang}")] for lang in ('de','en','fr','ru')]
    keyboard.append([InlineKeyboardButton('⬅ Hauptmenü', callback_data=f"{chat_id}_menu_back")])
    await query.edit_message_text(t(chat_id, 'LANG_SELECT_PROMPT'), reply_markup=InlineKeyboardMarkup(keyboard))

# --- Toggle Daily Stats ---
async def toggle_stats(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_')[0])
    current = is_daily_stats_enabled(chat_id)
    set_daily_stats(chat_id, not current)
    status_key = 'STATS_ENABLED' if not current else 'STATS_DISABLED'
    await query.answer(t(chat_id, status_key), show_alert=True)
    return await show_group_menu(query, context, chat_id)

# --- Edit Mood Question ---
async def edit_mood(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_')[0])
    context.user_data['awaiting'] = 'mood'
    context.user_data['mood_id'] = chat_id
    await query.message.reply_text(t(chat_id, 'MOOD_PROMPT'), reply_markup=ForceReply(selective=True))

# --- Cleanup Deleted Accounts ---
async def clean_delete(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_')[0])
    count = await clean_delete_accounts_for_chat(chat_id, context.bot)
    await query.edit_message_text(t(chat_id, 'CLEANUP_DONE').format(count=count))

# --- RSS Submenu ---
async def submenu_rss(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_')[0])
    keyboard = [
        [InlineKeyboardButton('Anzeigen', callback_data=f"{chat_id}_rss_list")],
        [InlineKeyboardButton('Hinzufügen', callback_data=f"{chat_id}_rss_add")],
        [InlineKeyboardButton('Entfernen', callback_data=f"{chat_id}_rss_remove")],
        [InlineKeyboardButton('⬅ Hauptmenü', callback_data=f"{chat_id}_menu_back")]
    ]
    await query.edit_message_text(t(chat_id, 'RSS_MENU'), reply_markup=InlineKeyboardMarkup(keyboard))

# --- RSS Actions: List/Add/Remove ---
async def rss_list(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_')[0])
    feeds = list_rss_feeds(chat_id)
    text = t(chat_id, 'RSS_NONE') if not feeds else '\n'.join(feeds)
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅', callback_data=f"{chat_id}_submenu_rss")]]))

async def rss_add(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_')[0])
    context.user_data['awaiting'] = 'rss'
    context.user_data['rss_id'] = chat_id
    await query.message.reply_text(t(chat_id, 'RSS_PROMPT'), reply_markup=ForceReply(selective=True))

async def rss_remove(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    await query.answer()
    chat_id = int(query.data.split('_')[0])
    remove_rss_feed(chat_id)
    await query.edit_message_text(t(chat_id, 'RSS_REMOVED'), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅', callback_data=f"{chat_id}_submenu_rss")]]))

# --- General Callback Dispatcher ---
async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data:
        return
    data = query.data
    # Channel-specific menus
    if data.startswith('ch_'):
        return await _handle_channel(update, context)
    # Group selection
    if data == 'group_select':
        return await _handle_group_select(update, context)
    # Submenu: back to main
    if data.endswith('_menu_back'):
        chat_id = int(data.split('_')[0])
        return await show_group_menu(query, context, chat_id)
    # Dispatch by prefix
    if '_submenu_' in data:
        prefix, submenu = data.split('_submenu_', 1)
        query.data = f"{prefix}_{submenu}"
        return await globals()[f"submenu_{submenu}"](query, context)
    if '_toggle_' in data:
        return await toggle_stats(query, context)
    if '_edit_mood' in data:
        return await edit_mood(query, context)
    if '_clean_delete' in data:
        return await clean_delete(query, context)
    if '_rss_' in data:
        action = data.split('_')[1]
        return await globals()[f"rss_{action}"](query, context)
    # Language setting actions
    if '_setlang_' in data:
        chat_id, _, lang = data.partition('_setlang_')[0], None, data.split('_setlang_')[1]
        set_group_language(int(chat_id), lang)
        await query.answer(t(int(chat_id), 'LANGUAGE_SET').format(lang=lang), show_alert=True)
        return await show_group_menu(query, context, int(chat_id))
    # Help
    if data == 'help':
        return await query.message.reply_text(HELP_TEXT, parse_mode='Markdown')

# --- Channel Dispatcher Stub ---
async def _handle_channel(update, context):
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
    # fallback
    return

# --- Group Select Handler ---
async def _handle_group_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    all_groups = get_registered_groups()
    user_id = update.effective_user.id
    visible = await get_visible_groups(user_id, context.bot, all_groups)
    if not visible:
        return await query.message.reply_text(t(0, 'NO_VISIBLE_GROUPS'))
    keyboard = [[InlineKeyboardButton(title, callback_data=f"group_{cid}")] for cid, title in visible]
    return await query.message.reply_text(t(0, 'SELECT_GROUP'), reply_markup=InlineKeyboardMarkup(keyboard))

# --- Channel Menu Registration ---
def register_menu(app):
    # Channel menus group=0
    app.add_handler(CallbackQueryHandler(_handle_channel, pattern=r'^ch_'), group=0)
    # General menus group=1
    app.add_handler(CallbackQueryHandler(menu_callback), group=1)

# --- Language Commands ---
def register_language_cmds(app):
    app.add_handler(CommandHandler('setlanguage', lambda u,c: None))
    app.add_handler(CommandHandler('showlanguage', lambda u,c: None))