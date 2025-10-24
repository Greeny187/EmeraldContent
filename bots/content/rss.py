import feedparser
import logging
import time, re
from telegram import Update, ForceReply
from telegram.ext import CommandHandler, CallbackContext, filters, ContextTypes
from telegram.error import BadRequest
from .database import (add_rss_feed, list_rss_feeds as db_list_rss_feeds, remove_rss_feed as db_remove_rss_feed, 
prune_posted_links, get_group_language, set_rss_feed_options, get_rss_feeds_full, set_rss_topic_for_group_feeds, 
get_last_posted_link, set_last_posted_link, update_rss_http_cache, get_ai_settings, set_pending_input, set_rss_topic_for_feed)
from .utils import ai_summarize
import html

logger = logging.getLogger(__name__)

_ALLOWED = {"b","strong","i","em","u","s","del","ins","code","pre","a","br"}

def _sanitize_html(t: str) -> str:
    if not t: return ""
    # p/br zu Zeilenumbrüchen
    t = re.sub(r"</?p[^>]*>", "\n", t, flags=re.I)
    t = re.sub(r"<br\s*/?>", "\n", t, flags=re.I)
    # Links normalisieren: nur href behalten
    def _a_repl(m):
        href = m.group("href"); text = m.group("text")
        return f'<a href="{href}">{text}</a>' if href else text
    t = re.sub(r'<a\b[^>]*?href="(?P<href>[^"]+)"[^>]*>(?P<text>.*?)</a>', _a_repl, t, flags=re.I|re.S)
    # Alle anderen Tags killen, außer erlaubten
    def _tag_repl(m):
        tag = m.group(1).lower()
        return m.group(0) if tag in _ALLOWED else ""
    t = re.sub(r"</?([a-z0-9]+)(\s[^>]*?)?>", _tag_repl, t, flags=re.I)
    return html.unescape(t).strip()

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
    # Für alle Feeds der Gruppe setzen (nur rss_feeds, kein group_settings)
    set_rss_topic_for_group_feeds(chat.id, topic_id)
    await msg.reply_text(f"✅ RSS-Topic aktualisiert: Alle Feeds posten nun in Topic {topic_id}.")

async def set_rss_feed(update: Update, context: CallbackContext):
    """
    /setrss <URL> [images=on|off]
    oder via Menü-Flow (ForceReply), dann nur URL.
    """
    chat_id = update.effective_chat.id
    # Nur noch aktuelles Thread-Topic verwenden; wenn keiner, dann Hauptchat (0)
    topic_id = getattr(update.effective_message, "message_thread_id", None) or 0

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
        return await update.message.reply_text("➡️ Bitte sende jetzt die RSS-URL:", reply_markup=ForceReply(selective=True))

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

async def _send_rss_entry(bot, chat_id: int, topic_id: int, caption: str, img_url: str | None) -> None:
    """Versendet eine RSS-Nachricht; nutzt Topic nur wenn >0, mit sinnvollen Fallbacks."""
    kwargs = {"parse_mode": "HTML"}
    if isinstance(topic_id, int) and topic_id > 0:
        kwargs["message_thread_id"] = topic_id

    if img_url:
        await bot.send_photo(chat_id=chat_id, photo=img_url, caption=caption, **kwargs)
    else:
        await bot.send_message(chat_id=chat_id, text=caption, **kwargs)


def _pick_image(entry) -> str | None:
    """Extrahiert eine brauchbare Bild-URL aus einem feedparser-Entry (wenn vorhanden)."""
    try:
        if "media_content" in entry and entry.media_content:
            u = entry.media_content[0].get("url")
            if u: return u
        if "image" in entry and isinstance(entry.image, dict):
            u = entry.image.get("href") or entry.image.get("url")
            if u: return u
        for l in entry.get("links", []):
            if l.get("rel") in ("enclosure", "alternate") and "image" in (l.get("type") or ""):
                u = l.get("href")
                if u: return u
    except Exception:
        pass
    return None


