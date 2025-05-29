import sqlite3

# Verbindung erstellen
conn = sqlite3.connect("bot_data.db")
c = conn.cursor()

# Tabellen erstellen
c.execute('''
CREATE TABLE IF NOT EXISTS groups (
    group_id INTEGER PRIMARY KEY,
    welcome_message TEXT DEFAULT "Willkommen!"
)
''')

conn.commit()
conn.close()