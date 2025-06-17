import feedparser
import logging
from telegram import Update
from telegram.ext import CommandHandler, CallbackContext, MessageHandler, filters
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
    logger.info(f"set_rss_feed called: chat_id={chat.id}, type={chat.type}, thread_id={update.message.message_thread_id}, args={context.args}")
    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("Bitte im Gruppenchat-Thema ausführen.")
        return
    topic_id = update.message.message_thread_id or None
    if not context.args:
        await update.message.reply_text("Verwendung: /setrss <RSS-URL>")
        return
    url = context.args[0]
    add_rss_feed(chat.id, url, topic_id)
    dest = "Hauptchat" if topic_id is None else f"Thema {topic_id}"
    await update.message.reply_text(f"✅ RSS-Feed hinzugefügt für {dest}:\n{url}")

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
# Alle eingetragenen Feeds abfragen
    feeds = get_rss_feeds()
    for chat_id, url, topic_id in feeds:
        try:
            # Bereits gepostete Links holen
            posted = get_posted_links(chat_id)
            #Feed laden und nach Datum sortieren
            feed = feedparser.parse(url)
            entries = sorted(
                feed.entries,
                key=lambda e: getattr(e, "published_parsed", 0) or 0
            )
        except Exception as e:
            logger.error(f"RSS load error for {url} in chat {chat_id}: {e}")
            continue

        for entry in entries:
            link = entry.link
            if link in posted:
                continue

            # Nachricht im Forenthema oder Hauptchat
            send_kwargs = {"chat_id": chat_id, "text": f"📰 *{entry.title}*\n{link}", "parse_mode": "Markdown"}
            if topic_id:
                send_kwargs["message_thread_id"] = topic_id

            try:
                await context.bot.send_message(**send_kwargs)
            except Exception as e:
                logger.error(f"Failed to send RSS entry to chat {chat_id}: {e}")
                # Weiter zum nächsten Eintrag, chat_id bleibt gültig
                continue

            # Nur wenn erfolgreich gesendet, als gepostet markieren
            try:
                add_posted_link(chat_id, link)
            except Exception as e:
                logger.error(f"Failed to record posted link for chat {chat_id}: {e}")

def register_rss(app):
    logger.info(f"→ register_rss: Bot.username={app.bot.username}")
    pattern = rf'^/setrss(?:@{app.bot.username})?\b'
    app.add_handler(MessageHandler(filters.Regex(pattern), set_rss_feed), group=1)
    app.add_handler(CommandHandler("listrss", list_rss_feeds))
    app.add_handler(CommandHandler("stoprss", stop_rss_feed))

    pattern = rf'^/setrss(?:@{app.bot.username})?\b'
    app.add_handler(
        MessageHandler(filters.Regex(pattern), set_rss_feed), 
        group=1  # Gruppe hinter den CommandHandlern
    )
    app.job_queue.run_repeating(fetch_rss_feed, interval=300, first=3)
