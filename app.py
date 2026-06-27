"""
Rail Event Dashboard — Flask Web App
"""
import json
import logging
import os
import threading
from datetime import datetime
from flask import Flask, jsonify, render_template, request
from event_scanner import (
    CONFIG,
    build_email_html,
    build_pdf_summary_html,
    get_upcoming_events,
    html_to_pdf,
    init_db,
    run_scan,
    send_email,
)

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
init_db()

scan_status = {
    "running": False,
    "last_scan": None,
    "last_count": 0,
    "last_new": 0,
    "error": None,
}

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/events")
def api_events():
    try:
        events = get_upcoming_events()
        for ev in events:
            for field in ["stations", "stations_primary", "stations_secondary", "stations_peripheral"]:
                try:
                    ev[field] = json.loads(ev.get(field) or "[]")
                except Exception:
                    ev[field] = []
        return jsonify({"ok": True, "events": events, "count": len(events)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/scan", methods=["POST"])
def api_scan():
    if scan_status["running"]:
        return jsonify({"ok": False, "error": "סריקה כבר רצה — המתן לסיום"}), 409
    def do_scan():
        scan_status["running"] = True
        scan_status["error"] = None
        try:
            events, new_ids = run_scan()
            scan_status["last_scan"] = datetime.now().strftime("%d/%m/%Y %H:%M")
            scan_status["last_count"] = len(events)
            scan_status["last_new"] = len(new_ids)
        except Exception as e:
            scan_status["error"] = str(e)
            logging.error(f"Scan error: {e}")
        finally:
            scan_status["running"] = False
    threading.Thread(target=do_scan, daemon=True).start()
    return jsonify({"ok": True, "message": "סריקה החלה"})

@app.route("/api/scan/status")
def api_scan_status():
    return jsonify(scan_status)

@app.route("/api/send-email", methods=["POST"])
def api_send_email():
    data = request.get_json(silent=True) or {}
    to_email = data.get("to", "").strip()
    if not to_email or "@" not in to_email:
        return jsonify({"ok": False, "error": "כתובת מייל לא תקינה"}), 400
    events = get_upcoming_events()
    if not events:
        return jsonify({"ok": False, "error": "אין אירועים — הרץ סריקה תחילה"}), 400
    original = CONFIG["recipient_email"]
    CONFIG["recipient_email"] = to_email
    def do_send():
        try:
            html_body = build_email_html(events, [])
            send_email(html_body, False, events)
        except Exception as e:
            logging.error(f"Email error: {e}")
        finally:
            CONFIG["recipient_email"] = original
    threading.Thread(target=do_send, daemon=True).start()
    return jsonify({"ok": True, "message": f"דוח PDF נשלח אל {to_email}"})

@app.route("/api/events/<event_id>", methods=["DELETE"])
def api_delete_event(event_id):
    try:
        from event_scanner import get_db
        conn = get_db()
        conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
        conn.commit()
        conn.close()
        return jsonify({"ok": True, "deleted": event_id})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
