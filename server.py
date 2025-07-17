import os
import threading
import database
from datetime import datetime, date, timedelta
from flask import Flask, jsonify, send_from_directory
from database import (
    get_registered_groups,
    count_members,
    get_new_members_count,
    list_active_members,
    get_rss_topic,
    get_mood_question,
)
from bot import start_time

# Flask-Setup: Statische Dateien liegen in /public
app = Flask(__name__, static_folder='public', static_url_path='')

@app.route('/')
def index():
    return send_from_directory('public', 'index.html')

@app.route('/login.html')
def login_page():
    return send_from_directory('public', 'login.html')

@app.route('/dashboard.html')
def dashboard_page():
    return send_from_directory('public', 'dashboard.html')

@app.route('/api/dashboard')
def api_dashboard():
    # Gruppen-Daten
    groups = get_registered_groups()
    group_list = [{'id': cid, 'title': title} for cid, title in groups]
    group_count = len(groups)

    # Gruppeneinstellungen
    group_settings = []
    for cid, title in groups:
        rss_topic = get_rss_topic(cid)
        mood_q = get_mood_question(cid)
        group_settings.append({
            'id': cid,
            'title': title,
            'daily_stats_enabled': True,
            'rss_topic_id': rss_topic,
            'mood_question': mood_q
        })

    # Nutzer-Analysen
    total_members = sum(count_members(cid) for cid, _ in groups)
    new_members_today = sum(get_new_members_count(cid, date.today()) for cid, _ in groups)
    active_members = sum(len(list_active_members(cid)) for cid, _ in groups)

    # Mood-Verteilung
    mood_dist = {}
    cur = database.conn.cursor()
    for cid, _ in groups:
        cur.execute(
            "SELECT mood, COUNT(*) FROM mood_meter WHERE chat_id = %s GROUP BY mood;",
            (cid,)
        )
        mood_dist[cid] = dict(cur.fetchall())

    # Top 3 Schreiber insgesamt heute
    cur = database.conn.cursor()
    cur.execute(
        "SELECT user_id, SUM(messages) FROM daily_stats WHERE stat_date = %s GROUP BY user_id ORDER BY SUM(messages) DESC LIMIT 3;",
        (date.today(),)
    )
    top_writers = [{'user_id': uid, 'messages': cnt} for uid, cnt in cur.fetchall()]
    cur.close()

    # Bot-Aktivität: Startzeit & Uptime
    uptime = datetime.now() - start_time

    # Zugriffshistorie (letzte 20 Einträge)
    cur.execute(
        "SELECT timestamp, user_id, chat_id, command FROM access_log ORDER BY timestamp DESC LIMIT 20;"
    )
    access_log = [
        { 'timestamp': ts.isoformat(), 'user_id': uid, 'chat_id': gid, 'command': cmd }
        for ts, uid, gid, cmd in cur.fetchall()
    ]

    # Command Usage
    cur.execute(
        "SELECT command, COUNT(*) FROM access_log GROUP BY command;"
    )
    command_usage = [
        { 'command': cmd, 'count': cnt }
        for cmd, cnt in cur.fetchall()
    ]
    cur.close()

    return jsonify({
        'groupCount': group_count,
        'groupList': group_list,
        'groupSettings': group_settings,
        'totalMembers': total_members,
        'newMembersToday': new_members_today,
        'activeMembers': active_members,
        'moodDistribution': mood_dist,
        'topWriters': top_writers,
        'startTime': start_time.isoformat(),
        'uptime': str(uptime),
        'commandUsage': command_usage,
        'accessLog': access_log
    })

# Telegram-Bot im Hintergrund starten

def run_bot():
    import bot
    bot.main()

if __name__ == '__main__':
    threading.Thread(target=run_bot, daemon=True).start()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
