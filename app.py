import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from zoneinfo import ZoneInfo

PST = ZoneInfo("America/Los_Angeles")


def now_pst():
    return datetime.now(PST)
from flask import Flask, jsonify, render_template, request

import esxi_client

app = Flask(__name__)
DB_PATH = os.environ.get("DB_PATH", "/app/data/shutdown_manager.db")

# --- Database ---

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS schedule (
            day_of_week INTEGER PRIMARY KEY,
            hour INTEGER NOT NULL DEFAULT 5,
            minute INTEGER NOT NULL DEFAULT 0,
            enabled INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS skip (
            date TEXT PRIMARY KEY
        );
        CREATE TABLE IF NOT EXISTS log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            event TEXT NOT NULL,
            details TEXT
        );
    """)
    # Seed default schedule (5:00 AM every day)
    for dow in range(7):
        conn.execute(
            "INSERT OR IGNORE INTO schedule (day_of_week, hour, minute, enabled) VALUES (?, 5, 0, 1)",
            (dow,),
        )
    conn.commit()
    conn.close()


def add_log(event, details=None):
    conn = get_db()
    conn.execute(
        "INSERT INTO log (timestamp, event, details) VALUES (?, ?, ?)",
        (now_pst().isoformat(), event, details),
    )
    conn.commit()
    conn.close()

# --- Scheduler ---

scheduler = BackgroundScheduler()


def check_and_shutdown():
    now = now_pst()
    conn = get_db()

    # Check if today is skipped
    today_str = now.strftime("%Y-%m-%d")
    skip = conn.execute("SELECT date FROM skip WHERE date = ?", (today_str,)).fetchone()
    if skip:
        add_log("skip", f"Shutdown skipped for {today_str}")
        conn.execute("DELETE FROM skip WHERE date = ?", (today_str,))
        conn.commit()
        conn.close()
        return

    # Check schedule for current day
    dow = now.weekday()  # 0=Monday
    row = conn.execute(
        "SELECT hour, minute, enabled FROM schedule WHERE day_of_week = ?", (dow,)
    ).fetchone()
    conn.close()

    if not row or not row["enabled"]:
        return

    scheduled_hour = row["hour"]
    scheduled_minute = row["minute"]

    # Only trigger if we're within 1 minute of the scheduled time
    if now.hour == scheduled_hour and now.minute == scheduled_minute:
        add_log("shutdown", f"Scheduled shutdown triggered at {now.strftime('%H:%M')}")
        esxi_client.trigger_shutdown(log_fn=add_log)


# --- Routes ---

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/vms")
def api_vms():
    try:
        vms = esxi_client.get_vms()
        return jsonify({"ok": True, "vms": vms})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/schedule", methods=["GET"])
def api_get_schedule():
    conn = get_db()
    rows = conn.execute("SELECT * FROM schedule ORDER BY day_of_week").fetchall()
    conn.close()
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    schedule = []
    for row in rows:
        schedule.append({
            "day_of_week": row["day_of_week"],
            "day_name": days[row["day_of_week"]],
            "hour": row["hour"],
            "minute": row["minute"],
            "enabled": bool(row["enabled"]),
        })
    return jsonify({"ok": True, "schedule": schedule})


@app.route("/api/schedule", methods=["POST"])
def api_set_schedule():
    data = request.json
    conn = get_db()
    for entry in data.get("schedule", []):
        conn.execute(
            "UPDATE schedule SET hour = ?, minute = ?, enabled = ? WHERE day_of_week = ?",
            (entry["hour"], entry["minute"], int(entry["enabled"]), entry["day_of_week"]),
        )
    conn.commit()
    conn.close()
    add_log("schedule_update", json.dumps(data.get("schedule", [])))
    return jsonify({"ok": True})


@app.route("/api/shutdown", methods=["POST"])
def api_shutdown():
    add_log("manual_shutdown", "Manual shutdown triggered from UI")
    esxi_client.trigger_shutdown(log_fn=add_log)
    return jsonify({"ok": True, "message": "Shutdown initiated"})


@app.route("/api/skip", methods=["POST"])
def api_skip():
    date_str = request.json.get("date", now_pst().strftime("%Y-%m-%d"))
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO skip (date) VALUES (?)", (date_str,))
    conn.commit()
    conn.close()
    add_log("skip_scheduled", f"Skip scheduled for {date_str}")
    return jsonify({"ok": True, "date": date_str})


@app.route("/api/skip", methods=["DELETE"])
def api_unskip():
    date_str = request.json.get("date", now_pst().strftime("%Y-%m-%d"))
    conn = get_db()
    conn.execute("DELETE FROM skip WHERE date = ?", (date_str,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/skips")
def api_get_skips():
    conn = get_db()
    rows = conn.execute("SELECT date FROM skip ORDER BY date").fetchall()
    conn.close()
    return jsonify({"ok": True, "skips": [r["date"] for r in rows]})


@app.route("/api/logs")
def api_logs():
    limit = request.args.get("limit", 50, type=int)
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    logs = [dict(row) for row in rows]
    return jsonify({"ok": True, "logs": logs})


@app.route("/api/next")
def api_next_shutdown():
    conn = get_db()
    now = now_pst()
    for i in range(8):
        check_date = now + timedelta(days=i)
        dow = check_date.weekday()
        row = conn.execute(
            "SELECT hour, minute, enabled FROM schedule WHERE day_of_week = ?", (dow,)
        ).fetchone()
        if not row or not row["enabled"]:
            continue
        candidate = check_date.replace(
            hour=row["hour"], minute=row["minute"], second=0, microsecond=0
        )
        if candidate <= now:
            continue
        date_str = candidate.strftime("%Y-%m-%d")
        skip = conn.execute("SELECT date FROM skip WHERE date = ?", (date_str,)).fetchone()
        if skip:
            continue
        conn.close()
        return jsonify({
            "ok": True,
            "next": candidate.isoformat(),
            "day": candidate.strftime("%A"),
            "time": candidate.strftime("%H:%M"),
        })
    conn.close()
    return jsonify({"ok": True, "next": None})


@app.route("/api/test")
def api_test():
    result = esxi_client.test_connection()
    if result is True:
        return jsonify({"ok": True, "message": "Connected to ESXi"})
    return jsonify({"ok": False, "error": result}), 500


if __name__ == "__main__":
    init_db()
    scheduler.add_job(check_and_shutdown, "cron", minute="*", timezone=PST, id="shutdown_checker")
    scheduler.start()
    app.run(host="0.0.0.0", port=8080)
