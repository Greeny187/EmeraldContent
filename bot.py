import logging
import os
import asyncio
import feedparser
import pytz

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
# BOT_TOKEN und andere Umgebungsvariablen
# ----------------------------------------------------------------------------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Der BOT_TOKEN ist nicht gesetzt. Bitte füge ihn in die Heroku Config Vars ein.")

# ----------------------------------------------------------------------------------------------------------------------
# Globale Dictionaries zum Speichern von Bild+Text je Gruppe (chat_id)
# ----------------------------------------------------------------------------------------------------------------------
# Jedes Dictionary speichert pro chat_id:
#   {"photo": file_id oder None, "text": str oder None}
welcome_data = {}
rules_data   = {}
faq_data     = {}

# ----------------------------------------------------------------------------------------------------------------------
# RSS‐Beispiele (falls du RSS‐Feeds weiterhin nutzt)
# ----------------------------------------------------------------------------------------------------------------------
rss_feeds = {}            # chat_id → [ { "url": str, "topic_id": int } ]
group_status = {}         # chat_id → bool (an/aus)
last_posted_articles = {} # chat_id → [link1, link2, ...]

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
        "  /cleandeleteaccounts – Gelöschte Konten entfernen\n"
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

    # --- Variante A: Foto wurde geschickt (Caption enthält den Text) ---
    if update.message.photo:
        # größtes Foto aus dem Array nehmen
        file_id = update.message.photo[-1].file_id

        # komplette Caption (z. B. "/setwelcome Willkommen, {user}!")
        full_caption = update.message.caption or ""
        full_caption = full_caption.strip()

        # Erster Token ist der Befehl selbst: z. B. "/setwelcome" oder "/setwelcome@BotName"
        tokens = full_caption.split(maxsplit=1)
        if len(tokens) > 1:
            # alles hinter dem ersten Token ist der eigentliche Begrüßungstext
            text = tokens[1].strip()
        else:
            text = None

        welcome_data[chat_id] = {"photo": file_id, "text": text}
        await update.message.reply_text("✅ Willkommen‐Bild (+Text) gespeichert.")
        return

    # --- Variante B: Nur reiner Text hinter "/setwelcome" ---
    if len(context.args) == 0:
        await update.message.reply_text(
            "Bitte gib den Begrüßungstext an oder sende ein Foto mit Caption.\n\n"
            "Beispiel (nur Text):\n"
            "  /setwelcome Willkommen, {user}!"
        )
        return

    # Text aus context.args zusammensetzen
    text = " ".join(context.args).strip()
    welcome_data[chat_id] = {"photo": None, "text": text}
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
        full_caption = update.message.caption or ""
        full_caption = full_caption.strip()

        tokens = full_caption.split(maxsplit=1)
        if len(tokens) > 1:
            text = tokens[1].strip()
        else:
            text = None

        rules_data[chat_id] = {"photo": file_id, "text": text}
        await update.message.reply_text("✅ Rules‐Bild (+Text) gespeichert.")
        return

    # Variante B: reiner Text
    if len(context.args) == 0:
        await update.message.reply_text(
            "Bitte gib den Regeln‐Text an oder sende ein Foto mit Caption.\n\n"
            "Beispiel (nur Text):\n"
            "  /setrules 1. Kein Spam / 2. Höflich bleiben"
        )
        return

    text = " ".join(context.args).strip()
    rules_data[chat_id] = {"photo": None, "text": text}
    await update.message.reply_text("✅ Regeln‐Text gespeichert.")

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
        full_caption = update.message.caption or ""
        full_caption = full_caption.strip()

        tokens = full_caption.split(maxsplit=1)
        if len(tokens) > 1:
            text = tokens[1].strip()
        else:
            text = None

        faq_data[chat_id] = {"photo": file_id, "text": text}
        await update.message.reply_text("✅ FAQ‐Bild (+Text) gespeichert.")
        return

    # Variante B: Nur Text
    if len(context.args) == 0:
        await update.message.reply_text(
            "Bitte gib den FAQ‐Text an oder sende ein Foto mit Caption.\n\n"
            "Beispiel (nur Text):\n"
            "  /setfaq Wie werde ich Mitglied? → Klicke auf Einladungslink …"
        )
        return

    text = " ".join(context.args).strip()
    faq_data[chat_id] = {"photo": None, "text": text}
    await update.message.reply_text("✅ FAQ‐Text gespeichert.")

