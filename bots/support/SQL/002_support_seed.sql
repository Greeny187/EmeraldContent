-- Einfache Kategorien über Tags
INSERT INTO kb_articles (title, body, tags) VALUES
('Willkommen beim Emerald Support',
'So erstellst du ein Ticket: Öffne die MiniApp → "Neue Anfrage" → Kategorie & Betreff wählen → Problem beschreiben → Senden.',
ARRAY['onboarding','tickets']
),
('Statistiken in /stats',
'Im MVP zeigen wir Anzahl Tickets (heute/7 Tage), mittlere Antwortzeit und Statusverteilung. In Pro bis 60 Tage.',
ARRAY['analytics','stats']
) ON CONFLICT DO NOTHING;