import os
from flask import Flask, request, jsonify
from datetime import datetime

app = Flask(__name__)

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

@app.route("/api/dashboard", methods=["GET"])
def dashboard():
    now = datetime.now()
    start = datetime.fromisoformat(BOT_DATA["start_time"])
    uptime = now - start
    return jsonify({
        "start_time": BOT_DATA["start_time"],
        "uptime": str(uptime).split('.')[0],
        "groups": BOT_DATA["groups"],
        "total_groups": len(BOT_DATA["groups"]),
        "total_members": sum(g["members"] for g in BOT_DATA["groups"]),
        "daily_messages": BOT_DATA["daily_messages"],
        "logged_events": BOT_DATA["logged_events"],
        "mood_stats": BOT_DATA["mood_stats"],
        "top_users": BOT_DATA["top_users"]
    })

if __name__ == "__main__":
    app.run(debug=True)
