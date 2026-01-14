-- Emerald Support Bot - Seed Data
-- Default KB-Artikel und Kategorien

INSERT INTO kb_articles (title, body, tags) VALUES
('Willkommen beim Emerald Support',
'So erstellst du ein Ticket:
1. Öffne die MiniApp → "Neue Anfrage"
2. Wähle eine Kategorie (Technik, Zahlungen, Konto, etc.)
3. Gib einen prägnanten Betreff ein
4. Beschreibe dein Problem ausführlich
5. Klick "Ticket erstellen"

Deine Tickets findest du unter "Meine Tickets".',
ARRAY['onboarding','tickets','start']
) ON CONFLICT DO NOTHING;

INSERT INTO kb_articles (title, body, tags) VALUES
('Antwortzeiten & SLA',
'Unsere Support-Zeiten:
🟢 Normal: 24 Stunden
🟠 Hoch: 4 Stunden
🔴 Kritisch: 1 Stunde

Diese Zeiten gelten Montag–Freitag 9–18 Uhr CET.',
ARRAY['sla','response-time']
) ON CONFLICT DO NOTHING;

INSERT INTO kb_articles (title, body, tags) VALUES
('Ticket-Status verstehen',
'Mögliche Ticket-Status:
• Neu: Gerade erstellt, noch nicht gelesen
• In Bearbeitung: Ein Agent kümmert sich drum
• Warten: Wir warten auf deine Antwort
• Gelöst: Problem behoben, Ticket geschlossen
• Archiv: Alter Eintrag',
ARRAY['status','tickets']
) ON CONFLICT DO NOTHING;