async def fetch_rss_feed(context):
    """
    Zyklischer Job: zieht Feeds aus der DB (rss_feeds) und postet neue Einträge.
    Fixes:
      - saubere Indentation
      - definierte Variablen (chat_id, url, post_images, fail_streak etc.)
      - Topic wird nur gesetzt, wenn >0 (Non-Forum-Gruppen funktionieren)
      - Fallback bei Thread-Fehlern (posten ohne Topic) + Auto-Korrektur topic_id=0
      - ai_rss korrekt aus get_ai_settings entpackt
    """
    logger = logging.getLogger(__name__)
    rows = get_rss_feeds_full()  # [(chat_id, url, topic_id, etag, last_mod, post_images, enabled), ...]

    for row in rows:
        try:
            chat_id, url, topic_id, last_etag, last_modified, post_images, enabled = row
        except Exception:
            logger.error("RSS: Unerwartetes Row-Format: %r", row)
            continue

        # NULL/False-Handling kommt bereits aus get_rss_feeds_full() per COALESCE, aber doppelt hält besser:
        if enabled is False:
            continue

        # --- HTTP-Cache für feedparser
        http_kwargs = {}
        if last_etag:
            http_kwargs["etag"] = last_etag
        if last_modified:
            http_kwargs["modified"] = last_modified

        try:
            feed = feedparser.parse(url, **http_kwargs)
            # neue Cache-Werte speichern (wenn vorhanden)
            if feed.get("etag") or feed.get("modified"):
                update_rss_http_cache(chat_id, url, feed.get("etag"), feed.get("modified"))
        except Exception as e:
            logger.error("RSS parse fail for %s: %s", url, e)
            continue

        entries = list(feed.entries or [])
        if not entries:
            continue

        # Welche Einträge sind neu?
        last_link = get_last_posted_link(chat_id, url)
        links = [e.get("link") for e in entries if e.get("link")]
        if last_link and last_link in links:
            idx = links.index(last_link)
            new_entries = entries[:idx]  # feedparser: i.d.R. newest-first → alles vor last_link ist neu
        else:
            # noch nie gepostet → nur den neuesten Eintrag
            new_entries = entries[:1]

        # oldest-first posten, max. 3
        new_entries = list(reversed(new_entries[-3:]))

        # AI-Option korrekt entpacken
        _, ai_rss = get_ai_settings(chat_id)

        fail_streak = 0  # ← jetzt definiert
        for entry in new_entries:
            title = (entry.get("title") or "").strip()
            link = entry.get("link") or ""
            summary = (entry.get("summary") or entry.get("description") or "").strip()

            # Caption bauen (sanitizen!)
            clean_summary = _sanitize_html(summary)
            base_text = f"<b>{_sanitize_html(title)}</b>\n{clean_summary}\n\n<a href=\"{link}\">Weiterlesen</a>"
            caption = base_text
            try:
                if ai_rss:
                    # kurze KI-Zusammenfassung voranstellen
                    short = await ai_summarize(base_text, lang="de")
                    caption = f"<b>Kurzfassung</b>: {short}\n\n{base_text}"
            except Exception as e:
                logger.warning("AI summary failed for %s: %s", url, e)

            img_url = _pick_image(entry) if post_images else None

            # Senden mit Fallbacks
            try:
                await _send_rss_entry(context.bot, chat_id, int(topic_id), caption, img_url)
                set_last_posted_link(chat_id, url, link)
                fail_streak = 0
            except BadRequest as e:
                msg = str(e).lower()

                # Ungültiger/gelöschter Thread → ohne Topic posten + Topic in DB auf 0 setzen
                if "message thread" in msg or "message_thread_id" in msg or "not found" in msg:
                    try:
                        await _send_rss_entry(context.bot, chat_id, 0, caption, img_url)
                        set_rss_topic_for_feed(chat_id, url, 0)
                        set_last_posted_link(chat_id, url, link)
                        fail_streak = 0
                        continue
                    except Exception as e2:
                        logging.getLogger(__name__).error("RSS-Fallback (ohne Topic) scheiterte: %s", e2)

                # Caption/HTML-Fehler → Foto ohne Caption + Text separat
                if "caption is too long" in msg or "can't parse entities" in msg:
                    try:
                        if img_url:
                            await context.bot.send_photo(chat_id=chat_id, photo=img_url)
                        await context.bot.send_message(chat_id=chat_id, text=caption, parse_mode="HTML")
                        set_last_posted_link(chat_id, url, link)
                        fail_streak = 0
                        continue
                    except Exception as e2:
                        logging.getLogger(__name__).error("RSS Caption/HTML Fallback scheiterte: %s", e2)

                logging.getLogger(__name__).error("RSS send failed (%s): %s", url, e)
                fail_streak += 1
            except Exception as e:
                logging.getLogger(__name__).error("RSS unexpected send error (%s): %s", url, e)
                fail_streak += 1


def register_rss(app):
    # RSS-Befehle
    app.add_handler(CommandHandler("setrss",   set_rss_feed))
    app.add_handler(CommandHandler("listrss",  list_rss_feeds))
    app.add_handler(CommandHandler("stoprss",  stop_rss_feed))
    app.add_handler(CommandHandler("settopicrss", set_rss_topic_cmd, filters=filters.ChatType.GROUPS))
    
    # Job zum Einlesen
    app.job_queue.run_repeating(fetch_rss_feed, interval=300, first=1)


