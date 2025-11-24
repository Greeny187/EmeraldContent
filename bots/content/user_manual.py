from telegram import Update
from telegram.ext import ContextTypes, CommandHandler
from shared.translator import translate_hybrid

# Basis-Handbuch in deutscher Sprache
HELP_TEXT = '''
*Emerald Content Bot - Benutzerhandbuch*

*Inhaltsverzeichnis*
1. Funktionen im Überblick
2. Bot-Chat: Menü & Untermenüs
3. Gruppen-Chat: Befehle & Abläufe
4. Erweiterte Features (Pro)
5. Support & Kontakt

---

*1. Funktionen im Überblick*

Basis-Features:
• Begrüßungsnachricht setzen (mit optionalem Foto)
• Regeln festlegen (mit optionalem Foto)
• Abschiedsnachricht setzen (mit optionalem Foto)
• Linkschutz mit Domain-Whitelist/Blacklist
• Themenverantwortliche verwalten
• Gelöschte Accounts automatisch bereinigen
• RSS-Feed Integration
• Mood-Meter Umfragen
• Live-Statistik & tägliche Reports

Erweiterte Features:
• Nachtmodus mit Schreib-Sperre
• KI-Moderation (Text & Bilder)
• Topic-Router für automatische Nachrichtenweiterleitung
• FAQ-System mit KI-gestützten Antworten
• Captcha bei neuen Mitgliedern
• EMRD-Rewards System

---

*2. Bot-Chat: Menü & Untermenüs*

/start - Wähle eine Gruppe
/miniapp - Öffne das Einstellungs-Panel
/help - Dieses Handbuch anzeigen
/version - Zeige aktuelle Patchnotes

Im Miniapp-Panel findest du folgende Hauptkategorien:

ADMIN-EINSTELLUNGEN:
• Begrüßung, Regeln, Farewell (mit Foto-Upload)
• Linkschutz & Spam-Filter
• Captcha für neue Mitglieder

CONTENT-MANAGEMENT:
• RSS-Feeds (hinzufügen/entfernen)
• FAQ (Fragen & Antworten)
• Mood-Meter Konfiguration

MODERAÇÃO & SICHERHEIT:
• KI-Moderation Einstellungen (Text & Bilder)
• Nachtmodus mit Zeitplanung
• Topic-Router für automatische Nachrichtenweiterleitung
• Strike-Punkte Management

STATISTIK & REWARDS:
• Tägliche Statistik-Reports
• EMRD-Rewards konfigurieren
• Sofort-Statistiken abrufen

SONSTIGES:
• Handbuch (dieses PDF)
• Patchnotes anzeigen
• Pro-Abo kaufen/verlängern
• Sprache & Einstellungen

---

*3. Gruppen-Chat: Befehle & Abläufe*

Mood & Umfragen:
• /mood - Starte eine Mood-Umfrage
• /setmoodtopic <topic_id> - Setze Mood-Topic für Foren

Verwaltung:
• /settopic @user - Weise Nutzer als Themenverantwortliche zu
• /removetopic @user - Entferne Themenverantwortung
• /cleandeleteaccounts - Lösche alle gelöschten Accounts
• /wallet <ton_adresse> - Speichere deine TON-Wallet für Rewards

Statistik & Berichte:
• /stats - Zeige Live-Statistiken
• /myquota - Zeige dein Kontingent im aktuellen Topic
• /mystrikes - Zeige deine aktuellen Strike-Punkte
• /strikes - Zeige Top-10 Strike-Nutzer

FAQ & Support:
• /faq <stichwort> - Suche in FAQ-Datenbank
• /rules - Zeige Gruppenregeln an

Router & Spam:
• /router list - Zeige alle Router-Regeln
• /router add <topic_id> keywords=a,b - Erstelle Router-Regel
• /spamlevel <off|light|medium|strict> - Setze Spam-Level

---

*4. Erweiterte Features (Pro)*

NACHTMODUS:
Zeitgesteuerte Schreib-Sperre für ruhigere Zeiten
• Konfigurierbare Start- & Endzeiten
• Optional: Nur Admins dürfen schreiben
• Hard-Mode: Chat vollständig gesperrt

KI-MODERATION:
Automatische Filterung von Spam & schädlichen Inhalten
• Text-Moderation (Toxizität, Hass, Gewalt etc.)
• Bild-Moderation (NSFW, Waffen, Gore)
• Domain-Risiko-Bewertung
• Strike-Punkte System mit automatischer Eskalation

TOPIC-ROUTER:
Automatische Nachrichtenweiterleitung zu passenden Themen
• Schlüsselwort-basierte Regeln
• Domain-basierte Regeln
• Optional: Originalnachricht löschen

FAQ & KI-ANTWORTEN:
FAQ-Datenbank mit KI-gestützten Fallback-Antworten
• Manuelle FAQ-Einträge
• KI beantwortet unbekannte Fragen (Pro)
• Automatische Antwort auf Fragen im Chat

REWARDS SYSTEM:
Nutzer verdienen EMRD-Token durch Engagement
• Punkte für Antworten & hilfreiche Inhalte
• Tägliche & Gesamtlimits
• Ansammeln & Claiming

---

*5. Support & Kontakt*

Website: https://greeny187.github.io/EmeraldContent/
Support: https://t.me/EmeraldContentSupport
PayPal: emerald@mail.de
TON Wallet: UQBopac1WFJGC_K48T8JqcbRoH3evUoUDwS2oItlS-SgpR8L

Version: Siehe /version für aktuelle Patchnotes
'''

async def send_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Sendet das Benutzerhandbuch in der Nutzersprache
    """
    user_lang = update.effective_user.language_code or 'de'
    
    # Übersetze den Text in die Nutzersprache
    translated = translate_hybrid(HELP_TEXT, target_lang=user_lang)
    
    # Sende das Handbuch direkt als Nachricht
    await update.message.reply_text(
        translated,
        parse_mode='Markdown',
        disable_web_page_preview=True
    )

help_handler = CommandHandler('help', send_manual)

__all__ = ['help_handler']
