from telegram import Update
from telegram.ext import ContextTypes, CommandHandler
from shared.translator import translate_hybrid

# Basis-Handbuch in deutscher Sprache
HELP_TEXT = '''
*Emerald Content Bot - Benutzerhandbuch*

*Inhaltsverzeichnis*
1. Funktionen im Überblick
2. Bot-Chat: Menü & Miniapp
3. Gruppen-Chat: Befehle & Abläufe
4. Erweiterte Features (Pro)
5. Support & Kontakt

---

*1. Funktionen im Überblick*

Basis-Features:
• Begrüßungsnachricht setzen (mit optionalem Foto)
• Regeln festlegen (mit optionalem Foto)
• Abschiedsnachricht setzen (mit optionalem Foto)
• Link- & Spam-Schutz pro Topic (inkl. Tageslimit pro Nutzer)
• Themenverantwortliche verwalten
• Gelöschte Accounts automatisch bereinigen (manuell & geplant)
• RSS-Feed Integration pro Topic
• FAQ-Datenbank mit Kurzantworten
• Mood-Meter Umfragen (Stimmung im Thread oder per Direktnachricht)
• Statistiken & tägliche Reports (Aktivität, Top-Antwortende)

Erweiterte Features:
• Nachtmodus mit Schreib-Sperre & Hard-Mode
• KI-Moderation (Text, Bilder, Links) mit Strike-System
• Topic-Router für automatische Nachrichtenweiterleitung
• FAQ-System mit KI-gestützten Fallback-Antworten (Pro)
• Captcha bei neuen Mitgliedern
• EMRD-Rewards System (Punkte & Claims)
• Pro-Abo-Konfiguration (Zahlungswege & Beschreibung)

---

*2. Bot-Chat: Menü & Miniapp*

Grundlegende Befehle im privaten Chat mit dem Bot:
/start   – Wähle eine Gruppe und registriere sie
/miniapp – Öffne das Einstellungs-Panel (Telegram Miniapp)
/help    – Dieses Handbuch anzeigen
/version – Zeige aktuelle Patchnotes

Die Miniapp ist in Tabs gegliedert. Inhaltlich lassen sie sich so gruppieren:

ADMIN-EINSTELLUNGEN:
• Begrüßung, Regeln, Farewell (mit Foto-Upload)
• Linkschutz & Spam-Filter (Level + Ausnahmen)
• Captcha für neue Mitglieder (Typ & Verhalten)

CONTENT-MANAGEMENT:
• RSS-Feeds hinzufügen/entfernen, Topic je Feed wählen
• Optionale KI-Analyse für RSS-Inhalte (je nach Setup)
• FAQ: Fragen & Antworten anlegen, bearbeiten, löschen
• Mood-Meter: Frage festlegen, Ziel-Topic bestimmen, Test-Post starten

MODERATION & SICHERHEIT:
• KI-Moderation (AI-Mod) konfigurieren:
  – Schwellenwerte für Toxizität, Hass, Sexuelles, Belästigung etc.
  – Aktionen: löschen, warnen, muten, bannen
  – Strike-Punkte und Eskalationslogik
• Nachtmodus:
  – Start-/Endzeit, Zeitzone
  – Schreib-Sperre (write_lock) & Hard-Mode
  – Option: nur Admins schreiben, Nicht-Admin-Nachrichten löschen
• Topic-Router (per /router-Befehl im Chat, siehe unten)
• Strike-Punkte-Auswertung (über AI-Mod-Logs & Commands)

STATISTIK & REWARDS:
• Report-Tab:
  – Täglicher Gruppen-Report (Topic, Uhrzeit)
  – „Jetzt senden“-Option für einen Sofortbericht
• Statistik-Tab:
  – Zeitraum wählen (Tage)
  – Überblick: Nachrichten, Aktivität, Top-Antwortende
• Rewards-Tab:
  – EMRD-Rewards global konfigurieren (Modus, Mindest-Claim)
  – Limits pro Nutzer/Chat (cap_user / cap_chat)
  – Grundlage für spätere Claims & On-Chain-Auszahlung

SONSTIGES & PRO:
• Clean Deleted Accounts:
  – Geplante Bereinigung (Uhrzeit, Wochentag, Demote-Option)
  – Einmalige Sofort-Aktion „Jetzt bereinigen“
• Handbuch & Patchnotes:
  – Schnellzugriff auf dieses Handbuch (/help)
  – Patchnotes über /version einsehbar
• Pro-Abo-Konfiguration:
  – Zahlungswege (z.B. TON-Wallet, NEAR-Adresse, PayPal-Link, Coinbase-Key, Stars)
  – Preise für 1/3/12 Monate
  – Beschreibungstext für dein Pro-Angebot
• Sprache & Grundeinstellungen:
  – Gruppensprache festlegen (für System-Texte & Übersetzungen)

---

*3. Gruppen-Chat: Befehle & Abläufe*

Mood & Umfragen:
• Mood-Meter wird über die Miniapp gesteuert (Tab „Mood“).
• Dort legst du Frage & Ziel-Topic fest und startest die Umfrage.
• Reaktionen werden automatisch gezählt und in der DB erfasst.

Verwaltung & Rollen:
• /settopic @user
  – Weist einem Nutzer die Verantwortung für das aktuelle Topic zu.
• /removetopic @user
  – Entfernt eine bestehende Themenverantwortung.
• /cleandeleteaccounts
  – Löscht alle „gelöschten Accounts“ aus der aktuellen Gruppe.
  – Ergänzt den geplanten Nachtjob aus dem Miniapp-Tab „Sonstiges“.
• /wallet <ton_adresse>
  – Speichert die TON-Wallet eines Nutzers für EMRD-Rewards.
  – Ohne Argument zeigt der Befehl die aktuell hinterlegte Adresse.

Topic-Limits & Kontingent:
• /topiclimit <topic_id> <anzahl>  (im privaten Chat)
• /topiclimit <anzahl>             (direkt im gewünschten Topic/Thread)
  – Setzt ein Tageslimit pro Nutzer im jeweiligen Topic.
  – 0 = Limit deaktiviert.
• /myquota
  – Im Topic ausführen: zeigt dein Restkontingent für heute an.

Statistik & Strikes:
• /mystrikes
  – Zeigt deine aktuellen Strike-Punkte in dieser Gruppe.
• /strikes
  – Zeigt die Top-Strike-Nutzer (Topliste) der Gruppe.
• Der Rest der Statistik (Verlauf, Top-Antwortende, Reports)
  – erfolgt über die Tabs „Report“ und „Statistik“ in der Miniapp.

FAQ & Regeln:
• /faq <stichwort>
  – Durchsucht die FAQ-Datenbank der Gruppe.
  – Bei passenden Einträgen wird automatisch geantwortet.
• /rules
  – Zeigt die in der Miniapp hinterlegten Gruppenregeln an.

Router & Spam:
• /router list
  – Zeigt alle Topic-Router-Regeln.
• /router add <topic_id> keywords=a,b
  – Leitet Nachrichten mit bestimmten Schlüsselwörtern in ein Topic um.
• /router add <topic_id> domains=x.com,y.com
  – Leitet Links mit bestimmten Domains in ein Topic um.
• /router del <rule_id>, /router toggle <rule_id> on|off
  – Löschen bzw. Aktivieren/Deaktivieren einer Regel.
• /spamlevel <off|light|medium|strict> [flags]
  – Setzt die Spam-Policy, inkl.:
    emoji=N, emoji_per_min=N, flood10s=N
    whitelist=dom1,dom2  blacklist=dom3,dom4

Nachtmodus & Softruhe:
• Nachtmodus-Zeiten und Verhalten werden in der Miniapp konfiguriert.
• /quietnow 30m oder /quietnow 2h
  – Aktiviert sofort eine temporäre Ruhephase („Softruhe“) bis zur angegebenen Dauer.
  – Nutzt die aktiven Nightmode-Einstellungen (inkl. Schreib-Sperre).

---

*4. Erweiterte Features (Pro)*

NACHTMODUS (Pro-optimiert):
• Zeitgesteuerte Schreib-Sperre für ruhigere Zeiten
• Konfigurierbare Start- & Endzeiten und Zeitzonen
• Optional: Nur Admins dürfen schreiben
• Hard-Mode: Chat vollständig gesperrt
• Softruhe per /quietnow, z.B. bei spontanen Eskalationen

KI-MODERATION:
• Automatische Filterung von Spam & schädlichen Inhalten
• Text-Moderation (Toxizität, Hass, Gewalt etc.)
• Bild-Moderation (NSFW, Gewalt, Waffen)
• Link-Risiko-Bewertung
• Strike-Punkte System mit automatischer Eskalation (Warnung, Mute, Ban)
• Tägliche Limits für KI-Aktionen & Rate-Limits gegen Missbrauch

TOPIC-ROUTER:
• Automatische Nachrichtenweiterleitung zu passenden Themen
• Schlüsselwort-basierte Regeln (z. B. „kaufen“, „verkaufen“)
• Domain-basierte Regeln (z. B. shop.com → „Angebote“-Topic)
• Optional: Originalnachricht löschen und Nutzer kurz informieren

FAQ & KI-ANTWORTEN:
• FAQ-Datenbank mit eigenen Snippets
• KI beantwortet unbekannte Fragen als Fallback (nur in Pro-Gruppen aktiv)
• Automatische Erkennung von Fragen im Chat („?“ oder FAQ-Trigger)
• Logging der Auto-Responses für spätere Optimierung

STATISTIK & REWARDS:
• Statistik-Tab:
  – Aktivitätsverlauf, Gruppentrends, Top-Antwortende
• Report-Tab:
  – Automatischer Tagesreport ins definierte Topic
• EMRD-Rewards:
  – Punkte für Antworten & hilfreiche Inhalte
  – Tages- & Chatlimits konfigurierbar
  – Zusammenfassung pending/claimed („Rewards Summary“)
  – Basis für spätere On-Chain-Auszahlungen

---

*5. Support & Kontakt*

Website: https://greeny187.github.io/GreenyManagementBots/
Support-Gruppe: https://t.me/+DkUfIvjyej8zNGVi
PayPal: greeny187@outlook.de
TON-Wallet: UQBopac1WFJGC_KOK48T8JqcbRoH3evUoUDwS2oItlS-SgpR8L

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
