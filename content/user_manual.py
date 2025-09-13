from telegram import Update
from telegram.ext import ContextTypes, CommandHandler
from shared.translator import translate_hybrid

# Basis-Handbuch in deutscher Sprache
HELP_TEXT = '''
*GreenyGroupManager Bot – Benutzerhandbuch*

*Inhaltsverzeichnis*
1️⃣ Funktionen im Überblick
2️⃣ Bot-Chat: Menü & Untermenüs
   2.1 Hauptmenü
   2.2 Gruppen-Menü
   2.3 Detaillierte Abläufe
3️⃣ Gruppen-Chat: Befehle & Abläufe
4️⃣ Support & Kontakt

---

*1️⃣ Funktionen im Überblick*
• **Begrüßungsnachricht** setzen (über Menü → *Begrüßung*)
• **Regeln** festlegen (über Menü → *Regeln*)
• **Abschiedsnachricht** setzen (über Menü → *Farewell*)
• **Linkschutz**: Automatische Warnung und Löschung von Links nicht-Admins
• **Themenverantwortliche** verwalten: `/settopic @user`, `/removetopic`
• **Accounts-Bereinigung**: Entfernt gelöschte Accounts
• **RSS-Integration** mit Feed-Verwaltung
• **Mood-Meter** Umfragen und Auswertung
• **Live-Statistik** via `/stats` und tägliche JobQueue-Jobs
• **Handbuch anzeigen**: `/help`

---

*2️⃣ Bot-Chat: Menü & Untermenüs*

*2.1 Hauptmenü*
┌─────────────────────────────┐
│ /start → Auswahl der Gruppe  │
│ /menu  → Öffnet Gruppen-Menü │
└─────────────────────────────┘

*2.2 Gruppen-Menü* (nach `/menu` oder Auswahl)
┌─────────────────────────────────────────────────┐
│ 🔧 Gruppe [Chat-Titel] – Hauptmenü              │
├─────────────────────────────────────────────────┤
│ Begrüßung      │ Regeln                         │
│ Farewell       │ Linksperre                     │
│ RSS            │ 🗑 Accounts löschen             │
│ 📊 Statistik   │ ✍️ Mood-Frage ändern           │
│ 📖 Handbuch    │ Dev-Dashboard                  │
└─────────────────────────────────────────────────┘

*2.3 Detaillierte Abläufe*
• **Begrüßung einstellen**
  – Nutzer klickt auf „Begrüßung“ im Gruppen-Menü.
  – Bot fordert mit `ForceReply` einen Text an.
  – Admin antwortet mit gewünschtem Begrüßungstext.
  – Bot speichert den Text in der Datenbank und sendet Bestätigung.

• **Regeln festlegen**
  – Klick auf „Regeln“. Bot sendet `ForceReply`.
  – Admin gibt Regeln als mehrzeiligen Text ein.
  – Bot speichert und zeigt die Regeln bei jedem neuen Beitritt an.

• **Farewell konfigurieren**
  – Über „Farewell“ wird analog zur Begrüßung eine Abschiedsnachricht gesetzt.
  – Bot nutzt diese Nachricht, wenn ein Mitglied die Gruppe verlässt.

• **Linkschutz aktivieren**
  – Klick auf „Linksperre“. Bot fragt nach Warn-Nachricht.
  – Admin definiert Warn-Text; Bot überwacht alle Nachrichten.
  – Bei Link-Postings von Nicht-Admins löscht Bot Nachricht und warnt Nutzer.

• **RSS-Feeds verwalten**
  – Klick auf „RSS“ öffnet RSS-Untermenü.
  – **Thema wählen** (`/settopicrss`): Bot nutzt Thema für Feed-Kennzeichnung.
  – **Feed hinzufügen** (`/setrss <URL>`): Bot verifiziert URL und speichert.
  – **Feeds anzeigen** (`/listrss`): Bot listet alle konfigurierten Feeds.
  – **Feed entfernen** (`/stoprss`): Entfernt ausgewählten Feed aus der Liste.

• **Accounts-Bereinigung ausführen**
  – Klick auf „Accounts löschen“ oder `/cleandeleteaccounts`.
  – Bot bannt und unbannt alle gelöschten Accounts in der Gruppe.
  – Nach Abschluss meldet Bot Anzahl entfernter Accounts.

• **Mood-Meter Umfragen**
  – Klick auf „Mood-Frage ändern“ startet `ForceReply`.
  – Admin gibt neue Frage ein.
  – `/mood` startet Umfrage mit Reactions 👍👎🤔, speichert Message-ID.
  – `/moodstats <message_id>` zeigt aktuelle Auswertung als Text.

• **Statistik-Jobs**
  – **Live-Statistik** mit `/stats` ruft direkte Gruppen-Insights ab.
  – **Tägliche Statistik** (08:00 Uhr): Top-3-Poster, Zusammenfassung.
  – **Telethon-Statistik** (02:00 Uhr): ausführliche API-Statistiken.
  – **Mitgliederbereinigung** (03:00 Uhr): entfernt inaktive Accounts.

• **Dev-Dashboard aufrufen**
  – `/dashboard` sendet Entwicklermodus-Link und Statistiken.

---

*3️⃣ Gruppen-Chat: Befehle & Abläufe*
• `/settopic @user` – Themenverantwortliche zuweisen
• `/removetopic` – Entfernt Themenverantwortung
• `/cleandeleteaccounts` – Accounts-Bereinigung ausführen
• `/mood` – Mood-Umfrage starten
• `/setmoodquestion <Frage>` – Stimmungfrage setzen
• `/moodstats <message_id>` – Umfrage-Auswertung holen
• `/settopicrss` – RSS-Thema definieren
• `/setrss <URL>` – RSS-Feed hinzufügen
• `/listrss` – RSS-Feeds listen
• `/stoprss` – Entfernt RSS-Feed
• `/stats [group=<id>] [range=<Nd|Nw>]` – Live-Statistik
• `/statistik` – Alias für `/stats`
• `/dashboard` – Dev-Dashboard anzeigen
• `/help` – Handbuch übersetzen und anzeigen

---

*4️⃣ Support & Kontakt*
• Website: https://greeny187.github.io/GreenyManagementBots/
• Support-Gruppe: https://t.me/GreenyGroupManagerSupport
• TON Wallet: `UQBopac1WFJGC_K48T8JqcbRoH3evUoUDwS2oItlS-SgpR8L`
• PayPal: greeny187@outlook.de
'''

async def send_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Sendet das Benutzerhandbuch in der Nutzersprache als Datei
    oder als kurze Nachricht mit Datei, abhängig vom Kontext.
    """
    user_lang = update.effective_user.language_code or 'de'
    translated = translate_hybrid(HELP_TEXT, target_lang=user_lang)
    
    # Kurze Einleitung senden
    intro_text = translate_hybrid("*GreenyGroupManager - Handbuch*\n\nHier ist das vollständige Benutzerhandbuch als Datei:", 
                                 target_lang=user_lang)
    await update.message.reply_text(intro_text, parse_mode='Markdown')
    
    # Handbuch als Datei senden
    file_name = f"GreenyGroupManager_Manual_{user_lang}.txt"
    with open(file_name, 'w', encoding='utf-8') as f:
        f.write(translated)
    
    with open(file_name, 'rb') as f:
        await update.message.reply_document(
            document=f,
            filename=file_name,
            caption=translate_hybrid("Benutzerhandbuch", target_lang=user_lang)
        )
    
    # Optional: Temporäre Datei löschen
    import os
    os.remove(file_name)

help_handler = CommandHandler('help', send_manual)

__all__ = ['help_handler']
