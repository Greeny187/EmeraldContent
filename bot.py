import logging
import os
import asyncio
import feedparser
import pytz
import psycopg2
from urllib.parse import urlparse

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackContext,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

# ----------------------------------------------------------------------------------------------------------------------
# LOGGING konfigurieren
# ----------------------------------------------------------------------------------------------------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------------------------------------------------------
# BOT_TOKEN und DATABASE_URL aus Umgebungsvariablen
# ----------------------------------------------------------------------------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
if not BOT_TOKEN:
    raise ValueError("Der BOT_TOKEN ist nicht gesetzt. Bitte füge ihn in die Heroku Config Vars ein.")
if not DATABASE_URL:
    raise ValueError("Die DATABASE_URL ist nicht gesetzt. Bitte füge das Heroku Postgres Addon hinzu und aktualisiere die Config Vars.")

# ----------------------------------------------------------------------------------------------------------------------
# PostgreSQL-Verbindung aufbauen
# ----------------------------------------------------------------------------------------------------------------------
result = urlparse(DATABASE_URL)
conn = psycopg2.connect(
    dbname=result.path.lstrip("/"),
    user=result.username,
    password=result.password,
    host=result.hostname,
    port=result.port,
    sslmode="require",
)
conn.autocommit = True

# Tabellen beim Start anlegen (wenn sie nicht existieren)
with conn.cursor() as cur:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS welcome (
            chat_id    BIGINT PRIMARY KEY,
            photo_id   TEXT,
            text       TEXT
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS rules (
            chat_id    BIGINT PRIMARY KEY,
            photo_id   TEXT,
            text       TEXT
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS faq (
            chat_id    BIGINT PRIMARY KEY,
            photo_id   TEXT,
            text       TEXT
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS rss_feeds (
            chat_id    BIGINT,
            url        TEXT,
            topic_id   BIGINT,
            PRIMARY KEY (chat_id, url)
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS last_posts (
            chat_id    BIGINT,
            link       TEXT,
            PRIMARY KEY (chat_id, link)
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS farewell (
            chat_id   BIGINT PRIMARY KEY,
            photo_id  TEXT,
            text      TEXT
        );
    """)

# ----------------------------------------------------------------------------------------------------------------------
# Hilfsfunktion: Prüfen, ob der Request‐Sender Inhaber oder Admin ist
# - Wenn Gruppen‐Inhaber anonym postet, steht `update.message.sender_chat.id == chat_id`.
# - Sonst prüfen wir den Status via get_chat_member.
# ----------------------------------------------------------------------------------------------------------------------
async def is_admin(update: Update, context: CallbackContext) -> bool:
    chat_id = update.effective_chat.id

    # 1) Anonymer Owner (sender_chat = Gruppe selbst) → sofort erlauben
    if (
        update.message
        and getattr(update.message, "sender_chat", None) is not None
        and update.message.sender_chat.id == chat_id
    ):
        return True

    # 2) Normale Admin‐Prüfung (Benutzer‐ID)
    user_id = update.effective_user.id
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        if member.status in ["creator", "administrator"]:
            return True
    except Exception as e:
        logger.error(f"Fehler beim Admin‐Check: {e}")

    return False

# ----------------------------------------------------------------------------------------------------------------------
# /start → Kurzinfo, welche Befehle der Bot hat
# ----------------------------------------------------------------------------------------------------------------------
async def start(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text(
        "👋 Hallo! Ich bin dein Gruppen‐Manager-Bot.\n\n"
        "Verfügbare Befehle:\n"
        "  /setwelcome   – Begrüßung (Bild + Text oder nur Text) festlegen\n"
        "  /welcome      – Begrüßung anzeigen (manuell oder beim Betreten)\n"
        "  /setrules     – Regeln (Bild + Text oder nur Text) festlegen\n"
        "  /rules        – Regeln anzeigen\n"
        "  /setfaq       – FAQ (Bild + Text oder nur Text) festlegen\n"
        "  /faq          – FAQ anzeigen\n"
        "  /ban          – Benutzer bannen (Antwort auf deren Nachricht)\n"
        "  /mute         – Benutzer stummschalten (Antwort)\n"
        "  /setrss       – RSS‐Feed setzen (Admin)\n"
        "  /listrss      – RSS‐Feeds auflisten\n"
        "  /stoprss      – RSS‐Feed stoppen\n\n"
        "📌 Die /set… Befehle dürfen nur Admins bzw. der anonym gepostete Inhaber ausführen."
    )

# ----------------------------------------------------------------------------------------------------------------------
# 1) /setwelcome – Begrüßung festlegen (Foto mit Caption oder reiner Text)
# ----------------------------------------------------------------------------------------------------------------------
async def set_welcome(update: Update, context: CallbackContext) -> None:
    if not await is_admin(update, context):
        await update.message.reply_text("❌ Nur Administratoren (oder Inhaber) dürfen den Begrüßungstext setzen.")
        return

    chat_id = update.effective_chat.id

    # Variante A: Foto + Caption
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        full_caption = (update.message.caption or "").strip()
        tokens = full_caption.split(maxsplit=1)
        text = tokens[1].strip() if len(tokens) > 1 else None

        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO welcome (chat_id, photo_id, text)
                VALUES (%s, %s, %s)
                ON CONFLICT (chat_id) DO UPDATE
                SET photo_id = EXCLUDED.photo_id, text = EXCLUDED.text;
            """, (chat_id, file_id, text))
        await update.message.reply_text("✅ Willkommen‐Bild (+Text) gespeichert.")
        return

    # Variante B: Nur reiner Text
    if len(context.args) == 0:
        await update.message.reply_text(
            "Bitte gib den Begrüßungstext an oder sende ein Foto mit Caption.\n\n"
            "Beispiel (nur Text):\n"
            "  /setwelcome Willkommen, {user}!"
        )
        return

    text = " ".join(context.args).strip()
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO welcome (chat_id, photo_id, text)
            VALUES (%s, NULL, %s)
            ON CONFLICT (chat_id) DO UPDATE
            SET photo_id = NULL, text = EXCLUDED.text;
        """, (chat_id, text))
    await update.message.reply_text("✅ Willkommen‐Text gespeichert.")

# ----------------------------------------------------------------------------------------------------------------------
# 2) /setrules – Regeln festlegen (Foto mit Caption oder reiner Text)
# ----------------------------------------------------------------------------------------------------------------------
async def set_rules(update: Update, context: CallbackContext) -> None:
    if not await is_admin(update, context):
        await update.message.reply_text("❌ Nur Administratoren (oder Inhaber) dürfen die Regeln setzen.")
        return

    chat_id = update.effective_chat.id

    # Variante A: Foto + Caption
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        full_caption = (update.message.caption or "").strip()
        tokens = full_caption.split(maxsplit=1)
        text = tokens[1].strip() if len(tokens) > 1 else None

        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO rules (chat_id, photo_id, text)
                VALUES (%s, %s, %s)
                ON CONFLICT (chat_id) DO UPDATE
                SET photo_id = EXCLUDED.photo_id, text = EXCLUDED.text;
            """, (chat_id, file_id, text))
        await update.message.reply_text("✅ Rules‐Bild (+Text) gespeichert.")
        return

    # Variante B: Nur reiner Text
    if len(context.args) == 0:
        await update.message.reply_text(
            "Bitte gib den Regeln‐Text an oder sende ein Foto mit Caption.\n\n"
            "Beispiel (nur Text):\n"
            "  /setrules 1. Kein Spam / 2. Höflich bleiben"
        )
        return

    text = " ".join(context.args).strip()
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO rules (chat_id, photo_id, text)
            VALUES (%s, NULL, %s)
            ON CONFLICT (chat_id) DO UPDATE
            SET photo_id = NULL, text = EXCLUDED.text;
        """, (chat_id, text))
    await update.message.reply_text("✅ Regeln‐Text gespeichert.")

# ----------------------------------------------------------------------------------------------------------------------
# 2.2) /setfarewell – Abschied festlegen (Foto mit Caption oder reiner Text)
# ----------------------------------------------------------------------------------------------------------------------

async def set_farewell(update: Update, context: CallbackContext) -> None:
    if not await is_admin(update, context):
        await update.message.reply_text("❌ Nur Administratoren dürfen die Abschiedsnachricht setzen.")
        return

    chat_id = update.effective_chat.id

    # Variante A: Foto + Caption
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        full_caption = (update.message.caption or "").strip()
        tokens = full_caption.split(maxsplit=1)
        text = tokens[1].strip() if len(tokens) > 1 else None

        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO farewell (chat_id, photo_id, text)
                VALUES (%s, %s, %s)
                ON CONFLICT (chat_id) DO UPDATE
                SET photo_id = EXCLUDED.photo_id, text = EXCLUDED.text;
            """, (chat_id, file_id, text))
        await update.message.reply_text("✅ Abschieds‐Bild (+Text) gespeichert.")
        return

    # Variante B: Nur Text
    if len(context.args) == 0:
        await update.message.reply_text(
            "Bitte gib den Abschiedstext an oder sende ein Foto mit Caption.\n\n"
            "Beispiel (nur Text):\n"
            "  /setfarewell Auf Wiedersehen, {user}!"
        )
        return

    text = " ".join(context.args).strip()
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO farewell (chat_id, photo_id, text)
            VALUES (%s, NULL, %s)
            ON CONFLICT (chat_id) DO UPDATE
            SET photo_id = NULL, text = EXCLUDED.text;
        """, (chat_id, text))
    await update.message.reply_text("✅ Abschieds‐Text gespeichert.")

# ----------------------------------------------------------------------------------------------------------------------
# 3) /setfaq – FAQ festlegen (Foto mit Caption oder reiner Text)
# ----------------------------------------------------------------------------------------------------------------------
async def set_faq(update: Update, context: CallbackContext) -> None:
    if not await is_admin(update, context):
        await update.message.reply_text("❌ Nur Administratoren (oder Inhaber) dürfen den FAQ‐Text setzen.")
        return

    chat_id = update.effective_chat.id

    # Variante A: Foto + Caption
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        full_caption = (update.message.caption or "").strip()
        tokens = full_caption.split(maxsplit=1)
        text = tokens[1].strip() if len(tokens) > 1 else None

        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO faq (chat_id, photo_id, text)
                VALUES (%s, %s, %s)
                ON CONFLICT (chat_id) DO UPDATE
                SET photo_id = EXCLUDED.photo_id, text = EXCLUDED.text;
            """, (chat_id, file_id, text))
        await update.message.reply_text("✅ FAQ‐Bild (+Text) gespeichert.")
        return

    # Variante B: Nur reiner Text
    if len(context.args) == 0:
        await update.message.reply_text(
            "Bitte gib den FAQ‐Text an oder sende ein Foto mit Caption.\n\n"
            "Beispiel (nur Text):\n"
            "  /setfaq Wie werde ich Mitglied? → Klicke auf Einladungslink …"
        )
        return

    text = " ".join(context.args).strip()
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO faq (chat_id, photo_id, text)
            VALUES (%s, NULL, %s)
            ON CONFLICT (chat_id) DO UPDATE
            SET photo_id = NULL, text = EXCLUDED.text;
        """, (chat_id, text))
    await update.message.reply_text("✅ FAQ‐Text gespeichert.")

# ----------------------------------------------------------------------------------------------------------------------
# 4) /welcome (manuelle Anzeige gespeicherte Begrüßung)
# ----------------------------------------------------------------------------------------------------------------------
async def show_welcome(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id
    user_name = update.effective_user.full_name

    with conn.cursor() as cur:
        cur.execute("SELECT photo_id, text FROM welcome WHERE chat_id = %s;", (chat_id,))
        row = cur.fetchone()

    if row:
        photo_id, text = row
        if photo_id:
            caption = (text or "").replace("{user}", user_name)
            await update.message.reply_photo(photo=photo_id, caption=caption)
        else:
            text_formatted = (text or "").replace("{user}", user_name)
            await update.message.reply_text(text_formatted)
    else:
        await update.message.reply_text(f"Willkommen, {user_name}! 🎉")

# ----------------------------------------------------------------------------------------------------------------------
# 4.1) /farewell (manuelle Anzeige gespeicherte Begrüßung)
# ----------------------------------------------------------------------------------------------------------------------

async def show_farewell(update: Update, context: CallbackContext) -> None:
    chat_id   = update.effective_chat.id
    user_name = update.effective_user.full_name

    with conn.cursor() as cur:
        cur.execute("SELECT photo_id, text FROM farewell WHERE chat_id = %s;", (chat_id,))
        row = cur.fetchone()

    if row:
        photo_id, text = row
        formatted = (text or "").replace("{user}", user_name)
        if photo_id:
            await update.message.reply_photo(photo=photo_id, caption=formatted)
        else:
            await update.message.reply_text(formatted)
    else:
        await update.message.reply_text(f"Auf Wiedersehen, {user_name}! 👋")

# ----------------------------------------------------------------------------------------------------------------------
# 5) Automatisch, wenn ein neues Mitglied beitritt
# ----------------------------------------------------------------------------------------------------------------------
async def welcome_new_member(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id

    with conn.cursor() as cur:
        cur.execute("SELECT photo_id, text FROM welcome WHERE chat_id = %s;", (chat_id,))
        row = cur.fetchone()

    for new_user in update.message.new_chat_members:
        if row:
            photo_id, text = row
            if photo_id:
                caption = (text or "").replace("{user}", new_user.full_name)
                await update.message.reply_photo(photo=photo_id, caption=caption)
            else:
                formatted = (text or "").replace("{user}", new_user.full_name)
                await update.message.reply_text(formatted)
        else:
            await update.message.reply_text(f"Willkommen, {new_user.full_name}! 🎉")

# ----------------------------------------------------------------------------------------------------------------------
# 5.2) Automatisch, wenn ein neues Mitglied austritt
# ----------------------------------------------------------------------------------------------------------------------

async def farewell_member(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id
    left    = update.message.left_chat_member

    with conn.cursor() as cur:
        cur.execute("SELECT photo_id, text FROM farewell WHERE chat_id = %s;", (chat_id,))
        row = cur.fetchone()

    if not row:
        return  # keine Abschiedsnachricht definiert

    photo_id, text = row
    formatted = (text or "").replace("{user}", left.full_name)
    if photo_id:
        await update.message.reply_photo(photo=photo_id, caption=formatted)
    else:
        await update.message.reply_text(formatted)

# ----------------------------------------------------------------------------------------------------------------------
# 6) /rules (manuelle Anzeige gespeicherter Regeln)
# ----------------------------------------------------------------------------------------------------------------------
async def rules_handler(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id

    with conn.cursor() as cur:
        cur.execute("SELECT photo_id, text FROM rules WHERE chat_id = %s;", (chat_id,))
        row = cur.fetchone()

    if not row:
        await update.message.reply_text("Für diese Gruppe wurden noch keine Regeln hinterlegt. Bitte benutze /setrules.")
        return

    photo_id, text = row
    if photo_id:
        await update.message.reply_photo(photo=photo_id, caption=(text or ""))
    else:
        await update.message.reply_text(text or "")

# ----------------------------------------------------------------------------------------------------------------------
# 7) /faq (manuelle Anzeige gespeicherter FAQs)
# ----------------------------------------------------------------------------------------------------------------------
async def faq_handler(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id

    with conn.cursor() as cur:
        cur.execute("SELECT photo_id, text FROM faq WHERE chat_id = %s;", (chat_id,))
        row = cur.fetchone()

    if not row:
        await update.message.reply_text("Für diese Gruppe wurden noch keine FAQs hinterlegt. Bitte benutze /setfaq.")
        return

    photo_id, text = row
    if photo_id:
        await update.message.reply_photo(photo=photo_id, caption=(text or ""))
    else:
        await update.message.reply_text(text or "")

# ----------------------------------------------------------------------------------------------------------------------
# 8) /ban – Einfaches Bannen per Reply
# ----------------------------------------------------------------------------------------------------------------------
async def ban(update: Update, context: CallbackContext) -> None:
    if update.message.reply_to_message:
        user_id = update.message.reply_to_message.from_user.id
        await update.message.chat.ban_member(user_id)
        await update.message.reply_text(f"{update.message.reply_to_message.from_user.full_name} wurde gebannt.")
    else:
        await update.message.reply_text("Bitte antworte auf die Nachricht des Benutzers, den du bannen möchtest.")

# ----------------------------------------------------------------------------------------------------------------------
# 9) /mute – Einfaches Stummschalten per Reply
# ----------------------------------------------------------------------------------------------------------------------
async def mute(update: Update, context: CallbackContext) -> None:
    if update.message.reply_to_message:
        user_id = update.message.reply_to_message.from_user.id
        await update.message.chat.mute_member(user_id)
        await update.message.reply_text(f"{update.message.reply_to_message.from_user.full_name} wurde stummgeschaltet.")
    else:
        await update.message.reply_text("Bitte antworte auf die Nachricht des Benutzers, den du stummschalten möchtest.")

# ----------------------------------------------------------------------------------------------------------------------
# 11) RSS‐Funktionen
# ----------------------------------------------------------------------------------------------------------------------
async def fetch_rss_feed(context: CallbackContext) -> None:
    """
    Diese Funktion ruft gespeicherte RSS-Feeds ab und postet neue Artikel in der jeweiligen Gruppe.
    """
    logger.info("Abrufen von RSS-Feeds gestartet.")
    with conn.cursor() as cur:
        cur.execute("SELECT chat_id, url, topic_id FROM rss_feeds;")
        feeds = cur.fetchall()

    for chat_id, feed_url, topic_id in feeds:
        logger.info(f"Rufe Feed {feed_url} für Chat {chat_id} ab.")
        try:
            feed = feedparser.parse(feed_url)

            if feed.bozo:  # Fehler im Feed
                logger.warning(f"Fehler beim Abrufen des Feeds {feed_url}: {feed.bozo_exception}")
                continue

            new_articles = []
            with conn.cursor() as cur:
                for entry in feed.entries[:3]:
                    # Prüfen, ob Link schon gepostet wurde
                    cur.execute("SELECT 1 FROM last_posts WHERE chat_id=%s AND link=%s;", (chat_id, entry.link))
                    if cur.fetchone():
                        continue
                    new_articles.append(entry)
                    cur.execute("""
                        INSERT INTO last_posts (chat_id, link)
                        VALUES (%s, %s)
                        ON CONFLICT (chat_id, link) DO NOTHING;
                    """, (chat_id, entry.link))
                    # Nur die letzten 10 Links pro Chat speichern
                    cur.execute("""
                        DELETE FROM last_posts
                        WHERE chat_id = %s AND link NOT IN (
                            SELECT link FROM last_posts WHERE chat_id=%s ORDER BY link DESC LIMIT 10
                        );
                    """, (chat_id, chat_id))

            if new_articles:
                response = "\n\n".join([f"<b>{article.title}</b>\n{article.link}" for article in new_articles])
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"📢 Neue Artikel:\n\n{response}",
                    parse_mode="HTML",
                    message_thread_id=topic_id,
                )
                logger.info(f"Neue Artikel im Chat {chat_id} gepostet.")
            else:
                logger.info(f"Keine neuen Artikel für {feed_url}.")
        except Exception as e:
            logger.error(f"Fehler beim Abrufen des Feeds {feed_url}: {e}")

async def set_rss_feed(update: Update, context: CallbackContext) -> None:
    if not await is_admin(update, context):
        await update.message.reply_text("❌ Nur Administratoren dürfen diesen Befehl verwenden.")
        return

    chat_id = update.effective_chat.id
    topic_id = update.message.message_thread_id or 0

    if len(context.args) == 0:
        await update.message.reply_text("Bitte gib die URL eines RSS-Feeds an. Beispiel:\n  /setrss <URL>")
        return

    rss_url = context.args[0]
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM rss_feeds WHERE chat_id=%s AND url=%s;", (chat_id, rss_url))
        if cur.fetchone():
            await update.message.reply_text("Dieser RSS-Feed wurde bereits hinzugefügt.")
            return
        cur.execute("""
            INSERT INTO rss_feeds (chat_id, url, topic_id)
            VALUES (%s, %s, %s);
        """, (chat_id, rss_url, topic_id))
    await update.message.reply_text(f"✅ RSS-Feed erfolgreich hinzugefügt: {rss_url}.")

async def stop_rss_feed(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM rss_feeds WHERE chat_id=%s;", (chat_id,))
        if not cur.fetchone():
            await update.message.reply_text("Es wurde kein RSS-Feed für diese Gruppe konfiguriert.")
            return
        cur.execute("DELETE FROM rss_feeds WHERE chat_id=%s;", (chat_id,))
    await update.message.reply_text("✅ RSS-Feed erfolgreich gestoppt.")

async def list_rss_feeds(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id
    with conn.cursor() as cur:
        cur.execute("SELECT url FROM rss_feeds WHERE chat_id=%s;", (chat_id,))
        rows = cur.fetchall()

    if not rows:
        await update.message.reply_text("Es wurden keine RSS-Feeds für diese Gruppe konfiguriert.")
        return

    response = "📰 <b>RSS-Feeds für diese Gruppe:</b>\n"
    for idx, (url,) in enumerate(rows, 1):
        response += f"{idx}. URL: {url}\n"
    await update.message.reply_text(response, parse_mode="HTML")

# ----------------------------------------------------------------------------------------------------------------------
# 12) Spam/Link-Filter (Links nur von Admins erlaubt)
# ----------------------------------------------------------------------------------------------------------------------
async def message_filter(update: Update, context: CallbackContext) -> None:
    text = update.message.text or ""
    if 'http' in text:
        if not await is_admin(update, context):
            await update.message.delete()
            await update.message.reply_text("❌ Links sind nur für Administratoren erlaubt!")
        # Admins dürfen Links posten: nichts weiter tun
        return

    forbidden_words = ['badword1', 'badword2']
    if any(word in text.lower() for word in forbidden_words):
        await update.message.delete()
        await update.message.reply_text("❌ Unzulässige Wörter sind nicht erlaubt!")

# ----------------------------------------------------------------------------------------------------------------------
# 13) Captcha
# ----------------------------------------------------------------------------------------------------------------------
async def captcha(update: Update, context: CallbackContext) -> None:
    keyboard = [[InlineKeyboardButton("Ich bin kein Roboter", callback_data='captcha_passed')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Bitte bestätige, dass du kein Roboter bist.", reply_markup=reply_markup)

async def captcha_passed(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(text="Captcha erfolgreich! Willkommen!")

# ----------------------------------------------------------------------------------------------------------------------
# 14) Forward und set_role
# ----------------------------------------------------------------------------------------------------------------------
async def forward_message(update: Update, context: CallbackContext) -> None:
    # Ziel‐Gruppen‐ID anpassen:
    target_chat_id = os.getenv("FORWARD_CHAT_ID")
    if not target_chat_id:
        await update.message.reply_text("🚫 Es ist keine Ziel-Gruppen-ID konfiguriert. Setze die Umgebungsvariable FORWARD_CHAT_ID.")
        return
    await update.message.forward(chat_id=int(target_chat_id))

async def set_role(update: Update, context: CallbackContext) -> None:
    if update.message.reply_to_message:
        user_id = update.message.reply_to_message.from_user.id
        role = context.args[0] if context.args else "Mitglied"
        await update.message.chat.promote_member(user_id, can_change_info=True, can_post_messages=True)
        await update.message.reply_text(f"Rolle {role} wurde zugewiesen!")
    else:
        await update.message.reply_text("Bitte antworte auf die Nachricht des Benutzers, dem du eine Rolle geben möchtest.")

# ----------------------------------------------------------------------------------------------------------------------
# 15) “main” – Handler registrieren und Polling starten
# ----------------------------------------------------------------------------------------------------------------------
def main() -> None:
    # 1) Application erstellen
    app = Application.builder().token(BOT_TOKEN).build()

    # 2) CommandHandler registrieren
    app.add_handler(CommandHandler("start", start))

    app.add_handler(CommandHandler("setwelcome", set_welcome))
    app.add_handler(CommandHandler("setrules", set_rules))
    app.add_handler(CommandHandler("setfaq", set_faq))
    app.add_handler(CommandHandler("setfarewell", set_farewell))

    app.add_handler(CommandHandler("welcome", show_welcome))
    app.add_handler(CommandHandler("rules", rules_handler))
    app.add_handler(CommandHandler("faq", faq_handler))
    app.add_handler(CommandHandler("farewell", show_farewell))

    app.add_handler(CommandHandler("ban", ban))
    app.add_handler(CommandHandler("mute", mute))

    app.add_handler(CommandHandler("setrss", set_rss_feed))
    app.add_handler(CommandHandler("listrss", list_rss_feeds))
    app.add_handler(CommandHandler("stoprss", stop_rss_feed))

    app.add_handler(CommandHandler("forward", forward_message))
    app.add_handler(CommandHandler("setrole", set_role))

    # 3) Photo‐Variante für /set… als MessageHandler
    app.add_handler(MessageHandler(filters.PHOTO & filters.CaptionRegex(r"^/setwelcome(@\w+)?"), set_welcome))
    app.add_handler(MessageHandler(filters.PHOTO & filters.CaptionRegex(r"^/setrules(@\w+)?"), set_rules))
    app.add_handler(MessageHandler(filters.PHOTO & filters.CaptionRegex(r"^/setfaq(@\w+)?"), set_faq))

    # 4) MessageHandler für NEW_CHAT_MEMBERS (automatische Begrüßung)
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_member))

    # 4) MessageHandler für NEW_CHAT_MEMBERS (automatische Begrüßung)
    app.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, farewell_member))

    # 5) MessageHandler für Captcha
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, captcha))
    app.add_handler(CallbackQueryHandler(captcha_passed, pattern='^captcha_passed$'))

    # 6) MessageHandler für Spam/Link‐Filter
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_filter))

    # 7) RSS‐Jobqueue alle 2 Minuten
    app.job_queue.run_repeating(fetch_rss_feed, interval=120, first=3)

    # 8) Bot starten (Polling)
    app.run_polling()

if __name__ == "__main__":
    main()

