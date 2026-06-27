"""
Rail Event Dashboard — Flask Web App
"""
import json
import logging
import os
import smtplib
import threading
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

from flask import Flask, jsonify, render_template, request
from event_scanner import (
    CONFIG,
    build_email_html,
    build_pdf_summary_html,
    get_upcoming_events,
    html_to_pdf,
    init_db,
    run_scan,
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
  try:
        do_send()
        return jsonify({"ok": True, "message": f"דוח PDF נשלח אל {to_email}"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

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

    def do_send():
        try:
            html_body = build_email_html(events, [])
            pdf_bytes = html_to_pdf(build_pdf_summary_html(events))

            msg = MIMEMultipart()
            msg["From"] = "אגף הביטחון — מסילת ישראל <b02f7b001@smtp-brevo.com>"
            msg["To"] = to_email
            msg["Subject"] = "דוח אירועים — אגף הביטחון מסילת ישראל"
            msg.attach(MIMEText(html_body, "html", "utf-8"))

            if pdf_bytes:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(pdf_bytes)
                encoders.encode_base64(part)
                part.add_header("Content-Disposition", 'attachment; filename="דוח_אירועים.pdf"')
                msg.attach(part)

            smtp_user = os.environ.get("BREVO_SMTP_USER", "")
            smtp_pass = os.environ.get("BREVO_SMTP_PASS", "")

            with smtplib.SMTP("smtp-relay.brevo.com", 587) as server:
                server.starttls()
                server.login(smtp_user, smtp_pass)
                server.sendmail(smtp_user, to_email, msg.as_string())
                logging.info(f"Brevo email sent to {to_email}")
        except Exception as e:
            logging.error(f"Email error: {e}")

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
