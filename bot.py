import logging
import feedparser
import asyncio
import pytz
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackContext, MessageHandler, CallbackQueryHandler, filters
)

# Logging konfigurieren
logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Der BOT_TOKEN ist nicht gesetzt. Bitte füge ihn zu den Heroku Config Vars hinzu.")

# Globale Variablen

# ========== Globale Speicher für pro-Group-Strings ==========
welcome_texts = {}  # chat_id → willkommen-Nachricht
rules_texts   = {}  # chat_id → regelsatz
faq_texts     = {}  # chat_id → faq-Text

# Struktur: {chat_id: {topic_id: [rss_urls]}} # Speichert die RSS-URLs und Themen-IDs für Gruppen
rss_feeds = {}  

# Speichert den Aktivierungsstatus für Gruppen
group_status = {}  

# Speichert die zuletzt geposteten Artikel
last_posted_articles = {}  

# Abfrage Admin-/Inhaberrechte

async def is_admin(update: Update, context: CallbackContext) -> bool:
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    try:
        chat_member = await context.bot.get_chat_member(chat_id, user_id)
        # Hier prüfen wir explizit auf "administrator" ODER "creator"
        if chat_member.status in ["administrator", "creator"]:
            return True
    except Exception as e:
        logging.error(f"Fehler beim Überprüfen der Adminrechte: {e}")
    return False

