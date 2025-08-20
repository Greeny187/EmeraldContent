import feedparser
import logging
import os, time, re
from telegram import Update, ForceReply
from telegram.ext import CommandHandler, CallbackContext, MessageHandler, filters, ContextTypes
from database import (add_rss_feed, list_rss_feeds as db_list_rss_feeds, remove_rss_feed as db_remove_rss_feed, 
prune_posted_links, get_group_language, set_rss_feed_options, get_rss_feeds_full, set_rss_topic, get_rss_topic, 
get_last_posted_link, set_last_posted_link, update_rss_http_cache, get_ai_settings, set_pending_input, 
get_pending_input, clear_pending_input
)
from utils import ai_summarize

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
    /setrss <URL> [images=on|off]
    oder via Menü-Flow (ForceReply), dann nur URL.
    """
    chat_id = update.effective_chat.id
    topic_id = get_rss_topic(chat_id)
    if not topic_id:
        return await update.message.reply_text(
            "❗ Kein RSS-Topic gesetzt. Bitte erst mit /settopicrss im gewünschten Thread ausführen."
        )

    url = None
    post_images = None

    if context.args:
        url = context.args[0].strip()
        # optionale Flags
        for arg in context.args[1:]:
            if arg.lower().startswith("images="):
                post_images = arg.split("=",1)[1].lower() in ("on","true","1","yes","y")

    if not url:
        context.user_data["awaiting_rss_url"] = True
        context.user_data["rss_group_id"] = chat_id
        set_pending_input(update.effective_chat.id, update.effective_user.id, "rss_url",
                          {"target_chat_id": chat_id})
        return await update.message.reply_text("➡ Bitte sende jetzt die RSS-URL:", reply_markup=ForceReply(selective=True))

    add_rss_feed(chat_id, url, topic_id)
    if post_images is not None:
        set_rss_feed_options(chat_id, url, post_images=post_images)
    return await update.message.reply_text(f"✅ RSS-Feed hinzugefügt (Topic {topic_id}):\n{url}")

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
    """
    Optimiert:
    - If-Modified-Since / ETag (via feedparser)
    - postet bis zu 3 neue Einträge pro Durchlauf (älteste zuerst)
    - optional: Bild-Vorschau (enclosure/media/erste <img>)
    - optional: KI-Zusammenfassung (kurzer TL;DR in Gruppen-Sprache)
    """
    start = time.time()
    for chat_id, url, topic_id, etag, last_mod, post_images, enabled in get_rss_feeds_full():
        if not enabled:
            continue

        lang = get_group_language(chat_id) or "de"
        ai_rss = get_ai_settings(chat_id)
        kwargs = {}
        if etag:      kwargs["etag"] = etag
        if last_mod:  kwargs["modified"] = last_mod

        try:
            feed = feedparser.parse(url, **kwargs)
        except Exception as e:
            logger.error(f"RSS parse fail for {url}: {e}")
            continue

        # HTTP 304 / nichts Neues
        if getattr(feed, "status", None) == 304:
            continue

        # HTTP-Cache aktualisieren
        try:
            update_rss_http_cache(chat_id, url, getattr(feed, "etag", None), getattr(feed, "modified", None))
        except Exception:
            pass

        entries = list(feed.entries or [])
        if not entries:
            continue

        # robust sortieren (published/updated)
        def _ts(e):
            return getattr(e, "published_parsed", None) or getattr(e, "updated_parsed", None) or 0
        entries.sort(key=_ts)

        last_link = get_last_posted_link(chat_id, url)
        to_post, found_last = [], last_link is None
        for entry in entries:
            link = getattr(entry, "link", None)
            if not link: 
                continue
            if not found_last:
                if link == last_link: 
                    found_last = True
                continue
            to_post.append(entry)

        # poste maximal 3 (älteste zuerst, um Reihenfolge zu halten)
        to_post = to_post[-3:] if to_post else ([] if last_link else [entries[-1]])
        fail_streak = 0
        for entry in to_post:
            title = getattr(entry, "title", "Neuer Artikel")
            link  = getattr(entry, "link", None)
            if not link:
                continue

            # Bild extrahieren (falls gewünscht)
            img_url = None
            if post_images:
                try:
                    if getattr(entry, "media_content", None):
                        img_url = entry.media_content[0].get("url")
                    if not img_url and getattr(entry, "media_thumbnail", None):
                        img_url = entry.media_thumbnail[0].get("url")
                    if not img_url and getattr(entry, "summary", None):
                        m = re.search(r'<img[^>]+src="([^"]+)"', entry.summary, re.I)
                        if m: img_url = m.group(1)
                except Exception:
                    img_url = None

            # optional KI-Zusammenfassung
            summary = None
            if ai_rss:
                parts = []
                if getattr(entry, "title", None): parts.append(entry.title)
                if getattr(entry, "summary", None): parts.append(re.sub("<.*?>", " ", entry.summary))
                if getattr(entry, "description", None): parts.append(re.sub("<.*?>", " ", entry.description))
                base_text = "\n\n".join(p for p in parts if p)[:4000]
                try:
                    summary = await ai_summarize(base_text, lang=lang)
                except Exception as e:
                    logger.info(f"AI summary skipped: {e}")

            # Nachricht senden
            caption = f"📰 <b>{title}</b>\n{link}"
            if summary:
                caption += f"\n\n<b>TL;DR</b> {summary}"

            try:
                if img_url:
                    await context.bot.send_photo(
                        chat_id=chat_id, photo=img_url, caption=caption,
                        message_thread_id=topic_id, parse_mode="HTML"
                    )
                else:
                    await context.bot.send_message(
                        chat_id=chat_id, text=caption,
                        message_thread_id=topic_id, parse_mode="HTML"
                    )
                set_last_posted_link(chat_id, url, link)
                fail_streak = 0  # success -> reset
            except Exception as e:
                logger.error(f"Failed to post RSS entry: {e}")
                fail_streak += 1
                continue

        # Auto-Fallback: wenn Bilder dreimal in Folge scheitern -> post_images=False
        try:
            if fail_streak >= 3 and post_images:
                set_rss_feed_options(chat_id, url, post_images=False)
                logger.info(f"RSS auto-fallback: post_images disabled for {url} in chat {chat_id}")
        except Exception:
            pass

        # optional Hausputz
        try:
            prune_posted_links(chat_id, keep_last=200)
        except Exception:
            pass

    logger.debug(f"fetch_rss_feed took {(time.time()-start):.3f}s")

async def rss_url_reply(update, context):
    """
    Callback für Menü-Flow: wenn awaiting_rss_url gesetzt ist, wird hier die URL abgeholt.
    Nur gültig, wenn es eine Antwort (Reply) auf unsere Aufforderung ist.
    """
    # STRIKTER: Wir akzeptieren nur echte Replies auf unsere ForceReply-Botnachricht.
    # Zusätzlich: DB-Fallback, falls user_data verloren ging.
    msg = update.message
    if not msg:
        return
    replied_to = msg.reply_to_message
    if not replied_to.from_user or replied_to.from_user.id != context.bot.id:
        return
    if not getattr(replied_to.reply_markup, 'force_reply', False):
        return

    target_chat_id = context.user_data.get("rss_group_id")
    if not context.user_data.get("awaiting_rss_url", False) or not target_chat_id:
        pend = get_pending_input(msg.chat.id, update.effective_user.id, "rss_url")
        if not pend:
            return
        target_chat_id = int(pend.get("target_chat_id") or 0)
        if target_chat_id:
            # Aufräumen, damit das Pending nicht „kleben“ bleibt
            clear_pending_input(msg.chat.id, update.effective_user.id, "rss_url")
        # setze einmalig Flags, damit der Rest des Codes identisch bleiben kann
        context.user_data["awaiting_rss_url"] = True
        context.user_data["rss_group_id"] = target_chat_id

    # Normale Route: echte Reply auf ForceReply
    replied_to = msg.reply_to_message
    is_proper_reply = (
        replied_to and replied_to.from_user and replied_to.from_user.id == context.bot.id
        and getattr(replied_to.reply_markup, 'force_reply', False)
    )
    # DB-Fallback (z.B. nach Neustart): pending inputs vorhanden?
    db_pending = get_pending_input(msg.chat.id, update.effective_user.id, "rss_url")
    if not is_proper_reply and not db_pending:
        return  # weder richtige Reply noch Pending vorhanden
    
    url = (msg.text or "").strip()
    chat_id = context.user_data.get("rss_group_id") or (db_pending and db_pending.get("chat_id"))
    
    # Safety-Checks
    if not chat_id:
        return
    topic_id = get_rss_topic(chat_id)
    if not topic_id:
        # Flags räumen, falls Topic verschwunden
        context.user_data.pop("awaiting_rss_url", None)
        context.user_data.pop("rss_group_id", None)
        return await msg.reply_text(
            "❗ Kein RSS-Topic gesetzt. Bitte zuerst /settopicrss im gewünschten Thread ausführen."
        )

    # Kollision mit anderen Flows vermeiden
    context.user_data.pop("awaiting_mood_question", None)
    context.user_data.pop("last_edit", None)

    add_rss_feed(chat_id, url, topic_id)
    context.user_data.pop("awaiting_rss_url", None)
    context.user_data.pop("rss_group_id", None)
    clear_pending_input(chat_id, update.effective_user.id, "rss_url")
    await msg.reply_text(f"✅ RSS-Feed hinzugefügt (Topic {topic_id}):\n{url}")

def register_rss(app):
    # RSS-Befehle
    app.add_handler(CommandHandler("setrss",   set_rss_feed))
    app.add_handler(CommandHandler("listrss",  list_rss_feeds))
    app.add_handler(CommandHandler("stoprss",  stop_rss_feed))
    app.add_handler(CommandHandler("settopicrss", set_rss_topic_cmd, filters=filters.ChatType.GROUPS))
    
    # SPEZIFISCHERER Handler: Nur Replies und nur wenn RSS-Flag gesetzt
    app.add_handler(MessageHandler(
        filters.REPLY & filters.TEXT & ~filters.COMMAND, 
        rss_url_reply
    ), group=3)
    
    # Job zum Einlesen
    app.job_queue.run_repeating(fetch_rss_feed, interval=300, first=1)
