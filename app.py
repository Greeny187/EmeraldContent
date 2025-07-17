import os
from datetime import datetime, date, time
from database import get_registered_groups, count_members, get_new_members_count, get_group_stats, get_mood_question, get_rss_topic, is_daily_stats_enabled
from statistic import get_active_users_count, get_command_usage, get_command_logs
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
# Erlaube deine GitHub-Pages-Domain (z.B. https://username.github.io)
CORS(app, resources={r"/api/*": {"origins": "https://greeny187.github.io/GreenyManagementBots/"}}, supports_credentials=True)

# Dashboard-Token aus Umgebungsvariable
DASHBOARD_TOKEN = os.getenv("DASHBOARD_TOKEN")

@app.before_request
def check_token():
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {DASHBOARD_TOKEN}":
        return jsonify({"error": "Unauthorized"}), 401


BOT_DATA = {
    "start_time": "2025-07-16T08:00:00",
    "groups": [
        {"id": 12345, "title": "Gruppe A", "members": 56},
        {"id": 67890, "title": "Gruppe B", "members": 142}
    ],
    "daily_messages": 1274,
    "logged_events": 945,
    "mood_stats": {
        "happy": 42,
        "neutral": 18,
        "sad": 6
    },
    "top_users": [
        {"user_id": 1001, "messages": 120},
        {"user_id": 1002, "messages": 95},
        {"user_id": 1003, "messages": 78}
    ]
}

@app.route("/api/dashboard")
def dashboard():
    start_dt = datetime.combine(date.today(), time.min)
    end_dt   = datetime.combine(date.today(), time.max)

    groups = []
    for chat_id, title in get_registered_groups():
        # Basis-Statistiken
        total_members = count_members(chat_id)
        new_today     = get_new_members_count(chat_id, date.today())
        active_users  = get_active_users_count(chat_id, start_dt, end_dt)
        top3          = get_group_stats(chat_id, date.today())
        mood_stats    = {}  # hier müsstest du message_id aus latest mood-prompt parsen
        # Einstellungen
        # Mit _with_cursor kannst du group_settings abfragen:
        # daily = is_daily_stats_enabled(chat_id)
        # mood_q= get_mood_question(chat_id)
        # rss   = get_rss_topic(chat_id)
        settings = {
          "daily_stats": is_daily_stats_enabled(chat_id),
          "mood_question": get_mood_question(chat_id),
          "rss_topic": get_rss_topic(chat_id)
        }

        # Befehls-Logs & Access History
        commands = [{"cmd": cmd, "count": cnt}
                    for cmd, cnt in get_command_usage(chat_id, start_dt, end_dt)]
        history = get_command_logs(chat_id, start_dt, end_dt)  # du kannst Funktion hinzufügen

        groups.append({
          "id": chat_id,
          "title": title,
          "total_members": total_members,
          "new_today": new_today,
          "active_users": active_users,
          "top_writers": [{"user_id": u, "msgs": m} for u, m in top3],
          "mood_stats": mood_stats,
          "settings": settings,
          "commands": commands,
          "access_history": history,
        })

    return jsonify({
      "start_time": BOT_DATA["start_time"],
      "uptime": str(datetime.now() - datetime.fromisoformat(BOT_DATA["start_time"])).split('.')[0],
      "total_groups": len(groups),
      "groups": groups,
    })

if __name__ == "__main__":
    app.run(debug=True)