# Kommando-Handler für den Startbefehl
async def start(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text("Hallo! Ich bin dein Gruppenverwaltungs-Bot.")

# Aktivieren des Bots
async def start_bot(update: Update, context: CallbackContext) -> None:
    if not await is_admin(update, context):
        await update.message.reply_text("Nur Administratoren dürfen den Bot starten.")
        return
    chat_id = update.effective_chat.id
    group_status[chat_id] = True
    await update.message.reply_text("Der Bot wurde für diese Gruppe aktiviert.")

# Deaktivieren des Bots
async def stop_bot(update: Update, context: CallbackContext) -> None:
    if not await is_admin(update, context):
        await update.message.reply_text("Nur Administratoren dürfen den Bot stoppen.")
        return
    chat_id = update.effective_chat.id
    group_status[chat_id] = False
    await update.message.reply_text("Der Bot wurde für diese Gruppe deaktiviert.")

# --- Bot-Funktionen ---

# -----------------------------------
# setwelcome: Legt die Welcome-Nachricht fest
async def set_welcome(update: Update, context: CallbackContext) -> None:
    if not await is_admin(update, context):
        await update.message.reply_text("Nur Administratoren dürfen den Willkommens-Text setzen.")
        return

    chat_id = update.effective_chat.id
    if len(context.args) == 0:
        await update.message.reply_text("Bitte gib den Begrüßungstext an. Beispiel:\n/setwelcome Willkommen in unserer Gruppe, {user}!")
        return

    # Alles nach dem Befehl (/setwelcome) zusammenfügen
    text = " ".join(context.args)
    welcome_texts[chat_id] = text
    await update.message.reply_text("Begrüßungstext gespeichert.")

# -----------------------------------
# setrules: Legt den Rules-Text fest
async def set_rules(update: Update, context: CallbackContext) -> None:
    if not await is_admin(update, context):
        await update.message.reply_text("Nur Administratoren dürfen den Rules-Text setzen.")
        return

    chat_id = update.effective_chat.id
    if len(context.args) == 0:
        await update.message.reply_text("Bitte gib den Regeln-Text an. Beispiel:\n/setrules 1. Kein Spam 2. Höflicher Umgang ...")
        return

    text = " ".join(context.args)
    rules_texts[chat_id] = text
    await update.message.reply_text("Regeln gespeichert.")

# -----------------------------------
# setfaq: Legt den FAQ-Text fest
async def set_faq(update: Update, context: CallbackContext) -> None:
    if not await is_admin(update, context):
        await update.message.reply_text("Nur Administratoren dürfen den FAQ-Text setzen.")
        return

    chat_id = update.effective_chat.id
    if len(context.args) == 0:
        await update.message.reply_text("Bitte gib den FAQ-Text an. Beispiel:\n/setfaq Wie werde ich Mitglied? → Klicke auf Einladungslink ...")
        return

    text = " ".join(context.args)
    faq_texts[chat_id] = text
    await update.message.reply_text("FAQ-Text gespeichert.")

# -----------------------------------

async def welcome(update: Update, context: CallbackContext) -> None:
    new_member = update.message.new_chat_members[0]
    chat_id = update.effective_chat.id

    # Standard-Fallback, falls noch kein Text gesetzt wurde
    default_text = f"Willkommen, {new_member.full_name}! 🎉"
    text_template = welcome_texts.get(chat_id, default_text)

    # Ersetze Platzhalter {user} mit dem Namen des neuen Mitglieds
    message = text_template.replace("{user}", new_member.full_name)
    await update.message.reply_text(message)

# /rules: Gibt die für diese Gruppe gespeicherten Regeln zurück
async def rules(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id
    text = rules_texts.get(chat_id)
    if not text:
        await update.message.reply_text("Für diese Gruppe wurden noch keine Regeln hinterlegt. Bitte benutze /setrules, um sie festzulegen.")
    else:
        await update.message.reply_text(text)

# -----------------------------------
# /faq: Gibt die gespeicherten FAQs zurück
async def faq(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id
    text = faq_texts.get(chat_id)
    if not text:
        await update.message.reply_text("Für diese Gruppe wurden noch keine FAQs hinterlegt. Bitte benutze /setfaq, um sie festzulegen.")
    else:
        await update.message.reply_text(text)

# Funktion zum Bannen eines Benutzers
async def ban(update: Update, context: CallbackContext) -> None:
    if update.message.reply_to_message:  # Der Bot muss eine Antwort auf eine Nachricht haben
        user_id = update.message.reply_to_message.from_user.id
        await update.message.chat.ban_member(user_id)  # Bann eines Benutzers
        await update.message.reply_text(f"{update.message.reply_to_message.from_user.full_name} wurde gebannt.")
    else:
        await update.message.reply_text("Bitte antworte auf die Nachricht des Benutzers, den du bannen möchtest.")

# Funktion zum Stummschalten eines Benutzers
async def mute(update: Update, context: CallbackContext) -> None:
    if update.message.reply_to_message:
        user_id = update.message.reply_to_message.from_user.id
        await update.message.chat.mute_member(user_id)  # Stummschalten eines Benutzers
        await update.message.reply_text(f"{update.message.reply_to_message.from_user.full_name} wurde stummgeschaltet.")
    else:
        await update.message.reply_text("Bitte antworte auf die Nachricht des Benutzers, den du stummschalten möchtest.")

# Funktion Entfernen gelöschter Accounts

async def clean_delete_accounts(update: Update, context: CallbackContext) -> None:
    chat = update.effective_chat

    try:
        # Überprüfen, ob der Bot Admin-Rechte hat
        bot_member = await context.bot.get_chat_member(chat.id, context.bot.id)
        if not bot_member.status in ["administrator", "creator"]:
            await update.message.reply_text(
                "Ich benötige Administratorrechte, um gelöschte Konten zu entfernen."
            )
            return

        # Mitgliederliste manuell iterieren (Telegram-API hat keinen direkten Zugriff auf alle Mitglieder)
        deleted_accounts = []
        members = await chat.get_members()  # Beispielhaft: Du brauchst eine Methode zur Iteration der Mitglieder

        for member in members:
            user = member.user

            # Prüfen, ob das Konto als gelöscht markiert ist
            if user.first_name in ["Deleted Account", "Gelöschtes Konto"] or user.username is None:
                if user.id == context.bot.id:
                    continue  # Bot selbst nicht entfernen

                # Benutzer bannen
                await context.bot.ban_chat_member(chat.id, user.id)
                deleted_accounts.append(user.id)

        # Rückmeldung an den Benutzer
        if deleted_accounts:
            await update.message.reply_text(f"Gelöschte Konten entfernt: {len(deleted_accounts)}")
        else:
            await update.message.reply_text("Keine gelöschten Konten gefunden.")
    except Exception as error:
        logging.error(f"Fehler beim Bereinigen gelöschter Konten: {error}")
        await update.message.reply_text(f"Ein Fehler ist aufgetreten: {error}")

# Globale Variablen für die RSS-Funktion
rss_feeds = {}  # Struktur: {chat_id: {topic_id: [rss_urls]}}

# Funktion zum Abrufen von RSS-Feeds
async def fetch_rss_feed(context=None):
    for chat_id, feeds in rss_feeds.items():  # Iteriere über Gruppen und deren Feeds
        if not group_status.get(chat_id, False):  # Gruppe aktiv?
            logging.info(f"Bot ist für Gruppe {chat_id} deaktiviert.")
            continue

        for feed_data in feeds:  # Iteriere über Feeds in der Gruppe
            rss_url = feed_data["url"]
            topic_id = feed_data.get("topic_id")

            try:
                logging.info(f"Rufe RSS-Feed ab: {rss_url}")
                feed = feedparser.parse(rss_url)

                if feed.bozo:
                    logging.warning(f"Ungültiger RSS-Feed für Chat {chat_id}: {rss_url}")
                    continue

                if not feed.entries:
                    logging.warning(f"Keine Artikel im RSS-Feed für {chat_id} gefunden.")
                    continue

                # Nur neue Artikel posten
                response = ""
                for article in feed.entries[:3]:  # Letzte 3 Artikel
                    if article.link in last_posted_articles.get(chat_id, []):
                        logging.info(f"Artikel bereits gepostet: {article.link}")
                        continue

                    response += f"📌 <b>{article.title}</b>\n{article.link}\n\n"
                    last_posted_articles.setdefault(chat_id, []).append(article.link)

                # Sende die Artikel, wenn neue vorhanden sind
                if response.strip():
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=f"📰 <b>Neue Artikel aus dem RSS-Feed:</b>\n\n{response}",
                        parse_mode="HTML",
                        message_thread_id=topic_id,
                    )
                    logging.info(f"Artikel erfolgreich in Gruppe {chat_id} gepostet.")
            except Exception as error:
                logging.error(f"Fehler beim Abrufen des RSS-Feeds für Chat {chat_id}: {error}")

# RSS-Feed setzen
async def set_rss_feed(update: Update, context: CallbackContext) -> None:
    if not await is_admin(update, context):
        await update.message.reply_text("Nur Administratoren dürfen diesen Befehl verwenden.")
        return

    chat_id = update.effective_chat.id
    topic_id = update.message.message_thread_id

    if len(context.args) == 0:
        await update.message.reply_text("Bitte gib die URL eines RSS-Feeds an. Beispiel: /setrss <URL>")
        return

    rss_url = context.args[0]

    # Füge die Gruppe hinzu, falls sie noch nicht existiert
    if chat_id not in rss_feeds:
        rss_feeds[chat_id] = []

    # Überprüfe, ob die URL bereits existiert
    for feed in rss_feeds[chat_id]:
        if feed["url"] == rss_url:
            await update.message.reply_text("Dieser RSS-Feed wurde bereits hinzugefügt.")
            return

    # Feed hinzufügen, da er noch nicht existiert
    rss_feeds[chat_id].append({"url": rss_url, "topic_id": topic_id})
    await update.message.reply_text(f"RSS-Feed erfolgreich hinzugefügt: {rss_url}.")

# Befehl zum Stoppen des RSS-Feeds
async def stop_rss_feed(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id

    if chat_id in rss_feeds:
        del rss_feeds[chat_id]
        await update.message.reply_text("RSS-Feed erfolgreich gestoppt.")
    else:
        await update.message.reply_text("Es wurde kein RSS-Feed für diese Gruppe konfiguriert.")

# RSS-Feeds auflisten
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

# Filter für Spam und Links
async def message_filter(update: Update, context: CallbackContext) -> None:
    # Beispiel für Filter von Links
    if 'http' in update.message.text:
        await update.message.delete()  # Lösche die Nachricht
        await update.message.reply_text("Links sind nicht erlaubt!")
    # Beispiel für einen Wortfilter
    forbidden_words = ['badword1', 'badword2']
    if any(word in update.message.text.lower() for word in forbidden_words):
        await update.message.delete()  # Lösche die Nachricht
        await update.message.reply_text("Unzul\u00e4ssige W\u00f6rter sind nicht erlaubt!")

# Funktion zur Erzeugung eines Captchas
async def captcha(update: Update, context: CallbackContext) -> None:
    # Eine einfache Aufgabe für das Captcha
    keyboard = [
        [InlineKeyboardButton("Ich bin kein Roboter", callback_data='captcha_passed')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Bitte bestätige, dass du kein Roboter bist.", reply_markup=reply_markup)

# Funktion zur Handhabung der Captcha-Bestätigung
async def captcha_passed(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(text="Captcha erfolgreich! Willkommen!")

# Weiterleitungsbefehl
async def forward_message(update: Update, context: CallbackContext) -> None:
    # Ersetze 'ZIEL_GRUPPE_ID' mit der tatsächlichen Zielgruppen-ID
    target_chat_id = 'ZIEL_GRUPPE_ID'
    await update.message.forward(chat_id=target_chat_id)

# Rollenvergabe an Mitglieder
async def set_role(update: Update, context: CallbackContext) -> None:
    if update.message.reply_to_message:
        user_id = update.message.reply_to_message.from_user.id
        role = context.args[0] if context.args else "Mitglied"  # Standardrolle: Mitglied
        # Beispielhafte Rolle vergeben (Benutzer anpassen)
        await update.message.chat.promote_member(user_id, can_change_info=True, can_post_messages=True)  # Beispielrolle
        await update.message.reply_text(f"Rolle {role} wurde zugewiesen!")

# --- Main-Funktion ---

def main():
    # Application erstellen
    app = Application.builder().token(BOT_TOKEN).build()

    # Registrierung der Kommandohandler
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("startbot", start_bot))
    app.add_handler(CommandHandler("stopbot", stop_bot))
    app.add_handler(CommandHandler("ban", ban))
    app.add_handler(CommandHandler("mute", mute))
    app.add_handler(CommandHandler("cleandeleteaccounts", clean_delete_accounts))
    app.add_handler(CommandHandler("faq", faq))
    app.add_handler(CommandHandler("rules",rules))
    app.add_handler(CommandHandler("forward", forward_message))
    app.add_handler(CommandHandler("setrole", set_role))
    app.add_handler(CommandHandler("setrss", set_rss_feed))
    app.add_handler(CommandHandler("listrss", list_rss_feeds))
    app.add_handler(CommandHandler("stoprss", stop_rss_feed))
    app.add_handler(CommandHandler("setwelcome", set_welcome))
    app.add_handler(CommandHandler("setrules", set_rules))
    app.add_handler(CommandHandler("setfaq", set_faq))

  # Registrierung der Nachricht-Handler
    app.add_handler(MessageHandler(filters.TEXT, message_filter))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, captcha))
    app.add_handler(CallbackQueryHandler(captcha_passed, pattern='^captcha_passed$'))
    app.add_handler(CommandHandler("rules", rules))
    app.add_handler(CommandHandler("faq", faq))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome))

    # RSS-Job über job_queue alle 2 Minuten ausführen
    app.job_queue.run_repeating(fetch_rss_feed, interval=120, first=10)

    # Telegram Bot-Setup
    app.run_polling()

if __name__ == "__main__":
    main()
