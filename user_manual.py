from telegram import Update, ReplyKeyboardMarkup, ForceReply
from telegram.ext import ContextTypes, CommandHandler

HELP_TEXT = '''
*GreenyGroupManager Bot – Benutzerhandbuch*

Willkommen zum offiziellen Handbuch. Dieses „Papierbuch“ ist in vier Kapitel gegliedert:

*Inhaltsverzeichnis*  
1️⃣ Funktionen im Überblick  
2️⃣ Bot-Chat: Menü & Untermenüs  
3️⃣ Gruppen-Chat: Befehle & Abläufe  
4️⃣ Support & Kontakt  

---

*1️⃣ Funktionen im Überblick*  
• **Themenverantwortliche zuweisen** (`/settopic`):  
  – per Reply, Text-Mention oder Username  
• **Gelöschte Accounts entfernen** (`/cleandeleteaccounts`):  
  – Admin-only, Ban+Unban über alle Mitglieder  
• **Tägliche Statistik**:  
  – Top-3-Poster per JobQueue (08:00 Uhr)  
  – im Bot-Chat manuell togglebar  
• **Mood-Meter**:  
  – Gruppen-Umfrage mit 👍👎🤔  
  – Live-Zählung & Anpassung der Frage  
• **Handbuch anzeigen** (`/help`)

---

*2️⃣ Bot-Chat: Menü & Untermenüs*  

2.1 Hauptmenü:  
┌─────────────────────────────┐
│ GreenyGroupManager │
├─────────────────────────────┤
│ /start → Gruppenauswahl │
│ /menu → Hauptmenü der Gruppe│
└─────────────────────────────┘

bash
Kopieren
Bearbeiten

2.2 Gruppen-Hauptmenü (nach Auswahl or `/menu`):  
┌────────────────────────────────────────────┐
│ 🔧 Gruppe [Chat-Titel] – Hauptmenü │
├────────────────────────────────────────────┤
│ Begrüßung │ Regeln │
│ Farewell │ Linksperre │
│ RSS │ 🗑 Accounts entfernen │
│ 📊 Statistik [Aktiv/Inaktiv] │
│ ✍️ Mood-Frage ändern │ 📖 Handbuch │
└────────────────────────────────────────────┘

yaml
Kopieren
Bearbeiten

- **📊 Statistik** togglet tägliche Reports ein/aus.  
- **✍️ Mood-Frage ändern** startet `ForceReply` zum Editieren der Umfrage-Frage.  
- **📖 Handbuch** springt zu diesem Dokument.

---

*3️⃣ Gruppen-Chat: Befehle & Abläufe*  

3.1 `/settopic @user`  
– Reply, Mention oder Username → weist Themenverantwortung zu.

3.2 `/cleandeleteaccounts`  
– Löscht „Deleted Accounts“ via Ban+Unban.

3.3 `/mood`  
– Startet Stimmungs-Umfrage. Frage wird aus DB gelesen.  
– Admins können mit `/setmoodquestion <Frage>` setzen.

3.4 `/moodstats <message_id>` (optional)  
– Zeigt Gesamt-Auswertung für eine Umfrage.

---

*4️⃣ Support & Kontakt*  
Tritt unserer Support-Gruppe bei:  
https://t.me/GreenyGroupManagerSupport  
'''

async def send_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT, parse_mode='Markdown')

help_handler = CommandHandler('help', send_manual)

# 2) Handler-Funktion für /help
async def send_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Sende das Handbuch
    await update.message.reply_text(HELP_TEXT, parse_mode='Markdown')

# 3) Registrierung
help_handler = CommandHandler('help', send_manual)

# Für Menüintegration exportieren
__all__ = ['help_handler']
