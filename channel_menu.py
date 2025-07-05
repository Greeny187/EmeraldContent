import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from database import get_registered_channels  # falls du das brauchst
from telegram import Message  # falls du Media-Edits nutzt
from i18n import t

logger = logging.getLogger(__name__)

# --- Kanal-Hauptmenü ---
async def channel_mgmt_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chan_id = int(query.data.split("_", 1)[1])
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
    await query.edit_message_text(
        t(chan_id, 'CHANNEL_MENU_HEADER').format(title=title),
        reply_markup=InlineKeyboardMarkup(kb)
    )

# --- Kanal-Submenus ---
async def channel_broadcast_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chan_id = int(query.data.split("_", 1)[1])
    context.user_data["broadcast_chan"] = chan_id
    return await query.edit_message_text(
        t(chan_id, 'CHANNEL_BROADCAST_PROMPT')
    )

async def channel_stats_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chan_id = int(query.data.split("_", 1)[1])
    chat = await context.bot.get_chat(chan_id)
    subs = await chat.get_members_count()
    text = t(chan_id, 'CHANNEL_STATS_HEADER').format(count=subs)
    return await query.edit_message_text(text)

async def channel_pins_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chan_id = int(query.data.split("_", 1)[1])
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
    chan_id = int(query.data.split("_", 1)[1])
    # Annahme: list_scheduled_posts gibt List[Tuple[text, cron]]
    from database import list_scheduled_posts
    schedules = list_scheduled_posts(chan_id)
    lines = [t(chan_id, 'CHANNEL_SCHEDULE_HEADER')]
    for text, cron in schedules:
        lines.append(f"• {cron} → «{text[:30]}…»")
    kb = [
        [InlineKeyboardButton(t(chan_id, 'CHANNEL_SCHEDULE_ADD'),
                              callback_data=f"ch_schedule_add_{chan_id}")],
        [InlineKeyboardButton(t(chan_id, 'BACK'),
                              callback_data=f"channel_{chan_id}")]
    ]
    return await query.edit_message_text("\n".join(lines),
                                         reply_markup=InlineKeyboardMarkup(kb))

async def channel_settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chan_id = int(query.data.split("_", 1)[1])
    kb = [
        [InlineKeyboardButton(t(chan_id, 'CHANNEL_SETTINGS_TITLE'),
                              callback_data=f"ch_settitle_{chan_id}")],
        [InlineKeyboardButton(t(chan_id, 'CHANNEL_SETTINGS_DESC'),
                              callback_data=f"ch_setdesc_{chan_id}")],
        [InlineKeyboardButton(t(chan_id, 'BACK'),
                              callback_data=f"channel_{chan_id}")]
    ]
    return await query.edit_message_text(
        t(chan_id, 'CHANNEL_SETTINGS_HEADER'),
        reply_markup=InlineKeyboardMarkup(kb)
    )