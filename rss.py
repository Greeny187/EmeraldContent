import feedparser
import logging
from telegram import Update, ForceReply
from telegram.ext import CommandHandler, CallbackContext, MessageHandler, filters, ContextTypes
from database import (add_rss_feed, list_rss_feeds as db_list_rss_feeds, remove_rss_feed as db_remove_rss_feed,
    get_rss_feeds, set_rss_topic, get_posted_links, add_posted_link, get_rss_topic)

logger = logging.getLogger(__name__)

async def set_rss_topic_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    msg  = update.effective_message

    # Nur in Gruppen/Supergruppen zulassen
    if chat.type not in ("group", "supergroup"):
        return await msg.reply_text("❌ `/settopicrss` nur in Gruppen möglich.")

    # 1) Wenn im Thema ausgeführt, nimmt message_thread_id
    topic_id = msg.message_thread_id or None
    # 2) Oder, falls als Reply in einem Thema
    if not topic_id and msg.reply_to_message:
        topic_id = msg.reply_to_message.message_thread_id

    if not topic_id:
        return await msg.reply_text(
            "⚠️ Bitte führe `/settopicrss` in dem gewünschten Forum-Thema aus "
            "oder antworte auf eine Nachricht darin."
        )

    # In DB speichern
    set_rss_topic(chat.id, topic_id)
    await msg.reply_text(f"✅ RSS-Posting-Thema gesetzt auf Topic {topic_id}.")

async def set_rss_feed(update: Update, context: CallbackContext):
    """ 
    /setrss <URL>  oder  Menü-Flow via ForceReply
    Funktioniert im Privat-Chat wie in Gruppen, solange  ein RSS-Topic gesetzt ist.
    """
    chat_id = update.effective_chat.id
    # prüfen, ob ein Topic existiert
    topic_id = get_rss_topic(chat_id)
    if not topic_id:
        return await update.message.reply_text(
            "❗ Kein RSS-Topic gesetzt. Bitte erst mit /settopicrss im Gruppen-Thread ein Thema festlegen."
        )

    # 1) Kommando-Flow: /setrss <url>
    if context.args:
        url = context.args[0]
        add_rss_feed(chat_id, url, topic_id)
        return await update.message.reply_text(
            f"✅ RSS-Feed hinzugefügt für Topic {topic_id}:\n{url}"
        )

    # 2) Menü-Flow: URL per ForceReply abholen
    context.user_data["awaiting_rss_url"] = True
    context.user_data["rss_group_id"] = chat_id
    return await update.message.reply_text(
        "➡ Bitte sende jetzt die RSS-URL für dieses Thema:",
        reply_markup=ForceReply(selective=True)
    )

async def list_rss_feeds(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    feeds = db_list_rss_feeds(chat_id)
    if not feeds:
        await update.message.reply_text("Keine RSS-Feeds gesetzt.")
    else:
        msg = "Aktive RSS-Feeds:\n" + "\n".join(f"- {url} (Topic {tid})" for url, tid in feeds)
        await update.message.reply_text(msg)

async def stop_rss_feed(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    if context.args:
        url = context.args[0]
        db_remove_rss_feed(chat_id, url)
        await update.message.reply_text(f"RSS-Feed entfernt:\n{url}")
    else:
        db_remove_rss_feed(chat_id)
        await update.message.reply_text("Alle RSS-Feeds entfernt.")

async def fetch_rss_feed(context: CallbackContext):
    for chat_id, url, topic_id in get_rss_feeds():
        posted = get_posted_links(chat_id)
        feed = feedparser.parse(url)
        entries = sorted(feed.entries, key=lambda e: getattr(e, "published_parsed", 0) or 0)
        for entry in entries:
            if entry.link in posted:
                continue
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    message_thread_id=topic_id,
                    text=f"📰 *{entry.title}*\n{entry.link}",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Failed to send RSS entry: {e}")
            add_posted_link(chat_id, entry.link)

async def rss_url_reply(update, context):
    """
    Callback für Menü-Flow: wenn awaiting_rss_url gesetzt ist, wird hier die URL abgeholt.
    """
    logger.info(f"➜ rss_url_reply: awaiting_rss_url={context.user_data.get('awaiting_rss_url')}")
    if not context.user_data.pop("awaiting_rss_url", False):
        return
    url = update.message.text.strip()
    chat_id = context.user_data.pop("rss_group_id")

    # Safety-Check: Topic darf nicht verschwunden sein
    topic_id = get_rss_topic(chat_id)
    if not topic_id:
        return await update.message.reply_text(
            "❗ Dein RSS-Topic wurde zwischenzeitlich entfernt. Bitte zuerst /settopicrss ausführen."
        )
    add_rss_feed(chat_id, url, topic_id)
    await update.message.reply_text(
        f"✅ RSS-Feed erfolgreich hinzugefügt für Topic {topic_id}:\n{url}"
    )

def register_rss(app):

    # RSS-Befehle
    app.add_handler(CommandHandler("setrss",   set_rss_feed))
    app.add_handler(CommandHandler("listrss",  list_rss_feeds))
    app.add_handler(CommandHandler("stoprss",  stop_rss_feed))
    app.add_handler(CommandHandler("settopicrss", set_rss_topic_cmd, filters=filters.ChatType.GROUPS))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, rss_url_reply), group=0)
    
    # Job zum Einlesen
    app.job_queue.run_repeating(fetch_rss_feed, interval=300, first=10)
