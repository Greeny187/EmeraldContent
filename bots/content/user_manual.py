﻿from telegram import Update
from telegram.ext import ContextTypes, CommandHandler
from shared.translator import translate_hybrid

# Basis-Handbuch in deutscher Sprache
HELP_TEXT = '''
*GreenyGroupManager Bot â€“ Benutzerhandbuch*

*Inhaltsverzeichnis*
1ï¸âƒ£ Funktionen im Ãœberblick
2ï¸âƒ£ Bot-Chat: MenÃ¼ & UntermenÃ¼s
   2.1 HauptmenÃ¼
   2.2 Gruppen-MenÃ¼
   2.3 Detaillierte AblÃ¤ufe
3ï¸âƒ£ Gruppen-Chat: Befehle & AblÃ¤ufe
4ï¸âƒ£ Support & Kontakt

---

*1ï¸âƒ£ Funktionen im Ãœberblick*
â€¢ **BegrÃ¼ÃŸungsnachricht** setzen (Ã¼ber MenÃ¼ â†’ *BegrÃ¼ÃŸung*)
â€¢ **Regeln** festlegen (Ã¼ber MenÃ¼ â†’ *Regeln*)
â€¢ **Abschiedsnachricht** setzen (Ã¼ber MenÃ¼ â†’ *Farewell*)
â€¢ **Linkschutz**: Automatische Warnung und LÃ¶schung von Links nicht-Admins
â€¢ **Themenverantwortliche** verwalten: `/settopic @user`, `/removetopic`
â€¢ **Accounts-Bereinigung**: Entfernt gelÃ¶schte Accounts
â€¢ **RSS-Integration** mit Feed-Verwaltung
â€¢ **Mood-Meter** Umfragen und Auswertung
â€¢ **Live-Statistik** via `/stats` und tÃ¤gliche JobQueue-Jobs
â€¢ **Handbuch anzeigen**: `/help`

---

*2ï¸âƒ£ Bot-Chat: MenÃ¼ & UntermenÃ¼s*

*2.1 HauptmenÃ¼*
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚ /start â†’ Auswahl der Gruppe  â”‚
â”‚ /menu  â†’ Ã–ffnet Gruppen-MenÃ¼ â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜

*2.2 Gruppen-MenÃ¼* (nach `/menu` oder Auswahl)
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚ ðŸ”§ Gruppe [Chat-Titel] â€“ HauptmenÃ¼              â”‚
â”œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¤
â”‚ BegrÃ¼ÃŸung      â”‚ Regeln                         â”‚
â”‚ Farewell       â”‚ Linksperre                     â”‚
â”‚ RSS            â”‚ ðŸ—‘ Accounts lÃ¶schen             â”‚
â”‚ ðŸ“Š Statistik   â”‚ âœï¸ Mood-Frage Ã¤ndern           â”‚
â”‚ ðŸ“– Handbuch    â”‚ Dev-Dashboard                  â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜

*2.3 Detaillierte AblÃ¤ufe*
â€¢ **BegrÃ¼ÃŸung einstellen**
  â€“ Nutzer klickt auf â€žBegrÃ¼ÃŸungâ€œ im Gruppen-MenÃ¼.
  â€“ Bot fordert mit `ForceReply` einen Text an.
  â€“ Admin antwortet mit gewÃ¼nschtem BegrÃ¼ÃŸungstext.
  â€“ Bot speichert den Text in der Datenbank und sendet BestÃ¤tigung.

â€¢ **Regeln festlegen**
  â€“ Klick auf â€žRegelnâ€œ. Bot sendet `ForceReply`.
  â€“ Admin gibt Regeln als mehrzeiligen Text ein.
  â€“ Bot speichert und zeigt die Regeln bei jedem neuen Beitritt an.

â€¢ **Farewell konfigurieren**
  â€“ Ãœber â€žFarewellâ€œ wird analog zur BegrÃ¼ÃŸung eine Abschiedsnachricht gesetzt.
  â€“ Bot nutzt diese Nachricht, wenn ein Mitglied die Gruppe verlÃ¤sst.

â€¢ **Linkschutz aktivieren**
  â€“ Klick auf â€žLinksperreâ€œ. Bot fragt nach Warn-Nachricht.
  â€“ Admin definiert Warn-Text; Bot Ã¼berwacht alle Nachrichten.
  â€“ Bei Link-Postings von Nicht-Admins lÃ¶scht Bot Nachricht und warnt Nutzer.

â€¢ **RSS-Feeds verwalten**
  â€“ Klick auf â€žRSSâ€œ Ã¶ffnet RSS-UntermenÃ¼.
  â€“ **Thema wÃ¤hlen** (`/settopicrss`): Bot nutzt Thema fÃ¼r Feed-Kennzeichnung.
  â€“ **Feed hinzufÃ¼gen** (`/setrss <URL>`): Bot verifiziert URL und speichert.
  â€“ **Feeds anzeigen** (`/listrss`): Bot listet alle konfigurierten Feeds.
  â€“ **Feed entfernen** (`/stoprss`): Entfernt ausgewÃ¤hlten Feed aus der Liste.

â€¢ **Accounts-Bereinigung ausfÃ¼hren**
  â€“ Klick auf â€žAccounts lÃ¶schenâ€œ oder `/cleandeleteaccounts`.
  â€“ Bot bannt und unbannt alle gelÃ¶schten Accounts in der Gruppe.
  â€“ Nach Abschluss meldet Bot Anzahl entfernter Accounts.

â€¢ **Mood-Meter Umfragen**
  â€“ Klick auf â€žMood-Frage Ã¤ndernâ€œ startet `ForceReply`.
  â€“ Admin gibt neue Frage ein.
  â€“ `/mood` startet Umfrage mit Reactions ðŸ‘ðŸ‘ŽðŸ¤”, speichert Message-ID.
  â€“ `/moodstats <message_id>` zeigt aktuelle Auswertung als Text.

â€¢ **Statistik-Jobs**
  â€“ **Live-Statistik** mit `/stats` ruft direkte Gruppen-Insights ab.
  â€“ **TÃ¤gliche Statistik** (08:00 Uhr): Top-3-Poster, Zusammenfassung.
  â€“ **Telethon-Statistik** (02:00 Uhr): ausfÃ¼hrliche API-Statistiken.
  â€“ **Mitgliederbereinigung** (03:00 Uhr): entfernt inaktive Accounts.

â€¢ **Dev-Dashboard aufrufen**
  â€“ `/dashboard` sendet Entwicklermodus-Link und Statistiken.

---

*3ï¸âƒ£ Gruppen-Chat: Befehle & AblÃ¤ufe*
â€¢ `/settopic @user` â€“ Themenverantwortliche zuweisen
â€¢ `/removetopic` â€“ Entfernt Themenverantwortung
â€¢ `/cleandeleteaccounts` â€“ Accounts-Bereinigung ausfÃ¼hren
â€¢ `/mood` â€“ Mood-Umfrage starten
â€¢ `/setmoodquestion <Frage>` â€“ Stimmungfrage setzen
â€¢ `/moodstats <message_id>` â€“ Umfrage-Auswertung holen
â€¢ `/settopicrss` â€“ RSS-Thema definieren
â€¢ `/setrss <URL>` â€“ RSS-Feed hinzufÃ¼gen
â€¢ `/listrss` â€“ RSS-Feeds listen
â€¢ `/stoprss` â€“ Entfernt RSS-Feed
â€¢ `/stats [group=<id>] [range=<Nd|Nw>]` â€“ Live-Statistik
â€¢ `/statistik` â€“ Alias fÃ¼r `/stats`
â€¢ `/dashboard` â€“ Dev-Dashboard anzeigen
â€¢ `/help` â€“ Handbuch Ã¼bersetzen und anzeigen

---

*4ï¸âƒ£ Support & Kontakt*
â€¢ Website: https://greeny187.github.io/GreenyManagementBots/
â€¢ Support-Gruppe: https://t.me/GreenyGroupManagerSupport
â€¢ TON Wallet: `UQBopac1WFJGC_K48T8JqcbRoH3evUoUDwS2oItlS-SgpR8L`
â€¢ PayPal: greeny187@outlook.de
'''

async def send_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Sendet das Benutzerhandbuch in der Nutzersprache als Datei
    oder als kurze Nachricht mit Datei, abhÃ¤ngig vom Kontext.
    """
    user_lang = update.effective_user.language_code or 'de'
    translated = translate_hybrid(HELP_TEXT, target_lang=user_lang)
    
    # Kurze Einleitung senden
    intro_text = translate_hybrid("*GreenyGroupManager - Handbuch*\n\nHier ist das vollstÃ¤ndige Benutzerhandbuch als Datei:", 
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
    
    # Optional: TemporÃ¤re Datei lÃ¶schen
    import os
    os.remove(file_name)

help_handler = CommandHandler('help', send_manual)

__all__ = ['help_handler']