# ----------------------------------------------------------------------------------------------------------------------
# 4) /welcome (manuelle Anzeige gespeicherte Begrüßung)
# ----------------------------------------------------------------------------------------------------------------------
async def show_welcome(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id
    user_name = update.effective_user.full_name
    data = welcome_data.get(chat_id)

    if data:
        if data.get("photo"):
            caption = (data.get("text") or "").replace("{user}", user_name)
            await update.message.reply_photo(photo=data["photo"], caption=caption)
        else:
            text = (data.get("text") or "").replace("{user}", user_name)
            await update.message.reply_text(text)
    else:
        await update.message.reply_text(f"Willkommen, {user_name}! 🎉")

# ----------------------------------------------------------------------------------------------------------------------
# 5) Automatisch, wenn ein neues Mitglied beitritt
# ----------------------------------------------------------------------------------------------------------------------
async def welcome_new_member(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id
    data = welcome_data.get(chat_id)

    for new_user in update.message.new_chat_members:
        if data:
            if data.get("photo"):
                caption = (data.get("text") or "").replace("{user}", new_user.full_name)
                await update.message.reply_photo(photo=data["photo"], caption=caption)
            else:
                text = (data.get("text") or "").replace("{user}", new_user.full_name)
                await update.message.reply_text(text)
        else:
            await update.message.reply_text(f"Willkommen, {new_user.full_name}! 🎉")

# ----------------------------------------------------------------------------------------------------------------------
# 6) /rules (manuelle Anzeige gespeicherter Regeln)
# ----------------------------------------------------------------------------------------------------------------------
async def rules_handler(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id
    data = rules_data.get(chat_id)
    if not data:
        await update.message.reply_text("Für diese Gruppe wurden noch keine Regeln hinterlegt. Bitte benutze /setrules.")
        return

    if data.get("photo"):
        await update.message.reply_photo(photo=data["photo"], caption=(data["text"] or ""))
    else:
        await update.message.reply_text(data.get("text", ""))

# ----------------------------------------------------------------------------------------------------------------------
# 7) /faq (manuelle Anzeige gespeicherter FAQs)
# ----------------------------------------------------------------------------------------------------------------------
async def faq_handler(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id
    data = faq_data.get(chat_id)
    if not data:
        await update.message.reply_text("Für diese Gruppe wurden noch keine FAQs hinterlegt. Bitte benutze /setfaq.")
        return

    if data.get("photo"):
        await update.message.reply_photo(photo=data["photo"], caption=(data["text"] or ""))
    else:
        await update.message.reply_text(data.get("text", ""))

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
# 10) /cleandeleteaccounts – Entfernt gelöschte Accounts aus Admin‐Liste
# ----------------------------------------------------------------------------------------------------------------------
async def clean_delete_accounts(update: Update, context: CallbackContext) -> None:
    chat = update.effective_chat

    try:
        bot_member = await context.bot.get_chat_member(chat.id, context.bot.id)
        if not bot_member.status in ["administrator", "creator"]:
            await update.message.reply_text("❌ Ich benötige Admin-Rechte, um gelöschte Konten zu entfernen.")
            return

        deleted_accounts = []
        admins = await context.bot.get_chat_administrators(chat.id)
        for admin in admins:
            user = admin.user
            if user.first_name in ["Deleted Account", "Gelöschtes Konto"] or user.username is None:
                if user.id == context.bot.id:
                    continue
                await context.bot.ban_chat_member(chat.id, user.id)
                deleted_accounts.append(user.id)

        if deleted_accounts:
            await update.message.reply_text(f"Gelöschte Konten entfernt: {len(deleted_accounts)}")
        else:
            await update.message.reply_text("Keine gelöschten Konten gefunden.")
    except Exception as error:
        logger.error(f"Fehler beim Bereinigen gelöschter Konten: {error}")
        await update.message.reply_text(f"Ein Fehler ist aufgetreten: {error}")

# ----------------------------------------------------------------------------------------------------------------------
# 11) RSS‐Funktionen (wenn du RSS noch brauchst, unverändert)
# ----------------------------------------------------------------------------------------------------------------------
async def fetch_rss_feed(context: CallbackContext) -> None:
    for chat_id, feeds in rss_feeds.items():
        if not group_status.get(chat_id, False):
            continue

        for feed_data in feeds:
            rss_url = feed_data["url"]
            topic_id = feed_data.get("topic_id")
            try:
                logger.info(f"Rufe RSS-Feed ab: {rss_url}")
                feed = feedparser.parse(rss_url)
                if feed.bozo or not feed.entries:
                    continue

                response = ""
                for article in feed.entries[:3]:
                    if article.link in last_posted_articles.get(chat_id, []):
                        continue
                    response += f"📌 <b>{article.title}</b>\n{article.link}\n\n"
                    last_posted_articles.setdefault(chat_id, []).append(article.link)

                if response.strip():
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=f"📰 <b>Neue Artikel aus dem RSS-Feed:</b>\n\n{response}",
                        parse_mode="HTML",
                        message_thread_id=topic_id,
                    )
                    logger.info(f"Artikel in Gruppe {chat_id} gepostet.")
            except Exception as error:
                logger.error(f"Fehler beim RSS-Abrufen für Chat {chat_id}: {error}")

async def set_rss_feed(update: Update, context: CallbackContext) -> None:
    if not await is_admin(update, context):
        await update.message.reply_text("❌ Nur Administratoren dürfen diesen Befehl verwenden.")
        return

    chat_id = update.effective_chat.id
    topic_id = update.message.message_thread_id

    if len(context.args) == 0:
        await update.message.reply_text("Bitte gib die URL eines RSS-Feeds an. Beispiel:\n  /setrss <URL>")
        return

    rss_url = context.args[0]
    if chat_id not in rss_feeds:
        rss_feeds[chat_id] = []

    for feed in rss_feeds[chat_id]:
        if feed["url"] == rss_url:
            await update.message.reply_text("Dieser RSS-Feed wurde bereits hinzugefügt.")
            return

    rss_feeds[chat_id].append({"url": rss_url, "topic_id": topic_id})
    await update.message.reply_text(f"✅ RSS-Feed erfolgreich hinzugefügt: {rss_url}.")

async def stop_rss_feed(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id
    if chat_id in rss_feeds:
        del rss_feeds[chat_id]
        await update.message.reply_text("✅ RSS-Feed erfolgreich gestoppt.")
    else:
        await update.message.reply_text("Es wurde kein RSS-Feed für diese Gruppe konfiguriert.")

async def list_rss_feeds(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id
    if chat_id not in rss_feeds or not rss_feeds[chat_id]:
        await update.message.reply_text("Es wurden keine RSS-Feeds für diese Gruppe konfiguriert.")
        return

    response = "📰 <b>RSS-Feeds für diese Gruppe:</b>\n"
    for idx, feed_data in enumerate(rss_feeds[chat_id], 1):
        response += f"{idx}. URL: {feed_data['url']}\n"
        if feed_data.get("topic_id"):
            response += f"   Thema-ID: {feed_data['topic_id']}\n"
    await update.message.reply_text(response, parse_mode="HTML")

# ----------------------------------------------------------------------------------------------------------------------
# 12) Spam/Link-Filter (einfaches Beispiel)
# ----------------------------------------------------------------------------------------------------------------------
async def message_filter(update: Update, context: CallbackContext) -> None:
    text = update.message.text or ""
    if 'http' in text:
        await update.message.delete()
        await update.message.reply_text("❌ Links sind nicht erlaubt!")
        return

    forbidden_words = ['badword1', 'badword2']
    if any(word in text.lower() for word in forbidden_words):
        await update.message.delete()
        await update.message.reply_text("❌ Unzulässige Wörter sind nicht erlaubt!")
        return

# ----------------------------------------------------------------------------------------------------------------------
# 13) Captcha (unverändert)
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
# 14) Forward und set_role (unverändert)
# ----------------------------------------------------------------------------------------------------------------------
async def forward_message(update: Update, context: CallbackContext) -> None:
    # Ziel‐Gruppen‐ID anpassen:
    target_chat_id = 'ZIEL_GRUPPE_ID'
    await update.message.forward(chat_id=target_chat_id)

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

    # 2) CommandHandler registrieren (Text‐Variante):
    app.add_handler(CommandHandler("start", start))

    app.add_handler(CommandHandler("setwelcome", set_welcome))
    app.add_handler(CommandHandler("setrules", set_rules))
    app.add_handler(CommandHandler("setfaq", set_faq))

    app.add_handler(CommandHandler("welcome", show_welcome))
    app.add_handler(CommandHandler("rules", rules_handler))
    app.add_handler(CommandHandler("faq", faq_handler))

    app.add_handler(CommandHandler("ban", ban))
    app.add_handler(CommandHandler("mute", mute))
    app.add_handler(CommandHandler("cleandeleteaccounts", clean_delete_accounts))

    app.add_handler(CommandHandler("setrss", set_rss_feed))
    app.add_handler(CommandHandler("listrss", list_rss_feeds))
    app.add_handler(CommandHandler("stoprss", stop_rss_feed))

    app.add_handler(CommandHandler("forward", forward_message))
    app.add_handler(CommandHandler("setrole", set_role))

    # 3) Photo‐Variante für /set… als MessageHandler registrieren
    app.add_handler(
        MessageHandler(
            filters.PHOTO & filters.CaptionRegex(r"^/setwelcome(@\w+)?"), set_welcome
        )
    )
    app.add_handler(
        MessageHandler(
            filters.PHOTO & filters.CaptionRegex(r"^/setrules(@\w+)?"), set_rules
        )
    )
    app.add_handler(
        MessageHandler(
            filters.PHOTO & filters.CaptionRegex(r"^/setfaq(@\w+)?"), set_faq
        )
    )

    # 4) MessageHandler für NEW_CHAT_MEMBERS (automatische Begrüßung)
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_member))

    # 5) MessageHandler für Captcha
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, captcha))
    app.add_handler(CallbackQueryHandler(captcha_passed, pattern='^captcha_passed$'))

    # 6) MessageHandler für Spam/Link‐Filter
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_filter))

    # 7) RSS‐Jobqueue (alle 2 Minuten)
    app.job_queue.run_repeating(fetch_rss_feed, interval=120, first=10)

    # 8) Bot starten (Polling)
    app.run_polling()

if __name__ == "__main__":
    main()
