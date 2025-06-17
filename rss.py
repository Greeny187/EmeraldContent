import feedparser
import logging
from telegram import Update
from telegram.ext import CommandHandler, CallbackContext
from database import (
    add_rss_feed,
    list_rss_feeds as db_list_rss_feeds,
    remove_rss_feed as db_remove_rss_feed,
    get_rss_feeds,
    get_posted_links,
    add_posted_link
)

logger = logging.getLogger(__name__)

async def set_rss_feed(update: Update, context: CallbackContext):
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("Bitte im Gruppenchat-Thema ausführen.")
        return
    topic_id = update.message.message_thread_id or None
    if not context.args:
        await update.message.reply_text("Verwendung: /setrss <RSS-URL>")
        return
    url = context.args[0]
    add_rss_feed(chat.id, url, topic_id)
    await update.message.reply_text(f"RSS-Feed hinzugefügt für Thema {topic_id}:\n{url}")

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
            # Wenn topic_id gesetzt: im Forenthema posten
            if topic_id:
                await context.bot.send_message(
                    chat_id=chat_id,
                    message_thread_id=topic_id,
                    text=f"📰 *{entry.title}*\n{entry.link}",
                    parse_mode="Markdown"
                )
            else:
            # Sonst normal in den Hauptchat
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"📰 *{entry.title}*\n{entry.link}",
                    parse_mode="Markdown"
                )
        except Exception as e:
            logger.error(f"Failed to send RSS entry: {e}")
    add_posted_link(chat_id, entry.link)

def register_rss(app):

    app.add_handler(CommandHandler("setrss", set_rss_feed))
    app.add_handler(CommandHandler("listrss", list_rss_feeds))
    app.add_handler(CommandHandler("stoprss", stop_rss_feed))
    app.job_queue.run_repeating(fetch_rss_feed, interval=300, first=3)
