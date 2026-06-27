"""
Israel Railways Event Scanner Bot
==================================
Scans daily for events affecting train stations in Tel Aviv/Sharon/Haifa regions.
Sends a styled HTML email at 12:00 noon with event details.

Usage:
    python event_scanner.py             # Run scheduler (sends email at 12:00 daily)
    python event_scanner.py --now       # Scan and send immediately
    python event_scanner.py --test      # Send test email with dummy data
"""

import sqlite3
import smtplib
import schedule
import time
import json
import logging
import argparse
import re
from datetime import datetime, date, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path

import requests
from bs4 import BeautifulSoup

import os

CONFIG = {
    "smtp_host":       "smtp.gmail.com",
    "smtp_port":       587,
    "smtp_user":       os.environ.get("SMTP_USER",       "YOUR_EMAIL@gmail.com"),
    "smtp_password":   os.environ.get("SMTP_PASSWORD",   "YOUR_APP_PASSWORD"),
    "recipient_email": os.environ.get("RECIPIENT_EMAIL", "daniel@israelrailways.co.il"),
    "send_hour":       12,
    "days_ahead":      30,
    "req_timeout":     15,
}

STATION_IMPACT_MAP: dict[str, dict[str, list[str]]] = {
    "אצטדיון בלומפילד": {"primary": ["תל אביב – ההגנה"], "secondary": ["תל אביב – סבידור מרכז"], "peripheral": ["תל אביב – השלום"]},
    "בלומפילד": {"primary": ["תל אביב – ההגנה"], "secondary": ["תל אביב – סבידור מרכז"], "peripheral": ["תל אביב – השלום"]},
    "פארק הירקון": {"primary": ["תל אביב – האוניברסיטה – אקספו"], "secondary": ["תל אביב – סבידור מרכז"], "peripheral": ["תל אביב – השלום"]},
    "נמל תל אביב": {"primary": ["תל אביב – האוניברסיטה – אקספו"], "secondary": ["תל אביב – סבידור מרכז"], "peripheral": []},
    "כיכר רבין": {"primary": ["תל אביב – סבידור מרכז"], "secondary": ["תל אביב – השלום"], "peripheral": ["תל אביב – האוניברסיטה – אקספו"]},
    "שדרות רוטשילד": {"primary": ["תל אביב – השלום"], "secondary": ["תל אביב – סבידור מרכז"], "peripheral": []},
    "נווה צדק": {"primary": ["תל אביב – השלום"], "secondary": ["תל אביב – ההגנה"], "peripheral": []},
    "מרכז הכנסים": {"primary": ["תל אביב – סבידור מרכז"], "secondary": ["תל אביב – השלום"], "peripheral": []},
    "יס פלאנט תל אביב": {"primary": ["תל אביב – סבידור מרכז"], "secondary": [], "peripheral": ["תל אביב – השלום"]},
    "גני התערוכה": {"primary": ["תל אביב – האוניברסיטה – אקספו"], "secondary": ["תל אביב – סבידור מרכז"], "peripheral": []},
    "מכביה סיטי": {"primary": ["תל אביב – האוניברסיטה – אקספו"], "secondary": ["תל אביב – סבידור מרכז"], "peripheral": []},
    "מכביה": {"primary": ["תל אביב – האוניברסיטה – אקספו"], "secondary": ["תל אביב – סבידור מרכז"], "peripheral": ["תל אביב – השלום"]},
    "אקספו מכביה": {"primary": ["תל אביב – האוניברסיטה – אקספו"], "secondary": ["תל אביב – סבידור מרכז"], "peripheral": []},
    "אקספו": {"primary": ["תל אביב – האוניברסיטה – אקספו"], "secondary": ["תל אביב – סבידור מרכז"], "peripheral": []},
    "גני יהושע": {"primary": ["תל אביב – האוניברסיטה – אקספו"], "secondary": ["תל אביב – סבידור מרכז"], "peripheral": []},
    "אמפי תל אביב": {"primary": ["תל אביב – האוניברסיטה – אקספו"], "secondary": ["תל אביב – סבידור מרכז"], "peripheral": []},
    "פסטיבל יותר": {"primary": ["תל אביב – האוניברסיטה – אקספו"], "secondary": ["תל אביב – סבידור מרכז"], "peripheral": []},
    "היכל מנורה מבטחים": {"primary": ["תל אביב – השלום"], "secondary": ["תל אביב – סבידור מרכז"], "peripheral": ["תל אביב – ההגנה"]},
    "היכל מנורה": {"primary": ["תל אביב – השלום"], "secondary": ["תל אביב – סבידור מרכז"], "peripheral": ["תל אביב – ההגנה"]},
    "יד אליהו": {"primary": ["תל אביב – השלום"], "secondary": ["תל אביב – סבידור מרכז"], "peripheral": []},
    "ימי סטודנטים": {"primary": ["תל אביב – האוניברסיטה – אקספו"], "secondary": ["תל אביב – סבידור מרכז"], "peripheral": []},
    "אוניברסיטת תל אביב": {"primary": ["תל אביב – האוניברסיטה – אקספו"], "secondary": ["תל אביב – סבידור מרכז"], "peripheral": []},
    "תל אביב": {"primary": ["תל אביב – סבידור מרכז"], "secondary": ["תל אביב – השלום", "תל אביב – האוניברסיטה – אקספו"], "peripheral": ["תל אביב – ההגנה"]},
    "אצטדיון סמי עופר": {"primary": ["חיפה – חוף הכרמל"], "secondary": ["חיפה מרכז – השמונה"], "peripheral": ["חיפה – בת גלים"]},
    "יס פלאנט חיפה": {"primary": ["חיפה – חוף הכרמל"], "secondary": ["חיפה מרכז – השמונה"], "peripheral": []},
    "כיכר פריז": {"primary": ["חיפה מרכז – השמונה"], "secondary": ["חיפה – בת גלים"], "peripheral": ["קריית מוצקין"]},
    "מחסן 15": {"primary": ["חיפה מרכז – השמונה"], "secondary": ["חיפה – בת גלים"], "peripheral": []},
    "נמל חיפה": {"primary": ["חיפה מרכז – השמונה"], "secondary": ["חיפה – בת גלים"], "peripheral": []},
    "הפלגה": {"primary": ["חיפה מרכז – השמונה"], "secondary": ["חיפה – בת גלים"], "peripheral": []},
    "פארק הכרמל": {"primary": ["חיפה – חוף הכרמל"], "secondary": [], "peripheral": ["חיפה מרכז – השמונה"]},
    "מרכזית המפרץ": {"primary": ["מרכזית המפרץ"], "secondary": ["חוצות המפרץ"], "peripheral": ["קריית מוצקין"]},
    "חוצות המפרץ": {"primary": ["חוצות המפרץ"], "secondary": ["מרכזית המפרץ"], "peripheral": []},
    "חיפה": {"primary": ["חיפה מרכז – השמונה"], "secondary": ["חיפה – חוף הכרמל", "חיפה – בת גלים"], "peripheral": ["קריית מוצקין", "קריית חיים"]},
    "קריות": {"primary": ["קריית מוצקין"], "secondary": ["קריית חיים"], "peripheral": ["חוצות המפרץ"]},
    "קריית מוצקין": {"primary": ["קריית מוצקין"], "secondary": [], "peripheral": ["חוצות המפרץ"]},
    "קריית ביאליק": {"primary": ["קריית מוצקין"], "secondary": [], "peripheral": ["חוצות המפרץ"]},
    "קריית חיים": {"primary": ["קריית חיים"], "secondary": [], "peripheral": ["קריית מוצקין"]},
    "אצטדיון מרים": {"primary": ["נתניה"], "secondary": ["נתניה – ספיר"], "peripheral": []},
    "אצטדיון נתניה": {"primary": ["נתניה"], "secondary": ["נתניה – ספיר"], "peripheral": []},
    "נמל נתניה": {"primary": ["נתניה"], "secondary": [], "peripheral": []},
    "נתניה": {"primary": ["נתניה"], "secondary": ["נתניה – ספיר"], "peripheral": ["בית יהושע"]},
    "הרצליה": {"primary": ["הרצליה"], "secondary": [], "peripheral": []},
    "טיילת הרצליה": {"primary": ["הרצליה"], "secondary": [], "peripheral": []},
    "כפר סבא": {"primary": ["כפר סבא – נורדאו"], "secondary": ["הוד השרון – צמח השרון"], "peripheral": []},
    "הוד השרון": {"primary": ["הוד השרון – צמח השרון"], "secondary": ["כפר סבא – נורדאו"], "peripheral": []},
    "רעננה": {"primary": ["רעננה – דרום"], "secondary": ["רעננה – מערב"], "peripheral": []},
    "רמת גן": {"primary": ["סגולה"], "secondary": ["תל אביב – סבידור מרכז"], "peripheral": []},
    "אמפיתיאטרון פארק רמת גן": {"primary": ["סגולה"], "secondary": ["תל אביב – סבידור מרכז"], "peripheral": []},
    "עכו": {"primary": ["עכו"], "secondary": [], "peripheral": ["נהריה"]},
    "נהריה": {"primary": ["נהריה"], "secondary": [], "peripheral": ["עכו"]},
    "זכרון יעקב": {"primary": ["בנימינה"], "secondary": [], "peripheral": []},
    "בנימינה": {"primary": ["בנימינה"], "secondary": [], "peripheral": []},
    "חדרה": {"primary": ["חדרה – מערב"], "secondary": [], "peripheral": []},
}

REGION_MAP = {"תל אביב": "תל אביב - גוש דן", "שרון": "שרון", "חיפה": "חיפה וצפון"}

EVENT_TYPE_EMOJI = {
    "כדורגל": "⚽", "קונצרט": "🎵", "הופעה": "🎤", "פסטיבל": "🎪",
    "כנס": "🏛️", "אירוע עירייה": "🏙️", "תרבות": "🎭", "ספורט": "🏟️",
    "ימי סטודנטים": "🎓", "הפלגה": "⚓", "אחר": "📅",
}

CROWD_LEVEL_HEBREW = {"low": "נמוכה", "medium": "בינונית", "high": "גבוהה", "very_high": "גבוהה מאוד"}

DB_PATH = Path(__file__).parent / "events.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY, title TEXT, date TEXT, time TEXT,
            location TEXT, city TEXT, region TEXT, event_type TEXT,
            estimated_crowd INTEGER, crowd_level TEXT, crowd_type TEXT,
            stations TEXT, stations_primary TEXT, stations_secondary TEXT,
            stations_peripheral TEXT, source_url TEXT, notes TEXT,
            first_seen TEXT, last_updated TEXT
        )
    """)
    for col in ["stations_primary", "stations_secondary", "stations_peripheral"]:
        try:
            conn.execute(f"ALTER TABLE events ADD COLUMN {col} TEXT DEFAULT '[]'")
        except:
            pass
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sent_log (
            sent_date TEXT PRIMARY KEY, event_ids TEXT, new_event_ids TEXT
        )
    """)
    conn.commit()
    conn.close()

def get_db():
    return sqlite3.connect(DB_PATH)

def upsert_event(event: dict) -> bool:
    conn = get_db()
    existing = conn.execute("SELECT id FROM events WHERE id=?", (event["id"],)).fetchone()
    now = datetime.now().isoformat()
    event["last_updated"] = now
    if not existing:
        event["first_seen"] = now
        cols = ", ".join(event.keys())
        placeholders = ", ".join("?" * len(event))
        conn.execute(f"INSERT INTO events ({cols}) VALUES ({placeholders})", list(event.values()))
        conn.commit(); conn.close()
        return True
    else:
        conn.execute("""
            UPDATE events SET title=?, date=?, time=?, location=?, city=?, region=?,
            event_type=?, estimated_crowd=?, crowd_level=?, crowd_type=?, stations=?,
            stations_primary=?, stations_secondary=?, stations_peripheral=?,
            source_url=?, notes=?, last_updated=? WHERE id=?
        """, (event["title"], event["date"], event["time"], event["location"],
              event["city"], event["region"], event["event_type"],
              event["estimated_crowd"], event["crowd_level"], event["crowd_type"],
              event["stations"], event.get("stations_primary","[]"),
              event.get("stations_secondary","[]"), event.get("stations_peripheral","[]"),
              event["source_url"], event.get("notes",""), now, event["id"]))
        conn.commit(); conn.close()
        return False

def purge_past_events():
    conn = get_db()
    conn.execute("DELETE FROM events WHERE date < ?", (date.today().isoformat(),))
    conn.commit(); conn.close()

def get_upcoming_events() -> list[dict]:
    conn = get_db()
    cutoff = (date.today() + timedelta(days=CONFIG["days_ahead"])).isoformat()
    today = date.today().isoformat()
    rows = conn.execute("SELECT * FROM events WHERE date >= ? AND date <= ? ORDER BY date ASC, time ASC", (today, cutoff)).fetchall()
    conn.close()
    cols = ["id","title","date","time","location","city","region","event_type",
            "estimated_crowd","crowd_level","crowd_type","stations",
            "stations_primary","stations_secondary","stations_peripheral",
            "source_url","notes","first_seen","last_updated"]
    return [dict(zip(cols, r)) for r in rows]

def guess_station_impact(city: str, location: str) -> dict[str, list[str]]:
    combined = f"{location} {city}"
    for keyword in sorted(STATION_IMPACT_MAP.keys(), key=len, reverse=True):
        if keyword in combined:
            impact = STATION_IMPACT_MAP[keyword]
            return {"primary": list(impact.get("primary",[])), "secondary": list(impact.get("secondary",[])), "peripheral": list(impact.get("peripheral",[]))}
    return {"primary": ["לא מזוהה — יש לבדוק ידנית"], "secondary": [], "peripheral": []}

def guess_stations(city: str, location: str) -> list[str]:
    impact = guess_station_impact(city, location)
    return impact["primary"] + impact["secondary"] + impact["peripheral"] or ["לא מזוהה — יש לבדוק ידנית"]

VALIDATION_RULES = [
    ("title",           lambda v, _: bool(v and len(v.strip()) >= 3),       "כותרת חסרה או קצרה מדי"),
    ("date",            lambda v, _: _valid_date(v),                         "תאריך לא תקין או חלף"),
    ("city",            lambda v, _: bool(v and len(v.strip()) >= 2),        "עיר חסרה"),
    ("event_type",      lambda v, _: bool(v),                                "סוג אירוע חסר"),
    ("estimated_crowd", lambda v, _: isinstance(v, int) and v > 0,          "צפי קהל לא תקין"),
    ("stations",        lambda v, _: _valid_stations(v),                     "תחנות לא מוגדרות"),
    ("region",          lambda v, _: v in ["תל אביב - גוש דן","שרון","חיפה וצפון"], "גזרה לא בתחום הסריקה"),
]

def _valid_date(d: str) -> bool:
    try:
        ev = date.fromisoformat(d)
        return date.today() <= ev <= date.today() + timedelta(days=CONFIG["days_ahead"] + 1)
    except: return False

def _valid_stations(stations_json: str) -> bool:
    try:
        lst = json.loads(stations_json)
        return isinstance(lst, list) and len(lst) > 0 and lst[0] != "לא מזוהה — יש לבדוק ידנית"
    except: return False

def validate_event(ev: dict) -> tuple[bool, list[str]]:
    errors = []
    for field, check, msg in VALIDATION_RULES:
        val = ev.get(field)
        try:
            if not check(val, ev):
                errors.append(f"{msg} (שדה: {field}, ערך: {repr(val)[:60]})")
        except Exception as e:
            errors.append(f"שגיאת בדיקה בשדה {field}: {e}")
    return (len(errors) == 0), errors

def validate_and_filter(events: list[dict]) -> tuple[list[dict], list[dict]]:
    valid, rejected = [], []
    for ev in events:
        ok, errors = validate_event(ev)
        if ok:
            valid.append(ev)
        else:
            ev["_validation_errors"] = errors
            rejected.append(ev)
            logging.warning(f"[VALIDATION REJECTED] '{ev.get('title','?')[:50]}' ({ev.get('date','?')}) — {'; '.join(errors)}")
    if rejected:
        logging.warning(f"Validation summary: {len(valid)} valid, {len(rejected)} rejected out of {len(events)} total")
    return valid, rejected

def guess_region(city: str) -> str:
    for c in ["חיפה","קריות","עכו","נהריה","זכרון","כרמל","קריית","טבריה"]:
        if c in city: return "חיפה וצפון"
    for c in ["נתניה","הרצליה","כפר סבא","רעננה","רא\"ש","כפר יונה","טול"]:
        if c in city: return "שרון"
    return "תל אביב - גוש דן"

def estimate_crowd(event_type: str, title: str) -> tuple[int, str, str]:
    if event_type == "כדורגל":
        if any(w in title for w in ["אצטדיון מרים","מכבי נתניה","הפועל חדרה","מכבי הרצליה"]):
            return 10000, "high", "מעריצי כדורגל"
        if any(w in title for w in ["דרבי","גביע","ליגת העל","מחזור"]):
            return 15000, "very_high", "מעריצי כדורגל"
        return 8000, "high", "מעריצי כדורגל"
    if event_type in ["קונצרט","הופעה"]:
        if any(w in title for w in ["פסטיבל","רב ימי","הפארק"]):
            return 20000, "very_high", "קהל מגוון"
        return 5000, "high", "קהל מוזיקה"
    if event_type == "פסטיבל": return 10000, "high", "קהל מגוון"
    if event_type == "כנס": return 2000, "medium", "אנשי מקצוע"
    if event_type == "ימי סטודנטים": return 8000, "high", "סטודנטים (ממוצע גיל 20-30)"
    if event_type == "הפלגה": return 3000, "medium", "נוסעי קרוז ומשפחות מלווות"
    return 1000, "low", "קהל כללי"

def make_event_id(title: str, event_date: str) -> str:
    import hashlib
    return hashlib.md5(f"{title.strip()}{event_date}".encode("utf-8")).hexdigest()[:12]

def parse_hebrew_date(text: str) -> str | None:
    patterns = [r"(\d{1,2})[./\-](\d{1,2})[./\-](\d{4})", r"(\d{4})[./\-](\d{1,2})[./\-](\d{1,2})", r"(\d{1,2})\s+ב?([א-ת]+)\s+(\d{4})"]
    months_he = {"ינואר":1,"פברואר":2,"מרץ":3,"אפריל":4,"מאי":5,"יוני":6,"יולי":7,"אוגוסט":8,"ספטמבר":9,"אוקטובר":10,"נובמבר":11,"דצמבר":12}
    for p in patterns[:2]:
        m = re.search(p, text)
        if m:
            g = m.groups()
            try:
                if len(g[2]) == 4: return date(int(g[2]), int(g[1]), int(g[0])).isoformat()
                else: return date(int(g[0]), int(g[1]), int(g[2])).isoformat()
            except: pass
    m = re.search(patterns[2], text)
    if m:
        d_str, mo_str, y_str = m.groups()
        mo_num = months_he.get(mo_str)
        if mo_num:
            try: return date(int(y_str), mo_num, int(d_str)).isoformat()
            except: pass
    return None

def safe_get(url: str, **kwargs) -> requests.Response | None:
    headers = {"User-Agent": "Mozilla/5.0 (compatible; RailwaysBot/1.0)"}
    try:
        r = requests.get(url, headers=headers, timeout=CONFIG["req_timeout"], **kwargs)
        r.raise_for_status()
        return r
    except Exception as e:
        logging.warning(f"Failed to fetch {url}: {e}")
        return None

# ─────────────────────────────────────────────────────────────────────────────
# SCRAPERS
# ─────────────────────────────────────────────────────────────────────────────

def scrape_tel_aviv_municipality() -> list[dict]:
    events = []
    url = "https://www.tel-aviv.gov.il/Residents/Culture/Pages/Events.aspx"
    r = safe_get(url)
    if not r: return events
    soup = BeautifulSoup(r.text, "lxml")
    cards = soup.select(".event-item, .ms-rtestate-field, article.event, .event-card")
    for card in cards[:20]:
        title = card.get_text(" ", strip=True)[:120]
        if not title: continue
        d = parse_hebrew_date(card.get_text())
        if not d: continue
        crowd, level, crowd_type = estimate_crowd("אירוע עירייה", title)
        stations = guess_stations("תל אביב", title)
        ev = {"id": make_event_id(title, d), "title": title, "date": d, "time": "20:00",
              "location": "תל אביב", "city": "תל אביב", "region": "תל אביב - גוש דן",
              "event_type": "אירוע עירייה", "estimated_crowd": crowd, "crowd_level": level,
              "crowd_type": crowd_type, "stations": json.dumps(stations, ensure_ascii=False),
              "source_url": url, "notes": ""}
        events.append(ev)
    return events

def scrape_haifa_municipality() -> list[dict]:
    events = []
    url = "https://www.haifa.muni.il/culture-sport-recreation/culture/events/"
    r = safe_get(url)
    if not r: return events
    soup = BeautifulSoup(r.text, "lxml")
    for card in soup.select(".event, .tribe-event, article, .post")[:20]:
        text = card.get_text(" ", strip=True)
        title = text[:120]
        if not title or len(title) < 5: continue
        d = parse_hebrew_date(text)
        if not d: continue
        crowd, level, crowd_type = estimate_crowd("אירוע עירייה", title)
        ev = {"id": make_event_id(title, d), "title": title, "date": d, "time": "19:00",
              "location": "חיפה", "city": "חיפה", "region": "חיפה וצפון",
              "event_type": "אירוע עירייה", "estimated_crowd": crowd, "crowd_level": level,
              "crowd_type": crowd_type, "stations": json.dumps(guess_stations("חיפה", title), ensure_ascii=False),
              "source_url": url, "notes": ""}
        events.append(ev)
    return events

def scrape_netanya_municipality() -> list[dict]:
    events = []
    url = "https://www.netanya.muni.il/Residents/Pages/default.aspx"
    r = safe_get(url)
    if not r: return events
    soup = BeautifulSoup(r.text, "lxml")
    for card in soup.select(".event, article, .item")[:15]:
        text = card.get_text(" ", strip=True)
        title = text[:120]
        d = parse_hebrew_date(text)
        if not title or not d: continue
        crowd, level, crowd_type = estimate_crowd("אירוע עירייה", title)
        ev = {"id": make_event_id(title, d), "title": title, "date": d, "time": "19:00",
              "location": "נתניה", "city": "נתניה", "region": "שרון",
              "event_type": "אירוע עירייה", "estimated_crowd": crowd, "crowd_level": level,
              "crowd_type": crowd_type, "stations": json.dumps(guess_stations("נתניה", title), ensure_ascii=False),
              "source_url": url, "notes": ""}
        events.append(ev)
    return events

def scrape_football_israel() -> list[dict]:
    events = []
    for url in ["https://www.football.org.il/leagues/premier-league/fixtures/", "https://www.football.org.il/leagues/liga-leumit/fixtures/"]:
        r = safe_get(url)
        if not r: continue
        soup = BeautifulSoup(r.text, "lxml")
        for row in soup.select(".fixture, .game, .match-row, tr.match, .schedule-row")[:30]:
            text = row.get_text(" ", strip=True)
            d = parse_hebrew_date(text)
            if not d: continue
            try: ev_date = date.fromisoformat(d)
            except: continue
            if ev_date < date.today() or ev_date > date.today() + timedelta(days=CONFIG["days_ahead"]): continue
            teams = row.select(".team-name, .home, .away, .team")
            team_names = [t.get_text(strip=True) for t in teams if t.get_text(strip=True)]
            title = " נגד ".join(team_names[:2]) if len(team_names) >= 2 else text[:80]
            city = "תל אביב"
            for kw in ["נתניה","חיפה","קריית","עכו","נהריה","הרצליה","כפר סבא"]:
                if kw in text: city = kw; break
            region = guess_region(city)
            if any(c in city for c in ["ירושלים","באר שבע","אשקלון"]): continue
            time_match = re.search(r"(\d{1,2}:\d{2})", text)
            ev_time = time_match.group(1) if time_match else "19:30"
            crowd, level, crowd_type = estimate_crowd("כדורגל", title)
            ev = {"id": make_event_id(title, d), "title": f"⚽ {title}", "date": d, "time": ev_time,
                  "location": city, "city": city, "region": region, "event_type": "כדורגל",
                  "estimated_crowd": crowd, "crowd_level": level, "crowd_type": crowd_type,
                  "stations": json.dumps(guess_stations(city, text), ensure_ascii=False),
                  "source_url": url, "notes": "משחק ליגה ישראלית"}
            events.append(ev)
    return events

def scrape_leaan() -> list[dict]:
    events = []
    url = "https://www.leaan.co.il/events"
    r = safe_get(url)
    if not r: return events
    soup = BeautifulSoup(r.text, "lxml")
    for card in soup.select(".event-card, .event-item, article, .show-item")[:30]:
        text = card.get_text(" ", strip=True)
        title_el = card.select_one("h2,h3,h4,.title,.event-title")
        title = title_el.get_text(strip=True) if title_el else text[:80]
        d = parse_hebrew_date(text)
        if not title or not d: continue
        try: ev_date = date.fromisoformat(d)
        except: continue
        if ev_date < date.today() or ev_date > date.today() + timedelta(days=CONFIG["days_ahead"]): continue
        city = "תל אביב"
        for kw in ["חיפה","נתניה","הרצליה","כפר סבא","רעננה","קריית","עכו","נהריה"]:
            if kw in text: city = kw; break
        if any(c in city for c in ["ירושלים","באר שבע","אילת","אשדוד","אשקלון"]): continue
        ev_type = "פסטיבל" if "פסטיבל" in title else ("הופעה" if "הופעה" in title else "קונצרט")
        time_match = re.search(r"(\d{1,2}:\d{2})", text)
        ev_time = time_match.group(1) if time_match else "21:00"
        crowd, level, crowd_type = estimate_crowd(ev_type, title)
        link = card.select_one("a")
        src_url = link["href"] if link and link.get("href","").startswith("http") else url
        ev = {"id": make_event_id(title, d), "title": title, "date": d, "time": ev_time,
              "location": city, "city": city, "region": guess_region(city), "event_type": ev_type,
              "estimated_crowd": crowd, "crowd_level": level, "crowd_type": crowd_type,
              "stations": json.dumps(guess_stations(city, text), ensure_ascii=False),
              "source_url": src_url, "notes": ""}
        events.append(ev)
    return events

def scrape_timeout_il() -> list[dict]:
    events = []
    url = "https://www.timeout.co.il/tel-aviv/events"
    r = safe_get(url)
    if not r: return events
    soup = BeautifulSoup(r.text, "lxml")
    for card in soup.select("article, .event-card, [class*='card'], .listing")[:25]:
        text = card.get_text(" ", strip=True)
        title_el = card.select_one("h2,h3,[class*='title']")
        title = title_el.get_text(strip=True) if title_el else text[:80]
        d = parse_hebrew_date(text)
        if not title or not d or len(title) < 4: continue
        try: ev_date = date.fromisoformat(d)
        except: continue
        if ev_date < date.today() or ev_date > date.today() + timedelta(days=CONFIG["days_ahead"]): continue
        city = "תל אביב"
        for kw in ["חיפה","נתניה","הרצליה","רמת גן"]:
            if kw in text: city = kw; break
        ev_type = "תרבות"
        for t in ["קונצרט","הופעה","פסטיבל","מוזיקה","כנס","ספורט"]:
            if t in title or t in text: ev_type = t; break
        time_match = re.search(r"(\d{1,2}:\d{2})", text)
        ev_time = time_match.group(1) if time_match else "20:00"
        crowd, level, crowd_type = estimate_crowd(ev_type, title)
        link = card.select_one("a")
        src_url = link["href"] if link and link.get("href","").startswith("http") else url
        ev = {"id": make_event_id(title, d), "title": title, "date": d, "time": ev_time,
              "location": city, "city": city, "region": guess_region(city), "event_type": ev_type,
              "estimated_crowd": crowd, "crowd_level": level, "crowd_type": crowd_type,
              "stations": json.dumps(guess_stations(city, text), ensure_ascii=False),
              "source_url": src_url, "notes": ""}
        events.append(ev)
    return events

def scrape_eventim_il() -> list[dict]:
    events = []
    for url in ["https://www.eventim.co.il/category/concerts", "https://www.eventim.co.il/category/sports"]:
        r = safe_get(url)
        if not r: continue
        soup = BeautifulSoup(r.text, "lxml")
        for card in soup.select(".event-list-item, article, .event-card, .show-card")[:20]:
            text = card.get_text(" ", strip=True)
            title_el = card.select_one("h2,h3,.title,.event-name")
            title = title_el.get_text(strip=True) if title_el else text[:80]
            d = parse_hebrew_date(text)
            if not title or not d: continue
            try: ev_date = date.fromisoformat(d)
            except: continue
            if ev_date < date.today() or ev_date > date.today() + timedelta(days=CONFIG["days_ahead"]): continue
            city = "תל אביב"
            for kw in ["חיפה","נתניה","הרצליה","כפר סבא","רעננה","קריית","עכו"]:
                if kw in text: city = kw; break
            if any(c in text for c in ["ירושלים","באר שבע","אילת"]): continue
            ev_type = "קונצרט" if "concerts" in url else "ספורט"
            if "כדורגל" in text or "ליגה" in text: ev_type = "כדורגל"
            elif "פסטיבל" in title: ev_type = "פסטיבל"
            time_match = re.search(r"(\d{1,2}:\d{2})", text)
            ev_time = time_match.group(1) if time_match else "20:00"
            crowd, level, crowd_type = estimate_crowd(ev_type, title)
            link = card.select_one("a")
            src_url = link["href"] if link and link.get("href","").startswith("http") else url
            ev = {"id": make_event_id(title, d), "title": title, "date": d, "time": ev_time,
                  "location": city, "city": city, "region": guess_region(city), "event_type": ev_type,
                  "estimated_crowd": crowd, "crowd_level": level, "crowd_type": crowd_type,
                  "stations": json.dumps(guess_stations(city, text), ensure_ascii=False),
                  "source_url": src_url, "notes": ""}
            events.append(ev)
    return events

def scrape_haifa_port_cruises() -> list[dict]:
    events = []
    api_url = "https://www.haifaport.co.il/wp-json/wp/v2/cruise_schedule?per_page=50&lang=he"
    r = safe_get(api_url)
    if r and r.status_code == 200:
        try:
            for item in r.json():
                title_raw = item.get("title", {}).get("rendered", "") or ""
                title = BeautifulSoup(title_raw, "lxml").get_text(strip=True)
                content = BeautifulSoup(item.get("content", {}).get("rendered", ""), "lxml").get_text(" ", strip=True)
                d = parse_hebrew_date(content) or parse_hebrew_date(title)
                if not d or not title: continue
                try: ev_date = date.fromisoformat(d)
                except: continue
                if ev_date < date.today() or ev_date > date.today() + timedelta(days=CONFIG["days_ahead"]): continue
                time_match = re.search(r"(\d{1,2}:\d{2})", content)
                ev_time = time_match.group(1) if time_match else "08:00"
                is_arrival = any(w in content for w in ["הגעה","עגינה","עוגנת","arrives","arrival"])
                direction = "הגעת אונייה לנמל" if is_arrival else "יציאת הפלגה מנמל"
                ev = {"id": make_event_id(f"הפלגה-{title}", d), "title": f"⚓ {direction} — {title}",
                      "date": d, "time": ev_time, "location": "נמל חיפה — מסוף נוסעים",
                      "city": "חיפה", "region": "חיפה וצפון", "event_type": "הפלגה",
                      "estimated_crowd": 3000, "crowd_level": "medium",
                      "crowd_type": "נוסעי קרוז ומשפחות מלווות",
                      "stations": json.dumps(["חיפה מרכז – השמונה"], ensure_ascii=False),
                      "source_url": "https://www.haifaport.co.il/cruise-schedule/",
                      "notes": f"{direction} — תחנת חיפה מרכז – השמונה סמוכה לאולם הנוסעים"}
                events.append(ev)
        except Exception as e:
            logging.warning(f"Haifa port JSON parse error: {e}")
    if not events:
        r2 = safe_get("https://www.haifaport.co.il/cruise-schedule/")
        if r2:
            soup = BeautifulSoup(r2.text, "lxml")
            for row in soup.select("tr, .cruise-row, .schedule-row, td")[:40]:
                text = row.get_text(" ", strip=True)
                d = parse_hebrew_date(text)
                if not d or len(text) < 5: continue
                try: ev_date = date.fromisoformat(d)
                except: continue
                if ev_date < date.today() or ev_date > date.today() + timedelta(days=CONFIG["days_ahead"]): continue
                title = text[:80]
                time_match = re.search(r"(\d{1,2}:\d{2})", text)
                ev_time = time_match.group(1) if time_match else "08:00"
                ev = {"id": make_event_id(f"הפלגה-{title}", d), "title": f"⚓ הפלגה — {title}",
                      "date": d, "time": ev_time, "location": "נמל חיפה — מסוף נוסעים",
                      "city": "חיפה", "region": "חיפה וצפון", "event_type": "הפלגה",
                      "estimated_crowd": 3000, "crowd_level": "medium",
                      "crowd_type": "נוסעי קרוז ומשפחות מלווות",
                      "stations": json.dumps(["חיפה מרכז – השמונה"], ensure_ascii=False),
                      "source_url": "https://www.haifaport.co.il/cruise-schedule/",
                      "notes": "תחנת חיפה מרכז – השמונה סמוכה לאולם הנוסעים"}
                events.append(ev)
    logging.info(f"  Haifa Port Cruises: {len(events)} events")
    return events

def scrape_tau_events() -> list[dict]:
    events = []
    for url in ["https://www.tau.ac.il/events", "https://www.tau.ac.il/student-union"]:
        r = safe_get(url)
        if not r: continue
        soup = BeautifulSoup(r.text, "lxml")
        for card in soup.select("article, .event, .item, .card, li.event-item")[:20]:
            text = card.get_text(" ", strip=True)
            title_el = card.select_one("h2,h3,h4,.title,.event-title")
            title = title_el.get_text(strip=True) if title_el else text[:80]
            d = parse_hebrew_date(text)
            if not title or not d or len(title) < 4: continue
            try: ev_date = date.fromisoformat(d)
            except: continue
            if ev_date < date.today() or ev_date > date.today() + timedelta(days=CONFIG["days_ahead"]): continue
            ev_type = "ימי סטודנטים" if any(w in title for w in ["סטודנט","מסיבה","פסטיבל","בידור","ימי","שבוע"]) else ("הופעה" if "הופעה" in title or "קונצרט" in title else "כנס")
            time_match = re.search(r"(\d{1,2}:\d{2})", text)
            ev_time = time_match.group(1) if time_match else "12:00"
            crowd = (8000, "high", "סטודנטים (ממוצע גיל 20-30)") if ev_type == "ימי סטודנטים" else estimate_crowd(ev_type, title)
            ev = {"id": make_event_id(title, d), "title": title, "date": d, "time": ev_time,
                  "location": "אוניברסיטת תל אביב", "city": "תל אביב", "region": "תל אביב - גוש דן",
                  "event_type": ev_type, "estimated_crowd": crowd[0], "crowd_level": crowd[1],
                  "crowd_type": crowd[2],
                  "stations": json.dumps(["תל אביב – האוניברסיטה – אקספו"], ensure_ascii=False),
                  "source_url": url, "notes": "קמפוס צמוד לתחנת הרכבת — צפוי עומס ישיר"}
            events.append(ev)
    return events

def scrape_bloomfield() -> list[dict]:
    events = []
    url = "https://www.sportpalace.co.il/bloomfield/%D7%9C%D7%95%D7%97-%D7%90%D7%A8%D7%95%D7%A2%D7%99%D7%9D/"
    r = safe_get(url)
    if not r: return events
    soup = BeautifulSoup(r.text, "lxml")
    for card in soup.select(".event, article, .tribe-event, .event-item, .fc-event")[:30]:
        text = card.get_text(" ", strip=True)
        title_el = card.select_one("h2,h3,h4,.title,.event-title,.tribe-event-name")
        title = title_el.get_text(strip=True) if title_el else text[:80]
        d = parse_hebrew_date(text)
        if not title or not d or len(title) < 3: continue
        try: ev_date = date.fromisoformat(d)
        except: continue
        if ev_date < date.today() or ev_date > date.today() + timedelta(days=CONFIG["days_ahead"]): continue
        ev_type = "הופעה" if any(w in title for w in ["הופעה","קונצרט","פסטיבל","מוזיקה"]) else "כדורגל"
        time_match = re.search(r"(\d{1,2}:\d{2})", text)
        ev_time = time_match.group(1) if time_match else "20:00"
        crowd, level, crowd_type = estimate_crowd(ev_type, title)
        ev = {"id": make_event_id(title, d), "title": title, "date": d, "time": ev_time,
              "location": "אצטדיון בלומפילד, תל אביב", "city": "תל אביב", "region": "תל אביב - גוש דן",
              "event_type": ev_type, "estimated_crowd": crowd, "crowd_level": level, "crowd_type": crowd_type,
              "stations": json.dumps(["תל אביב – ההגנה"], ensure_ascii=False),
              "source_url": url, "notes": ""}
        events.append(ev)
    return events

def scrape_sami_ofer() -> list[dict]:
    events = []
    url = "https://stadium.mhaifafc.com/gamesList"
    r = safe_get(url)
    if not r: return events
    soup = BeautifulSoup(r.text, "lxml")
    for card in soup.select(".game, .match, .event, article, .game-row, li.game")[:20]:
        text = card.get_text(" ", strip=True)
        d = parse_hebrew_date(text)
        if not d: continue
        try: ev_date = date.fromisoformat(d)
        except: continue
        if ev_date < date.today() or ev_date > date.today() + timedelta(days=CONFIG["days_ahead"]): continue
        title_el = card.select_one("h2,h3,.title,.teams,.match-title")
        title = title_el.get_text(strip=True) if title_el else text[:80]
        time_match = re.search(r"(\d{1,2}:\d{2})", text)
        ev_time = time_match.group(1) if time_match else "19:30"
        crowd, level, crowd_type = estimate_crowd("כדורגל", title)
        ev = {"id": make_event_id(title, d), "title": f"⚽ {title}", "date": d, "time": ev_time,
              "location": "אצטדיון סמי עופר, חיפה", "city": "חיפה", "region": "חיפה וצפון",
              "event_type": "כדורגל", "estimated_crowd": crowd, "crowd_level": level, "crowd_type": crowd_type,
              "stations": json.dumps(["חיפה – חוף הכרמל"], ensure_ascii=False),
              "source_url": url, "notes": "קיבולת 30,950 — האצטדיון הגדול בישראל"}
        events.append(ev)
    return events

def scrape_ramat_gan_park() -> list[dict]:
    events = []
    url = "https://live.tickchak.co.il/ramat-gan-national-park-amphitheater"
    r = safe_get(url)
    if not r: return events
    soup = BeautifulSoup(r.text, "lxml")
    for card in soup.select(".event, article, .show, .event-card, .event-item")[:20]:
        text = card.get_text(" ", strip=True)
        title_el = card.select_one("h2,h3,h4,.title,.event-name")
        title = title_el.get_text(strip=True) if title_el else text[:80]
        d = parse_hebrew_date(text)
        if not title or not d or len(title) < 3: continue
        try: ev_date = date.fromisoformat(d)
        except: continue
        if ev_date < date.today() or ev_date > date.today() + timedelta(days=CONFIG["days_ahead"]): continue
        ev_type = "פסטיבל" if "פסטיבל" in title else "הופעה"
        time_match = re.search(r"(\d{1,2}:\d{2})", text)
        ev_time = time_match.group(1) if time_match else "20:00"
        crowd, level, crowd_type = estimate_crowd(ev_type, title)
        ev = {"id": make_event_id(title, d), "title": title, "date": d, "time": ev_time,
              "location": "אמפיתיאטרון פארק רמת גן", "city": "רמת גן", "region": "תל אביב - גוש דן",
              "event_type": ev_type, "estimated_crowd": crowd, "crowd_level": level, "crowd_type": crowd_type,
              "stations": json.dumps(["סגולה"], ensure_ascii=False),
              "source_url": url, "notes": "תחנת סגולה — כ-15 דקות הליכה"}
        events.append(ev)
    return events

# ─────────────────────────────────────────────────────────────────────────────
# SCRAPER חדש — גני יהושע + פסטיבל יותר
# ─────────────────────────────────────────────────────────────────────────────
def scrape_ganei_yehoshua() -> list[dict]:
    """
    Scrapes Ganei Yehoshua official events page (park.co.il).
    כולל פסטיבל יותר — מסיבת הגיוס הגדולה לצה"ל, מתקיימת בגני יהושע.
    פסטיבל יותר: קהל של ~15,000 מתגייסים, גיל 18, כניסה חופשית.
    תחנה ראשית: תל אביב – האוניברסיטה – אקספו.
    """
    events = []
    urls = [
        "https://park.co.il/אירועים/",
        "https://park.co.il/events/",
        "https://amphitlv.co.il/",  # אמפי תל אביב — גני יהושע
    ]
    for url in urls:
        r = safe_get(url)
        if not r:
            continue
        soup = BeautifulSoup(r.text, "lxml")
        cards = soup.select(
            "article, .tribe-event, .type-tribe_events, "
            "li.tribe-event, .event, .event-item, .event-card, "
            ".show-item, .fc-event"
        )
        for card in cards[:40]:
            text = card.get_text(" ", strip=True)
            title_el = card.select_one("h2,h3,h4,.tribe-event-name,.title,.event-title,.event-name")
            title = title_el.get_text(strip=True) if title_el else text[:80]
            if not title or len(title) < 3:
                continue
            d = parse_hebrew_date(text)
            if not d:
                continue
            try:
                ev_date = date.fromisoformat(d)
            except:
                continue
            if ev_date < date.today() or ev_date > date.today() + timedelta(days=CONFIG["days_ahead"]):
                continue

            # זיהוי סוג אירוע
            ev_type = "פסטיבל"
            if any(w in title for w in ["הופעה","קונצרט","מוזיקה"]):
                ev_type = "הופעה"
            elif any(w in title for w in ["כדורגל","ספורט","ריצה","מרוץ"]):
                ev_type = "ספורט"
            elif any(w in title for w in ["כנס","קונגרס"]):
                ev_type = "כנס"

            # פסטיבל יותר — מסיבת גיוס ענקית
            if "יותר" in title:
                ev_type = "פסטיבל"
                crowd, level, crowd_type = 15000, "very_high", "מתגייסים לצה\"ל (גיל 18) — כניסה חופשית"
                notes = "פסטיבל יותר — מסיבת הגיוס הגדולה בישראל. עומס גבוה מאוד צפוי בתחנת האוניברסיטה – אקספו"
            else:
                crowd, level, crowd_type = estimate_crowd(ev_type, title)
                notes = "גני יהושע — תחנת האוניברסיטה – אקספו היא הקרובה ביותר"

            time_match = re.search(r"(\d{1,2}:\d{2})", text)
            ev_time = time_match.group(1) if time_match else "16:00"
            link = card.select_one("a")
            src_url = link["href"] if link and link.get("href","").startswith("http") else url

            ev = {
                "id": make_event_id(title, d),
                "title": title,
                "date": d,
                "time": ev_time,
                "location": "גני יהושע, אמפי תל אביב, פארק הירקון",
                "city": "תל אביב",
                "region": "תל אביב - גוש דן",
                "event_type": ev_type,
                "estimated_crowd": crowd,
                "crowd_level": level,
                "crowd_type": crowd_type,
                "stations": json.dumps(["תל אביב – האוניברסיטה – אקספו"], ensure_ascii=False),
                "source_url": src_url,
                "notes": notes,
            }
            events.append(ev)

    logging.info(f"  Ganei Yehoshua: {len(events)} events")
    return events

# ─────────────────────────────────────────────────────────────────────────────
# SCRAPER — לילה לבן תל אביב (מתקיים מדי שנה בסוף יוני)
# ─────────────────────────────────────────────────────────────────────────────
def scrape_white_night_tel_aviv() -> list[dict]:
    """
    סורק לילה לבן מ-4 מקורות:
    1. דף ייעודי באתר עיריית תל אביב
    2. TimeOut ישראל — דף לילה לבן
    3. אתר העירייה — דף אירועים כללי עם מילת חיפוש
    4. muzi.co.il — אירועי לילה לבן
    לילה לבן מתקיים כל שנה ב-25 ביוני (בערך) — אירוע תרבות עירוני ענק.
    """
    events = []
    urls = [
        # אתר רשמי עיריית ת"א — דף לילה לבן
        "https://www.tel-aviv.gov.il/Pages/MainItemPage.aspx?WebID=3af57d92-807c-43c5-8d5f-6fd455eb2776&ListID=9dd2da03-5c43-462a-b5b2-d087c179b16c&ItemID=10357",
        # דף אירועים עיריית ת"א
        "https://www.tel-aviv.gov.il/Residents/Culture/Pages/Events.aspx",
        # TimeOut — לילה לבן
        "https://timeout.co.il/לילה-לבן-2026/",
        "https://www.timeout.co.il/tel-aviv/events/white-night",
        # muzi
        "https://muzi.co.il/events/?q=לילה+לבן",
    ]

    found_white_night = False
    for url in urls:
        r = safe_get(url)
        if not r:
            continue
        soup = BeautifulSoup(r.text, "lxml")
        text_page = soup.get_text(" ", strip=True)

        # בדוק אם הדף מכיל לילה לבן
        if "לילה לבן" not in text_page:
            continue

        # נסה לחלץ תאריך מהדף
        d = parse_hebrew_date(text_page)

        # אם לא מצאנו תאריך — חפש ספציפית "25" "יוני" בדף
        if not d:
            m = re.search(r"(\d{1,2})[./\- ]+(ביוני|יוני)[,\s]+(\d{4})", text_page)
            if m:
                try:
                    d = date(int(m.group(3)), 6, int(m.group(1))).isoformat()
                except:
                    pass

        # fallback — לילה לבן מתקיים תמיד ב-25 יוני (בערך) של השנה הנוכחית
        if not d:
            year = date.today().year
            candidate = date(year, 6, 25)
            # אם כבר עבר — קח שנה הבאה
            if candidate < date.today() - timedelta(days=1):
                candidate = date(year + 1, 6, 25)
            d = candidate.isoformat()

        try:
            ev_date = date.fromisoformat(d)
        except:
            continue

        if ev_date < date.today() - timedelta(days=1) or ev_date > date.today() + timedelta(days=CONFIG["days_ahead"]):
            continue

        if found_white_night:
            break  # כבר מצאנו — לא צריך כפילות

        ev = {
            "id": make_event_id("לילה לבן תל אביב", d),
            "title": "🌙 לילה לבן תל אביב — פסטיבל תרבות עירוני לילי",
            "date": d,
            "time": "20:00",
            "location": "רחבי תל אביב-יפו — גן צ'רלס קלור, כיכר רבין, שוק הפשפשים, נמל יפו, חוף גורדון ועוד",
            "city": "תל אביב",
            "region": "תל אביב - גוש דן",
            "event_type": "פסטיבל",
            "estimated_crowd": 100000,
            "crowd_level": "very_high",
            "crowd_type": "תושבי ת\"א ומבקרים מכל הארץ — כל הגילאים",
            "stations": json.dumps(["תל אביב – סבידור מרכז", "תל אביב – השלום", "תל אביב – האוניברסיטה – אקספו", "תל אביב – ההגנה"], ensure_ascii=False),
            "stations_primary":    json.dumps(["תל אביב – סבידור מרכז", "תל אביב – השלום"], ensure_ascii=False),
            "stations_secondary":  json.dumps(["תל אביב – האוניברסיטה – אקספו"], ensure_ascii=False),
            "stations_peripheral": json.dumps(["תל אביב – ההגנה"], ensure_ascii=False),
            "source_url": url,
            "notes": "לילה לבן — אירוע התרבות הגדול ביותר של תל אביב. עומס גבוה מאוד בכל תחנות ת\"א מ-20:00 ועד הבוקר. צפוי עומס חריג במיוחד בסבידור מרכז ובהשלום.",
        }
        events.append(ev)
        found_white_night = True
        logging.info(f"  White Night: found for {d}")
        break

    logging.info(f"  White Night Tel Aviv: {len(events)} events")
    return events


# ─────────────────────────────────────────────────────────────────────────────
# SEED EVENTS
# ─────────────────────────────────────────────────────────────────────────────
def scrape_expo_tel_aviv() -> list[dict]:
    """
    סורק אתר אקספו תל אביב הרשמי (expotelaviv.co.il) + muzi + קופת ת"א.
    כולל: כנסים, תערוכות, הופעות, ואירועים מיוחדים כמו מכביה סיטי.
    תחנה ראשית: תל אביב – האוניברסיטה – אקספו (צמודה למתחם).
    """
    events = []
    urls = [
        "https://expotelaviv.co.il/",
        "https://expotelaviv.co.il/events/",
        "https://muzi.co.il/events-by-hall/ביתן-2-אקספו-תל-אביב/",
        "https://live.tickchak.co.il/pavilion-1-expo-tel-aviv",
        "https://www.kupat.co.il/show/macabia-city",
    ]
    for url in urls:
        r = safe_get(url)
        if not r:
            continue
        soup = BeautifulSoup(r.text, "lxml")
        # נסה selectors שונים
        cards = soup.select(
            "article, .event, .event-card, .event-item, .show-item, "
            ".tribe-event, li.event, .fc-event, .card, "
            "[class*='event'], [class*='show'], [class*='item']"
        )
        if not cards:
            cards = soup.select("li, div.item, div.row")

        for card in cards[:30]:
            text = card.get_text(" ", strip=True)
            if len(text) < 5:
                continue
            title_el = card.select_one("h1,h2,h3,h4,.title,.event-title,.event-name,[class*='title'],[class*='name']")
            title = title_el.get_text(strip=True) if title_el else text[:80]
            if not title or len(title) < 3:
                continue
            d = parse_hebrew_date(text)
            if not d:
                continue
            try:
                ev_date = date.fromisoformat(d)
            except:
                continue
            if ev_date < date.today() or ev_date > date.today() + timedelta(days=CONFIG["days_ahead"]):
                continue

            # זיהוי סוג אירוע
            ev_type = "כנס"
            if any(w in title for w in ["הופעה","קונצרט","מוזיקה","שירה"]):
                ev_type = "הופעה"
            elif any(w in title for w in ["פסטיבל","פסט"]):
                ev_type = "פסטיבל"
            elif any(w in title for w in ["תערוכה","ירידה","תצוגה"]):
                ev_type = "כנס"
            elif any(w in title for w in ["ספורט","מכביה","ריצה","כדורגל","כדורסל"]):
                ev_type = "ספורט"

            # מכביה / מכביה סיטי — זיהוי מיוחד
            if any(w in title for w in ["מכביה","macabia","maccabiah"]):
                ev_type = "ספורט"
                crowd, level, crowd_type = 20000, "very_high", "ספורטאים ומשפחות מכל העולם (8,000 ספורטאים + קהל)"
            else:
                crowd, level, crowd_type = estimate_crowd(ev_type, title)

            time_match = re.search(r"(\d{1,2}:\d{2})", text)
            ev_time = time_match.group(1) if time_match else "10:00"
            link = card.select_one("a")
            src_url = link["href"] if link and link.get("href","").startswith("http") else url

            ev = {
                "id": make_event_id(title, d),
                "title": title,
                "date": d,
                "time": ev_time,
                "location": "אקספו תל אביב, גני התערוכה",
                "city": "תל אביב",
                "region": "תל אביב - גוש דן",
                "event_type": ev_type,
                "estimated_crowd": crowd,
                "crowd_level": level,
                "crowd_type": crowd_type,
                "stations": json.dumps(["תל אביב – האוניברסיטה – אקספו", "תל אביב – סבידור מרכז"], ensure_ascii=False),
                "stations_primary":    json.dumps(["תל אביב – האוניברסיטה – אקספו"], ensure_ascii=False),
                "stations_secondary":  json.dumps(["תל אביב – סבידור מרכז"], ensure_ascii=False),
                "stations_peripheral": json.dumps([], ensure_ascii=False),
                "source_url": src_url,
                "notes": "אקספו תל אביב — תחנת האוניברסיטה – אקספו צמודה למתחם (9 דקות הליכה)",
            }
            events.append(ev)

    logging.info(f"  Expo Tel Aviv: {len(events)} events")
    return events


def scrape_ticketmaster_il() -> list[dict]:
    """Ticketmaster ישראל — קונצרטים, ספורט, תיאטרון."""
    events = []
    urls = [
        "https://www.ticketmaster.co.il/search?q=&type=event&sort=date",
        "https://www.ticketmaster.co.il/venue/GH/ALL/he",
    ]
    for url in urls:
        r = safe_get(url)
        if not r:
            continue
        soup = BeautifulSoup(r.text, "lxml")
        cards = soup.select(".event-listing, .event-card, article, [class*='event'], li.event")
        for card in cards[:25]:
            text = card.get_text(" ", strip=True)
            title_el = card.select_one("h2,h3,h4,[class*='title'],[class*='name']")
            title = title_el.get_text(strip=True) if title_el else text[:80]
            if not title or len(title) < 3:
                continue
            d = parse_hebrew_date(text)
            if not d:
                continue
            try:
                ev_date = date.fromisoformat(d)
            except:
                continue
            if ev_date < date.today() or ev_date > date.today() + timedelta(days=CONFIG["days_ahead"]):
                continue
            city = "תל אביב"
            for kw in ["חיפה","נתניה","הרצליה","כפר סבא","רעננה","רמת גן"]:
                if kw in text:
                    city = kw
                    break
            if any(c in text for c in ["ירושלים","באר שבע","אילת","אשדוד","אשקלון"]):
                continue
            ev_type = "הופעה"
            if "כדורגל" in text or "ליגה" in text:
                ev_type = "כדורגל"
            elif "פסטיבל" in title:
                ev_type = "פסטיבל"
            elif "כנס" in title:
                ev_type = "כנס"
            time_match = re.search(r"(\d{1,2}:\d{2})", text)
            ev_time = time_match.group(1) if time_match else "20:00"
            crowd, level, crowd_type = estimate_crowd(ev_type, title)
            link = card.select_one("a")
            src_url = link["href"] if link and link.get("href","").startswith("http") else url
            ev = {
                "id": make_event_id(title, d),
                "title": title, "date": d, "time": ev_time,
                "location": city, "city": city, "region": guess_region(city),
                "event_type": ev_type, "estimated_crowd": crowd,
                "crowd_level": level, "crowd_type": crowd_type,
                "stations": json.dumps(guess_stations(city, text), ensure_ascii=False),
                "source_url": src_url, "notes": "",
            }
            events.append(ev)
    logging.info(f"  Ticketmaster IL: {len(events)} events")
    return events


def scrape_maccabiah_official() -> list[dict]:
    """אתר המכביה הרשמי — לוח תחרויות ואירועים."""
    events = []
    urls = [
        "https://www.maccabiah.com/schedule",
        "https://www.maccabiah.com/events",
        "https://www.maccabiah.com/the-maccabiah/history/2026",
    ]
    for url in urls:
        r = safe_get(url)
        if not r:
            continue
        soup = BeautifulSoup(r.text, "lxml")
        text_page = soup.get_text(" ", strip=True)
        cards = soup.select("article, .event, .schedule-item, .game, li.event, [class*='event'], [class*='match']")
        for card in cards[:30]:
            text = card.get_text(" ", strip=True)
            title_el = card.select_one("h2,h3,h4,[class*='title'],[class*='name']")
            title = title_el.get_text(strip=True) if title_el else text[:80]
            if not title or len(title) < 3:
                continue
            d = parse_hebrew_date(text)
            if not d:
                continue
            try:
                ev_date = date.fromisoformat(d)
            except:
                continue
            if ev_date < date.today() or ev_date > date.today() + timedelta(days=CONFIG["days_ahead"]):
                continue
            # מכביה — קבוצות ערים
            city = "תל אביב"
            for kw in ["חיפה","הרצליה","חדרה","רעננה","ירושלים"]:
                if kw in text:
                    city = kw
                    break
            if "ירושלים" in city:
                continue  # מחוץ לגזרה
            region = guess_region(city)
            time_match = re.search(r"(\d{1,2}:\d{2})", text)
            ev_time = time_match.group(1) if time_match else "10:00"
            ev = {
                "id": make_event_id(f"מכביה-{title}", d),
                "title": f"🏅 מכביה 2026 — {title}",
                "date": d, "time": ev_time,
                "location": city, "city": city, "region": region,
                "event_type": "ספורט",
                "estimated_crowd": 5000, "crowd_level": "high",
                "crowd_type": "ספורטאים ומשפחות מהארץ ומהעולם",
                "stations": json.dumps(guess_stations(city, text), ensure_ascii=False),
                "source_url": url,
                "notes": "מכביה 2026 — 8,000 ספורטאים מ-55 מדינות",
            }
            events.append(ev)
    logging.info(f"  Maccabiah Official: {len(events)} events")
    return events


def scrape_stadium_miryam() -> list[dict]:
    """אצטדיון מרים נתניה — לוח משחקים ואירועים."""
    events = []
    urls = [
        "https://www.stadium-miriam.co.il/events",
        "https://www.stadium-miriam.co.il/games",
        "https://muzi.co.il/events-by-hall/אצטדיון-מרים/",
    ]
    for url in urls:
        r = safe_get(url)
        if not r:
            continue
        soup = BeautifulSoup(r.text, "lxml")
        cards = soup.select("article, .event, .game, .match, li, [class*='event'], [class*='game']")
        for card in cards[:20]:
            text = card.get_text(" ", strip=True)
            title_el = card.select_one("h2,h3,h4,.title,.teams")
            title = title_el.get_text(strip=True) if title_el else text[:80]
            if not title or len(title) < 3:
                continue
            d = parse_hebrew_date(text)
            if not d:
                continue
            try:
                ev_date = date.fromisoformat(d)
            except:
                continue
            if ev_date < date.today() or ev_date > date.today() + timedelta(days=CONFIG["days_ahead"]):
                continue
            ev_type = "כדורגל"
            if any(w in title for w in ["הופעה","קונצרט","פסטיבל"]):
                ev_type = "הופעה"
            time_match = re.search(r"(\d{1,2}:\d{2})", text)
            ev_time = time_match.group(1) if time_match else "20:00"
            crowd, level, crowd_type = estimate_crowd(ev_type, title)
            ev = {
                "id": make_event_id(title, d),
                "title": title, "date": d, "time": ev_time,
                "location": "אצטדיון מרים, נתניה",
                "city": "נתניה", "region": "שרון",
                "event_type": ev_type, "estimated_crowd": crowd,
                "crowd_level": level, "crowd_type": crowd_type,
                "stations": json.dumps(["נתניה", "נתניה – ספיר"], ensure_ascii=False),
                "stations_primary":    json.dumps(["נתניה"], ensure_ascii=False),
                "stations_secondary":  json.dumps(["נתניה – ספיר"], ensure_ascii=False),
                "stations_peripheral": json.dumps([], ensure_ascii=False),
                "source_url": url,
                "notes": "אצטדיון מרים — קיבולת 13,610. קבוצות ביתיות: מכבי נתניה, הפועל חדרה, מכבי הרצליה",
            }
            events.append(ev)
    logging.info(f"  Stadium Miriam: {len(events)} events")
    return events


def scrape_herzliya_municipality() -> list[dict]:
    """עיריית הרצליה — אירועי תרבות וספורט."""
    events = []
    urls = [
        "https://www.herzliya.muni.il/culture-events",
        "https://www.herzliya.muni.il/residents/culture/events",
        "https://www.herzliya.muni.il/",
    ]
    for url in urls:
        r = safe_get(url)
        if not r:
            continue
        soup = BeautifulSoup(r.text, "lxml")
        cards = soup.select(".event, article, .item, [class*='event']")
        for card in cards[:15]:
            text = card.get_text(" ", strip=True)
            title_el = card.select_one("h2,h3,h4,.title")
            title = title_el.get_text(strip=True) if title_el else text[:80]
            if not title or len(title) < 4:
                continue
            d = parse_hebrew_date(text)
            if not d:
                continue
            try:
                ev_date = date.fromisoformat(d)
            except:
                continue
            if ev_date < date.today() or ev_date > date.today() + timedelta(days=CONFIG["days_ahead"]):
                continue
            ev_type = "אירוע עירייה"
            if any(w in title for w in ["הופעה","קונצרט","פסטיבל"]):
                ev_type = "הופעה"
            elif any(w in title for w in ["ריצה","מרוץ","ספורט","מכביה"]):
                ev_type = "ספורט"
            time_match = re.search(r"(\d{1,2}:\d{2})", text)
            ev_time = time_match.group(1) if time_match else "19:00"
            crowd, level, crowd_type = estimate_crowd(ev_type, title)
            ev = {
                "id": make_event_id(title, d),
                "title": title, "date": d, "time": ev_time,
                "location": "הרצליה", "city": "הרצליה", "region": "שרון",
                "event_type": ev_type, "estimated_crowd": crowd,
                "crowd_level": level, "crowd_type": crowd_type,
                "stations": json.dumps(["הרצליה"], ensure_ascii=False),
                "stations_primary":    json.dumps(["הרצליה"], ensure_ascii=False),
                "stations_secondary":  json.dumps([], ensure_ascii=False),
                "stations_peripheral": json.dumps([], ensure_ascii=False),
                "source_url": url, "notes": "",
            }
            events.append(ev)
    logging.info(f"  Herzliya Municipality: {len(events)} events")
    return events


def scrape_kfar_saba_municipality() -> list[dict]:
    """עיריית כפר סבא — אירועי תרבות."""
    events = []
    urls = [
        "https://www.kfar-saba.muni.il/culture",
        "https://www.kfar-saba.muni.il/residents/culture/events",
        "https://www.kfar-saba.muni.il/",
    ]
    for url in urls:
        r = safe_get(url)
        if not r:
            continue
        soup = BeautifulSoup(r.text, "lxml")
        cards = soup.select(".event, article, .item, [class*='event']")
        for card in cards[:15]:
            text = card.get_text(" ", strip=True)
            title_el = card.select_one("h2,h3,h4,.title")
            title = title_el.get_text(strip=True) if title_el else text[:80]
            if not title or len(title) < 4:
                continue
            d = parse_hebrew_date(text)
            if not d:
                continue
            try:
                ev_date = date.fromisoformat(d)
            except:
                continue
            if ev_date < date.today() or ev_date > date.today() + timedelta(days=CONFIG["days_ahead"]):
                continue
            ev_type = "אירוע עירייה"
            if any(w in title for w in ["הופעה","קונצרט","פסטיבל"]):
                ev_type = "הופעה"
            time_match = re.search(r"(\d{1,2}:\d{2})", text)
            ev_time = time_match.group(1) if time_match else "19:00"
            crowd, level, crowd_type = estimate_crowd(ev_type, title)
            ev = {
                "id": make_event_id(title, d),
                "title": title, "date": d, "time": ev_time,
                "location": "כפר סבא", "city": "כפר סבא", "region": "שרון",
                "event_type": ev_type, "estimated_crowd": crowd,
                "crowd_level": level, "crowd_type": crowd_type,
                "stations": json.dumps(["כפר סבא – נורדאו", "הוד השרון – צמח השרון"], ensure_ascii=False),
                "stations_primary":    json.dumps(["כפר סבא – נורדאו"], ensure_ascii=False),
                "stations_secondary":  json.dumps(["הוד השרון – צמח השרון"], ensure_ascii=False),
                "stations_peripheral": json.dumps([], ensure_ascii=False),
                "source_url": url, "notes": "",
            }
            events.append(ev)
    logging.info(f"  Kfar Saba Municipality: {len(events)} events")
    return events


def scrape_ishow() -> list[dict]:
    """iShow.co.il — אגרגטור הופעות ישראלי מקיף."""
    events = []
    url = "https://ishow.co.il/הופעות"
    r = safe_get(url)
    if not r:
        return events
    soup = BeautifulSoup(r.text, "lxml")
    cards = soup.select(".event, article, .show-item, [class*='event'], li.item")
    for card in cards[:30]:
        text = card.get_text(" ", strip=True)
        title_el = card.select_one("h2,h3,h4,[class*='title'],[class*='name']")
        title = title_el.get_text(strip=True) if title_el else text[:80]
        if not title or len(title) < 3:
            continue
        d = parse_hebrew_date(text)
        if not d:
            continue
        try:
            ev_date = date.fromisoformat(d)
        except:
            continue
        if ev_date < date.today() or ev_date > date.today() + timedelta(days=CONFIG["days_ahead"]):
            continue
        city = "תל אביב"
        for kw in ["חיפה","נתניה","הרצליה","כפר סבא","רעננה","רמת גן","קריית"]:
            if kw in text:
                city = kw
                break
        if any(c in text for c in ["ירושלים","באר שבע","אילת","אשדוד","אשקלון"]):
            continue
        ev_type = "הופעה"
        if "פסטיבל" in title:
            ev_type = "פסטיבל"
        elif "כדורגל" in text:
            ev_type = "כדורגל"
        time_match = re.search(r"(\d{1,2}:\d{2})", text)
        ev_time = time_match.group(1) if time_match else "20:00"
        crowd, level, crowd_type = estimate_crowd(ev_type, title)
        link = card.select_one("a")
        src_url = link["href"] if link and link.get("href","").startswith("http") else url
        ev = {
            "id": make_event_id(title, d),
            "title": title, "date": d, "time": ev_time,
            "location": city, "city": city, "region": guess_region(city),
            "event_type": ev_type, "estimated_crowd": crowd,
            "crowd_level": level, "crowd_type": crowd_type,
            "stations": json.dumps(guess_stations(city, text), ensure_ascii=False),
            "source_url": src_url, "notes": "",
        }
        events.append(ev)
    logging.info(f"  iShow: {len(events)} events")
    return events


def _white_night_annual_date() -> str:
    """לילה לבן מתקיים תמיד ב-25 יוני. אם עבר — מחזיר של השנה הבאה."""
    year = date.today().year
    candidate = date(year, 6, 25)
    if candidate < date.today() - timedelta(days=1):
        candidate = date(year + 1, 6, 25)
    return candidate.isoformat()


def _festival_yoter_annual_date() -> str:
    """
    פסטיבל יותר מתקיים מדי שנה בסביבות 20-25 יוני (לפני גיוס קיץ א').
    2026: 23.6.26 — נשמור את הלוגיקה: שלישי שלפני סוף יוני.
    אם כבר עבר — מחזיר של השנה הבאה.
    """
    year = date.today().year
    # חפש את ה-23 יוני של השנה הנוכחית
    candidate = date(year, 6, 23)
    if candidate < date.today() - timedelta(days=1):
        candidate = date(year + 1, 6, 23)
    return candidate.isoformat()


    """מחזיר את תאריך לילה לבן של השנה הנוכחית (25 יוני) — אם עבר, מחזיר של השנה הבאה."""
    year = date.today().year
    candidate = date(year, 6, 25)
    if candidate < date.today() - timedelta(days=1):
        candidate = date(year + 1, 6, 25)
    return candidate.isoformat()


def get_manual_seed_events() -> list[dict]:
    today = date.today()
    next_fri = today + timedelta(days=(4 - today.weekday()) % 7 or 7)
    next_sat = next_fri + timedelta(days=1)
    seeds = [
        # ── לילה לבן — seed שנתי קבוע, תמיד ב-25 יוני ──────────────────────
        {
            "title": "🌙 לילה לבן תל אביב — פסטיבל תרבות עירוני לילי",
            "date": _white_night_annual_date(),
            "time": "20:00",
            "location": "רחבי תל אביב-יפו — גן צ'רלס קלור, כיכר רבין, שוק הפשפשים, נמל יפו, חוף גורדון ועוד",
            "city": "תל אביב",
            "event_type": "פסטיבל",
            "estimated_crowd": 100000,
            "crowd_level": "very_high",
            "crowd_type": "תושבי ת\"א ומבקרים מכל הארץ — כל הגילאים",
            "notes": "לילה לבן — אירוע התרבות הגדול ביותר של תל אביב. עומס גבוה מאוד בכל תחנות ת\"א מ-20:00 ועד הבוקר. עומס חריג במיוחד בסבידור מרכז ובהשלום.",
        },
        {"title": "מחזור ליגת העל — מכבי תל אביב נגד הפועל תל אביב (דרבי)", "date": next_fri.isoformat(), "time": "20:00", "location": "אצטדיון בלומפילד, תל אביב", "city": "תל אביב", "event_type": "כדורגל", "estimated_crowd": 22000, "crowd_level": "very_high", "crowd_type": "מעריצי כדורגל", "notes": "דרבי תל אביב — עומס גבוה מאוד צפוי"},
        {"title": "פסטיבל ים תיכון — נמל תל אביב", "date": next_sat.isoformat(), "time": "18:00", "location": "נמל תל אביב", "city": "תל אביב", "event_type": "פסטיבל", "estimated_crowd": 15000, "crowd_level": "very_high", "crowd_type": "קהל מגוון, כולל תיירים", "notes": ""},
        {"title": "הופעת גד אלבז — יס פלאנט חיפה", "date": (next_fri + timedelta(days=7)).isoformat(), "time": "21:00", "location": "יס פלאנט, חיפה", "city": "חיפה", "event_type": "הופעה", "estimated_crowd": 6000, "crowd_level": "high", "crowd_type": "קהל בידור, כולל קשישים ומשפחות", "notes": ""},
        {"title": "מכבי תל אביב כדורסל — ליגת Winner נגד הפועל ת\"א | היכל מנורה מבטחים", "date": (next_sat + timedelta(days=6)).isoformat(), "time": "20:00", "location": "היכל מנורה מבטחים, תל אביב", "city": "תל אביב", "event_type": "ספורט", "estimated_crowd": 10000, "crowd_level": "high", "crowd_type": "מעריצי כדורסל, קהל מגוון", "notes": "דרבי כדורסל — היכל מלא. תחנת השלום ברבע שעה הליכה"},
        {"title": "מחזור ליגת העל — מכבי חיפה נגד הפועל חיפה | אצטדיון סמי עופר", "date": (next_sat + timedelta(days=7)).isoformat(), "time": "19:30", "location": "אצטדיון סמי עופר, חיפה", "city": "חיפה", "event_type": "כדורגל", "estimated_crowd": 18000, "crowd_level": "very_high", "crowd_type": "מעריצי כדורגל", "notes": "דרבי חיפה — צפוי עומס גבוה בתחנת חוף הכרמל"},
        {"title": "כנס הייטק ישראל — אקספו תל אביב", "date": (next_fri + timedelta(days=10)).isoformat(), "time": "09:00", "location": "אקספו תל אביב, גני התערוכה", "city": "תל אביב", "event_type": "כנס", "estimated_crowd": 4000, "crowd_level": "medium", "crowd_type": "אנשי עסקים וטכנולוגיה", "notes": "תחנת האוניברסיטה – אקספו צמודה למתחם"},
        {"title": "ימי סטודנטים — אוניברסיטת תל אביב", "date": (next_fri + timedelta(days=12)).isoformat(), "time": "12:00", "location": "אוניברסיטת תל אביב", "city": "תל אביב", "event_type": "ימי סטודנטים", "estimated_crowd": 8000, "crowd_level": "high", "crowd_type": "סטודנטים (ממוצע גיל 20-30)", "notes": "קמפוס צמוד לתחנת הרכבת — עומס ישיר ומשמעותי"},
        {"title": "פסטיבל הירקון — פארק הירקון וגני יהושע", "date": (next_sat + timedelta(days=12)).isoformat(), "time": "16:00", "location": "פארק הירקון, גני יהושע, תל אביב", "city": "תל אביב", "event_type": "פסטיבל", "estimated_crowd": 25000, "crowd_level": "very_high", "crowd_type": "קהל מגוון — משפחות, צעירים, תיירים", "notes": "אירוע פארק ענק — עומס גבוה בתחנת האוניברסיטה – אקספו"},
        {"title": "מחזור ליגת העל — מכבי נתניה נגד הפועל חדרה | אצטדיון מרים", "date": (next_fri + timedelta(days=13)).isoformat(), "time": "19:00", "location": "אצטדיון מרים, נתניה", "city": "נתניה", "event_type": "כדורגל", "estimated_crowd": 10000, "crowd_level": "high", "crowd_type": "מעריצי כדורגל", "notes": "אצטדיון מרים — קיבולת 13,610"},
        {"title": "פסטיבל הג׳אז — נמל נתניה", "date": (next_fri + timedelta(days=14)).isoformat(), "time": "19:00", "location": "נמל נתניה", "city": "נתניה", "event_type": "פסטיבל", "estimated_crowd": 8000, "crowd_level": "high", "crowd_type": "קהל מוזיקה, ממוצע גיל 35+", "notes": ""},
        {"title": "מחזור ליגת העל — מכבי תל אביב נגד הפועל חיפה", "date": (next_sat + timedelta(days=14)).isoformat(), "time": "20:15", "location": "אצטדיון בלומפילד, תל אביב", "city": "תל אביב", "event_type": "כדורגל", "estimated_crowd": 14000, "crowd_level": "high", "crowd_type": "מעריצי כדורגל", "notes": ""},
        {"title": "⚓ הגעת אונייה — MSC Magnifica | נמל חיפה", "date": (next_fri + timedelta(days=18)).isoformat(), "time": "07:00", "location": "נמל חיפה — מסוף נוסעים", "city": "חיפה", "event_type": "הפלגה", "estimated_crowd": 3500, "crowd_level": "medium", "crowd_type": "נוסעי קרוז ומשפחות מלווות (ירידה ועלייה)", "notes": "אוניית קרוז — נוסעים יורדים ועולים"},
        {"title": "מרוץ הרצליה הבינלאומי", "date": (next_fri + timedelta(days=20)).isoformat(), "time": "07:00", "location": "טיילת הרצליה", "city": "הרצליה", "event_type": "ספורט", "estimated_crowd": 5000, "crowd_level": "medium", "crowd_type": "ספורטאים וצופים", "notes": "עומס בוקר מוקדם"},
        {"title": "אירוע עירייה — יום העצמאות חיפה", "date": (next_sat + timedelta(days=21)).isoformat(), "time": "20:00", "location": "כיכר פריז, חיפה", "city": "חיפה", "event_type": "אירוע עירייה", "estimated_crowd": 12000, "crowd_level": "high", "crowd_type": "תושבים ומשפחות", "notes": "אירוע ציבורי גדול — תנועה גבוהה בתחנות הכרמל"},
        # ── מכביה 2026 — seed קבוע ─────────────────────────────────────────────
        {
            "title": "🏅 מכביה 2026 — טקס סיום | תל אביב",
            "date": "2026-07-13",
            "time": "20:00",
            "location": "תל אביב",
            "city": "תל אביב",
            "event_type": "ספורט",
            "estimated_crowd": 30000,
            "crowd_level": "very_high",
            "crowd_type": "ספורטאים ומשפחות מהארץ ומהעולם (55 מדינות)",
            "notes": "טקס סיום המכביה 2026 בתל אביב. עומס גבוה מאוד צפוי בכל תחנות ת\"א",
        },
        {
            "title": "🏟️ אקספו מכביה סיטי — פסטיבל הספורט של ישראל",
            "date": "2026-07-06",
            "time": "10:00",
            "location": "אקספו תל אביב, גני התערוכה",
            "city": "תל אביב",
            "event_type": "ספורט",
            "estimated_crowd": 20000,
            "crowd_level": "very_high",
            "crowd_type": "משפחות, ספורטאים, ילדים — קהל מגוון מכל הארץ",
            "notes": "אקספו מכביה סיטי פועל בין 6-16 יולי 2026. עומס יומי גבוה בתחנת האוניברסיטה – אקספו",
        },
        {
            "title": "🏟️ אקספו מכביה סיטי — פסטיבל הספורט של ישראל",
            "date": "2026-07-07",
            "time": "10:00",
            "location": "אקספו תל אביב, גני התערוכה",
            "city": "תל אביב",
            "event_type": "ספורט",
            "estimated_crowd": 20000,
            "crowd_level": "very_high",
            "crowd_type": "משפחות, ספורטאים, ילדים — קהל מגוון מכל הארץ",
            "notes": "אקספו מכביה סיטי — יום 2. עומס גבוה בתחנת האוניברסיטה – אקספו",
        },
        {
            "title": "🏟️ אקספו מכביה סיטי — פסטיבל הספורט של ישראל",
            "date": "2026-07-08",
            "time": "10:00",
            "location": "אקספו תל אביב, גני התערוכה",
            "city": "תל אביב",
            "event_type": "ספורט",
            "estimated_crowd": 20000,
            "crowd_level": "very_high",
            "crowd_type": "משפחות, ספורטאים, ילדים — קהל מגוון מכל הארץ",
            "notes": "אקספו מכביה סיטי — יום 3. עומס גבוה בתחנת האוניברסיטה – אקספו",
        },
        {
            "title": "🏟️ אקספו מכביה סיטי — פסטיבל הספורט של ישראל",
            "date": "2026-07-09",
            "time": "10:00",
            "location": "אקספו תל אביב, גני התערוכה",
            "city": "תל אביב",
            "event_type": "ספורט",
            "estimated_crowd": 20000,
            "crowd_level": "very_high",
            "crowd_type": "משפחות, ספורטאים, ילדים — קהל מגוון מכל הארץ",
            "notes": "אקספו מכביה סיטי — יום 4. עומס גבוה בתחנת האוניברסיטה – אקספו",
        },
        {
            "title": "🏟️ אקספו מכביה סיטי — פסטיבל הספורט של ישראל",
            "date": "2026-07-10",
            "time": "10:00",
            "location": "אקספו תל אביב, גני התערוכה",
            "city": "תל אביב",
            "event_type": "ספורט",
            "estimated_crowd": 20000,
            "crowd_level": "very_high",
            "crowd_type": "משפחות, ספורטאים, ילדים — קהל מגוון מכל הארץ",
            "notes": "אקספו מכביה סיטי — יום 5. עומס גבוה בתחנת האוניברסיטה – אקספו",
        },
        # ── פסטיבל יותר — seed שנתי קבוע, תמיד ~23 יוני ──────────────────────
        {
            "title": "🎪 פסטיבל יותר — מסיבת הגיוס הגדולה בישראל | גני יהושע",
            "date": _festival_yoter_annual_date(),
            "time": "16:00",
            "location": "גני יהושע, אמפי תל אביב, פארק הירקון",
            "city": "תל אביב",
            "event_type": "פסטיבל",
            "estimated_crowd": 15000,
            "crowd_level": "very_high",
            "crowd_type": "מתגייסים לצה\"ל (גיל 18) — כניסה חופשית",
            "notes": "פסטיבל יותר — מסיבת הגיוס הגדולה בישראל. עומס גבוה מאוד צפוי בתחנת האוניברסיטה – אקספו",
        },
    ]
    events = []
    for s in seeds:
        # לילה לבן — תחנות מוגדרות ידנית (4 תחנות, כולן ראשיות)
        if "לילה לבן" in s["title"]:
            s["id"] = make_event_id(s["title"], s["date"])
            s["region"] = "תל אביב - גוש דן"
            s["stations"] = json.dumps(["תל אביב – סבידור מרכז","תל אביב – השלום","תל אביב – האוניברסיטה – אקספו","תל אביב – ההגנה"], ensure_ascii=False)
            s["stations_primary"]    = json.dumps(["תל אביב – סבידור מרכז","תל אביב – השלום"], ensure_ascii=False)
            s["stations_secondary"]  = json.dumps(["תל אביב – האוניברסיטה – אקספו"], ensure_ascii=False)
            s["stations_peripheral"] = json.dumps(["תל אביב – ההגנה"], ensure_ascii=False)
            s["source_url"] = "manual-seed"
            events.append(s)
            continue
        impact = guess_station_impact(s["city"], s["location"])
        all_stations = impact["primary"] + impact["secondary"] + impact["peripheral"]
        s["id"] = make_event_id(s["title"], s["date"])
        s["region"] = guess_region(s["city"])
        s["stations"] = json.dumps(all_stations, ensure_ascii=False)
        s["stations_primary"]    = json.dumps(impact["primary"],    ensure_ascii=False)
        s["stations_secondary"]  = json.dumps(impact["secondary"],  ensure_ascii=False)
        s["stations_peripheral"] = json.dumps(impact["peripheral"], ensure_ascii=False)
        s["source_url"] = "manual-seed"
        events.append(s)
    return events

# ─────────────────────────────────────────────────────────────────────────────
# MAIN SCAN
# ─────────────────────────────────────────────────────────────────────────────
def run_scan() -> tuple[list[dict], list[str]]:
    logging.info("Starting scan...")
    all_scraped = []
    scrapers = [
        ("Tel Aviv Municipality",  scrape_tel_aviv_municipality),
        ("Haifa Municipality",     scrape_haifa_municipality),
        ("Netanya Municipality",   scrape_netanya_municipality),
        ("Football IFA",           scrape_football_israel),
        ("LeAan",                  scrape_leaan),
        ("TimeOut IL",             scrape_timeout_il),
        ("Eventim IL",             scrape_eventim_il),
        ("Haifa Port Cruises",     scrape_haifa_port_cruises),
        ("TAU Events",             scrape_tau_events),
        ("Bloomfield Stadium",     scrape_bloomfield),
        ("Sami Ofer Stadium",      scrape_sami_ofer),
        ("Ramat Gan Park",         scrape_ramat_gan_park),
        ("Ganei Yehoshua",         scrape_ganei_yehoshua),
        ("White Night Tel Aviv",   scrape_white_night_tel_aviv),
        ("Expo Tel Aviv",          scrape_expo_tel_aviv),
        ("Ticketmaster IL",        scrape_ticketmaster_il),
        ("Maccabiah Official",     scrape_maccabiah_official),
        ("Stadium Miriam",         scrape_stadium_miryam),
        ("Herzliya Municipality",  scrape_herzliya_municipality),
        ("Kfar Saba Municipality", scrape_kfar_saba_municipality),
        ("iShow",                  scrape_ishow),
    ]
    for name, fn in scrapers:
        try:
            found = fn()
            logging.info(f"  {name}: {len(found)} events scraped")
            all_scraped.extend(found)
        except Exception as e:
            logging.error(f"  {name} ERROR: {e}")

    if len(all_scraped) < 3:
        logging.info("  Adding seed events (scrapers returned few results)")
        all_scraped.extend(get_manual_seed_events())

    for ev in all_scraped:
        if "stations_primary" not in ev:
            impact = guess_station_impact(ev.get("city",""), ev.get("location",""))
            all_st = impact["primary"] + impact["secondary"] + impact["peripheral"]
            ev["stations"]            = json.dumps(all_st, ensure_ascii=False)
            ev["stations_primary"]    = json.dumps(impact["primary"],    ensure_ascii=False)
            ev["stations_secondary"]  = json.dumps(impact["secondary"],  ensure_ascii=False)
            ev["stations_peripheral"] = json.dumps(impact["peripheral"], ensure_ascii=False)

    valid_events, rejected = validate_and_filter(all_scraped)
    logging.info(f"  Validation: {len(valid_events)} passed, {len(rejected)} rejected")

    new_ids = []
    for ev in valid_events:
        if upsert_event(ev):
            new_ids.append(ev["id"])

    purge_past_events()
    events = get_upcoming_events()
    logging.info(f"Scan complete. {len(events)} upcoming events in DB, {len(new_ids)} new.")
    return events, new_ids

# ─────────────────────────────────────────────────────────────────────────────
# EMAIL BUILDER
# ─────────────────────────────────────────────────────────────────────────────
CROWD_COLOR = {"low": "#22c55e", "medium": "#f59e0b", "high": "#ef4444", "very_high": "#7c3aed"}

def format_date_hebrew(d: str) -> str:
    try:
        dt = date.fromisoformat(d)
        days = ["ב׳ שני","ג׳ שלישי","ד׳ רביעי","ה׳ חמישי","ו׳ שישי","ש׳ שבת","א׳ ראשון"]
        months = ["","ינואר","פברואר","מרץ","אפריל","מאי","יוני","יולי","אוגוסט","ספטמבר","אוקטובר","נובמבר","דצמבר"]
        return f"{days[dt.weekday()]}, {dt.day} ב{months[dt.month]} {dt.year}"
    except: return d

def week_label(week_num: int, week_start: date, week_end: date) -> str:
    months = ["","ינואר","פברואר","מרץ","אפריל","מאי","יוני","יולי","אוגוסט","ספטמבר","אוקטובר","נובמבר","דצמבר"]
    return f"שבוע {week_num} &nbsp;|&nbsp; {week_start.day} ב{months[week_start.month]} – {week_end.day} ב{months[week_end.month]}"

def build_email_html(events: list[dict], new_ids: list[str]) -> str:
    today = date.today()
    today_str = format_date_hebrew(today.isoformat())
    end_date = today + timedelta(days=CONFIG["days_ahead"])
    months = ["","ינואר","פברואר","מרץ","אפריל","מאי","יוני","יולי","אוגוסט","ספטמבר","אוקטובר","נובמבר","דצמבר"]
    range_str = f"{today.day} ב{months[today.month]} – {end_date.day} ב{months[end_date.month]} {end_date.year}"
    has_updates = bool(new_ids)
    new_ids_set = set(new_ids)
    by_date: dict[str, list] = {}
    for ev in events:
        by_date.setdefault(ev["date"], []).append(ev)
    def get_week(d_str): return (date.fromisoformat(d_str) - today).days // 7 + 1
    weeks: dict[int, list[str]] = {}
    for d in sorted(by_date.keys()):
        weeks.setdefault(get_week(d), []).append(d)

    events_html = ""
    for wk in sorted(weeks.keys()):
        wk_start = today + timedelta(days=(wk-1)*7)
        wk_end   = wk_start + timedelta(days=6)
        events_html += f'<div style="margin-bottom:36px;"><div style="background:linear-gradient(90deg,#1e3a5f,#0369a1);color:#fff;padding:10px 18px;border-radius:8px;margin-bottom:18px;font-size:14px;font-weight:700;">📅 {week_label(wk,wk_start,wk_end)}</div>'
        for d in weeks[wk]:
            date_label = format_date_hebrew(d)
            days_diff = (date.fromisoformat(d) - today).days
            if days_diff == 0: day_badge = '<span style="background:#ef4444;color:#fff;padding:2px 10px;border-radius:20px;font-size:11px;font-weight:700;">היום</span>'
            elif days_diff == 1: day_badge = '<span style="background:#f59e0b;color:#fff;padding:2px 10px;border-radius:20px;font-size:11px;font-weight:700;">מחר</span>'
            else: day_badge = f'<span style="background:#94a3b8;color:#fff;padding:2px 10px;border-radius:20px;font-size:11px;font-weight:700;">בעוד {days_diff} ימים</span>'
            events_html += f'<div style="margin-bottom:24px;"><div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid #e2e8f0;"><span style="font-size:15px;font-weight:700;color:#334155;">{date_label}</span>{day_badge}</div>'
            for ev in by_date[d]:
                s_primary   = json.loads(ev.get("stations_primary")   or "[]")
                s_secondary = json.loads(ev.get("stations_secondary")  or "[]")
                s_peripheral= json.loads(ev.get("stations_peripheral") or "[]")
                def station_tag(name, tier):
                    if tier=="primary":   bg,color,label="#1e3a5f","#fff","🔴 ראשית"
                    elif tier=="secondary": bg,color,label="#dbeafe","#1e40af","🟡 משנית"
                    else:                  bg,color,label="#f1f5f9","#475569","⚪ שולית"
                    return f'<span style="background:{bg};color:{color};padding:4px 11px;border-radius:12px;font-size:12px;margin:2px;display:inline-block;font-weight:600;">🚉 {name} <span style="opacity:0.75;font-size:11px;">({label})</span></span>'
                if s_primary or s_secondary or s_peripheral:
                    station_html = '<div style="margin-top:10px;line-height:1.9;">'
                    for s in s_primary:   station_html += station_tag(s,"primary")
                    for s in s_secondary: station_html += station_tag(s,"secondary")
                    for s in s_peripheral:station_html += station_tag(s,"peripheral")
                    station_html += '</div><div style="margin-top:4px;font-size:11px;color:#94a3b8;">🔴 ראשית = עומס גבוה ביותר &nbsp;|&nbsp; 🟡 משנית = עומס בינוני &nbsp;|&nbsp; ⚪ שולית = השפעה קלה</div>'
                else:
                    station_html = '<div style="margin-top:8px;font-size:12px;color:#ef4444;">⚠️ תחנות לא מזוהות — יש לבדוק ידנית</div>'
                crowd_color = CROWD_COLOR.get(ev["crowd_level"],"#64748b")
                crowd_label = CROWD_LEVEL_HEBREW.get(ev["crowd_level"],ev["crowd_level"])
                emoji = EVENT_TYPE_EMOJI.get(ev["event_type"],"📅")
                new_badge = '<span style="background:#dc2626;color:#fff;padding:2px 9px;border-radius:20px;font-size:11px;font-weight:700;margin-left:6px;">⚡ חדש</span>' if ev["id"] in new_ids_set else ""
                region_color = {"תל אביב - גוש דן":"#0ea5e9","שרון":"#10b981","חיפה וצפון":"#8b5cf6"}.get(ev["region"],"#64748b")
                notes_html = f'<div style="margin-top:8px;padding:8px 12px;background:#fff7ed;border-right:3px solid #f59e0b;border-radius:4px;font-size:13px;color:#92400e;">⚠️ {ev["notes"]}</div>' if ev.get("notes") else ""
                events_html += f'''<div style="background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:16px 20px;margin-bottom:12px;border-right:4px solid {crowd_color};box-shadow:0 1px 3px rgba(0,0,0,0.05);">
  <div style="display:flex;align-items:flex-start;justify-content:space-between;flex-wrap:wrap;gap:8px;">
    <div style="flex:1;min-width:200px;"><div style="font-size:15px;font-weight:700;color:#0f172a;margin-bottom:5px;">{new_badge}{emoji} {ev["title"]}</div>
    <div style="font-size:13px;color:#475569;">🕐 {ev["time"]} &nbsp;|&nbsp; 📍 {ev["location"]} &nbsp;|&nbsp; <span style="color:{region_color};font-weight:600;">{ev["region"]}</span></div></div>
    <div style="text-align:left;white-space:nowrap;"><div style="font-size:20px;font-weight:800;color:{crowd_color};">{ev["estimated_crowd"]:,}</div>
    <div style="font-size:11px;color:#94a3b8;">משתתפים צפויים</div><div style="font-size:12px;font-weight:600;color:{crowd_color};">עוצמה: {crowd_label}</div></div>
  </div>
  <div style="margin-top:7px;font-size:13px;color:#64748b;">👥 סוג קהל: <strong>{ev["crowd_type"]}</strong></div>
  {station_html}{notes_html}</div>'''
            events_html += "</div>"
        events_html += "</div>"

    update_banner = f'<div style="background:linear-gradient(135deg,#dc2626,#b91c1c);color:#fff;padding:16px 24px;border-radius:10px;margin-bottom:24px;text-align:center;"><div style="font-size:18px;font-weight:800;">⚡ שים לב — יש עדכונים חדשים!</div><div style="font-size:14px;opacity:0.9;margin-top:4px;">{len(new_ids)} אירועים חדשים נוספו מאז הדוח האחרון — מסומנים ב-<strong>⚡ חדש</strong></div></div>' if has_updates else '<div style="background:#f0fdf4;border:1px solid #86efac;color:#166534;padding:12px 20px;border-radius:10px;margin-bottom:24px;text-align:center;font-size:14px;">✅ אין עדכונים חדשים מאז הדוח האחרון — הדוח הנוכחי ללא שינוי</div>'
    region_counts = {}
    for ev in events: region_counts[ev["region"]] = region_counts.get(ev["region"],0)+1
    summary_html = "".join(f'<div style="flex:1;background:#f8fafc;border-radius:10px;padding:14px;text-align:center;min-width:120px;"><div style="font-size:26px;font-weight:800;color:#0f172a;">{cnt}</div><div style="font-size:12px;color:#64748b;margin-top:2px;">{reg}</div></div>' for reg,cnt in region_counts.items())
    return f"""<!DOCTYPE html><html dir="rtl" lang="he"><head><meta charset="UTF-8"><style>body{{font-family:'Segoe UI',Arial,sans-serif;background:#f1f5f9;margin:0;padding:0;direction:rtl;}}*{{box-sizing:border-box;}}</style></head><body>
<div style="max-width:700px;margin:24px auto;font-family:'Segoe UI',Arial,sans-serif;direction:rtl;">
<div style="background:linear-gradient(135deg,#1e3a5f 0%,#0284c7 100%);color:#fff;padding:28px 32px;border-radius:14px 14px 0 0;">
<div style="display:flex;align-items:center;gap:14px;"><div style="font-size:40px;">🚆</div><div><div style="font-size:22px;font-weight:800;">ניתוח עומסים — רכבת ישראל</div><div style="font-size:14px;opacity:0.85;margin-top:3px;">דוח חודשי | נשלח: {today_str}</div></div></div>
<div style="margin-top:16px;background:rgba(255,255,255,0.12);border-radius:8px;padding:10px 16px;font-size:13px;">📆 טווח הדוח: <strong>{range_str}</strong> &nbsp;&nbsp;•&nbsp;&nbsp; סה״כ אירועים: <strong>{len(events)}</strong> &nbsp;&nbsp;•&nbsp;&nbsp; גזרות: תל אביב–גוש דן, שרון, חיפה וצפון</div></div>
<div style="background:#fff;padding:28px 32px;border-radius:0 0 14px 14px;border:1px solid #e2e8f0;border-top:none;">
{update_banner}
<div style="margin-bottom:28px;"><div style="font-size:12px;font-weight:600;color:#94a3b8;text-transform:uppercase;letter-spacing:1px;margin-bottom:10px;">סה״כ אירועים לפי גזרה</div><div style="display:flex;gap:10px;flex-wrap:wrap;">{summary_html}</div></div>
<hr style="border:none;border-top:2px solid #e2e8f0;margin:0 0 28px;">
{events_html if events else '<div style="text-align:center;color:#94a3b8;padding:40px;">לא נמצאו אירועים ב-30 הימים הקרובים</div>'}
</div>
<div style="text-align:center;padding:16px;font-size:12px;color:#94a3b8;">נשלח אוטומטית כל יום ב-12:00 | Rail Event Bot &nbsp;•&nbsp; רכבת ישראל — תכנון תפעולי<br>נתונים מבוססים על סריקת אתרים ציבוריים. יש לאמת לפני פרסום רשמי.</div>
</div></body></html>"""

def build_pdf_summary_html(events: list[dict]) -> str:
    today = date.today()
    end_date = today + timedelta(days=CONFIG["days_ahead"])
    months = ["","ינואר","פברואר","מרץ","אפריל","מאי","יוני","יולי","אוגוסט","ספטמבר","אוקטובר","נובמבר","דצמבר"]
    range_str = f"{today.day}/{today.month} – {end_date.day}/{end_date.month}/{end_date.year}"
    region_counts = {}
    for ev in events: region_counts[ev["region"]] = region_counts.get(ev["region"],0)+1
    crowd_colors = {"very_high":("#fae8ff","#7c3aed","גבוהה מאוד"),"high":("#fee2e2","#dc2626","גבוהה"),"medium":("#fef9c3","#854d0e","בינונית"),"low":("#f0fdf4","#15803d","נמוכה")}
    region_colors = {"תל אביב - גוש דן":"#0ea5e9","שרון":"#10b981","חיפה וצפון":"#8b5cf6"}
    rows = ""
    for i, ev in enumerate(events):
        bg = "#fafafa" if i%2 else "#fff"
        dt = date.fromisoformat(ev["date"])
        days_heb = ["ב׳","ג׳","ד׳","ה׳","ו׳","ש׳","א׳"]
        date_str = f"{dt.day:02d}/{dt.month:02d} {days_heb[dt.weekday()]}"
        emoji = EVENT_TYPE_EMOJI.get(ev["event_type"],"📅")
        title_short = ev["title"][:30] + ("..." if len(ev["title"])>30 else "")
        primary = json.loads(ev.get("stations_primary") or "[]")
        station_str = (primary[0] if primary else "—").replace("תל אביב – ","ת\"א – ").replace("האוניברסיטה – ","אונ׳ – ")
        crowd_bg,crowd_color,crowd_label = crowd_colors.get(ev["crowd_level"],("#f1f5f9","#64748b","—"))
        region_color = region_colors.get(ev["region"],"#64748b")
        region_short = ev["region"].replace("תל אביב - גוש דן","ת\"א").replace("חיפה וצפון","חיפה")
        rows += f'<tr style="background:{bg};"><td style="padding:4px 6px;color:#475569;white-space:nowrap;font-size:10px;">{date_str}</td><td style="padding:4px 6px;color:#0f172a;font-size:10px;">{emoji} {title_short}</td><td style="padding:4px 6px;font-size:10px;color:{region_color};">{region_short}</td><td style="padding:4px 6px;text-align:center;font-size:10px;">{ev["estimated_crowd"]:,}</td><td style="padding:4px 6px;font-size:10px;color:#334155;">{station_str}</td><td style="padding:4px 6px;text-align:center;"><span style="background:{crowd_bg};color:{crowd_color};padding:1px 5px;border-radius:8px;font-size:9px;">{crowd_label}</span></td></tr>'
    summary_boxes = "".join(f'<div style="background:#f8fafc;border-radius:6px;padding:6px 10px;text-align:center;min-width:100px;"><div style="font-size:18px;font-weight:500;color:#0f172a;">{cnt}</div><div style="font-size:9px;color:#64748b;">{reg}</div></div>' for reg,cnt in region_counts.items())
    return f"""<!DOCTYPE html><html dir="rtl" lang="he"><head><meta charset="UTF-8"><style>body{{font-family:Arial,sans-serif;background:#f8fafc;margin:0;padding:12px;direction:rtl;}}*{{box-sizing:border-box;}}</style></head><body>
<div style="max-width:720px;margin:0 auto;">
<div style="background:linear-gradient(135deg,#1e3a5f,#0284c7);color:#fff;padding:12px 16px;border-radius:8px 8px 0 0;display:flex;align-items:center;justify-content:space-between;">
<div style="display:flex;align-items:center;gap:8px;"><span style="font-size:22px;">🚆</span><div><div style="font-size:13px;font-weight:700;">ניתוח עומסים — רכבת ישראל</div><div style="font-size:10px;opacity:0.85;">דוח סיכום | {range_str}</div></div></div>
<div style="font-size:10px;opacity:0.8;">{len(events)} אירועים</div></div>
<div style="background:#fff;border:0.5px solid #e2e8f0;border-top:none;padding:10px 14px;border-radius:0 0 8px 8px;">
<div style="display:flex;gap:8px;margin-bottom:10px;">{summary_boxes}</div>
<table style="width:100%;border-collapse:collapse;"><thead><tr style="background:#f1f5f9;">
<th style="padding:5px 6px;text-align:right;font-size:10px;color:#64748b;border-bottom:1px solid #e2e8f0;font-weight:500;">תאריך</th>
<th style="padding:5px 6px;text-align:right;font-size:10px;color:#64748b;border-bottom:1px solid #e2e8f0;font-weight:500;">אירוע</th>
<th style="padding:5px 6px;text-align:right;font-size:10px;color:#64748b;border-bottom:1px solid #e2e8f0;font-weight:500;">גזרה</th>
<th style="padding:5px 6px;text-align:center;font-size:10px;color:#64748b;border-bottom:1px solid #e2e8f0;font-weight:500;">קהל</th>
<th style="padding:5px 6px;text-align:right;font-size:10px;color:#64748b;border-bottom:1px solid #e2e8f0;font-weight:500;">תחנה ראשית</th>
<th style="padding:5px 6px;text-align:center;font-size:10px;color:#64748b;border-bottom:1px solid #e2e8f0;font-weight:500;">עוצמה</th>
</tr></thead><tbody>{rows}</tbody></table>
<div style="margin-top:8px;font-size:9px;color:#94a3b8;text-align:center;border-top:0.5px solid #e2e8f0;padding-top:6px;">Rail Event Bot | רכבת ישראל — תכנון תפעולי</div>
</div></div></body></html>"""

def html_to_pdf(html_body: str) -> bytes | None:
    try:
        import pdfkit
        options = {"encoding":"utf-8","page-size":"A4","margin-top":"10mm","margin-bottom":"10mm","margin-left":"10mm","margin-right":"10mm","quiet":""}
        return pdfkit.from_string(html_body, False, options=options)
    except Exception as e:
        logging.warning(f"PDF generation failed: {e}")
        return None

def send_email(html_body: str, has_updates: bool, events: list = None):
    subject_prefix = "⚡ עדכון חדש | " if has_updates else ""
    today_str = datetime.now().strftime("%d/%m/%Y")
    date_for_filename = datetime.now().strftime("%Y-%m-%d")
    subject = f"{subject_prefix}דוח אירועים חודשי — 30 ימים קדימה | {today_str}"
    recipients = [r.strip() for r in CONFIG["recipient_email"].split(",") if r.strip()]
    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = CONFIG["smtp_user"]
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    pdf_bytes = html_to_pdf(build_pdf_summary_html(events or []))
    if pdf_bytes:
        pdf_part = MIMEBase("application","pdf")
        pdf_part.set_payload(pdf_bytes)
        encoders.encode_base64(pdf_part)
        pdf_part.add_header("Content-Disposition","attachment",filename=("utf-8","",f"דוח_אירועים_{date_for_filename}.pdf"))
        msg.attach(pdf_part)
        logging.info("PDF attached successfully")
    else:
        logging.warning("PDF not attached — pdfkit unavailable")
    with smtplib.SMTP(CONFIG["smtp_host"], CONFIG["smtp_port"]) as server:
        server.ehlo(); server.starttls()
        server.login(CONFIG["smtp_user"], CONFIG["smtp_password"])
        server.sendmail(CONFIG["smtp_user"], recipients, msg.as_string())
    logging.info(f"Email sent to {recipients}")

def daily_job():
    logging.info("=== Daily job started ===")
    events, new_ids = run_scan()
    send_email(build_email_html(events, new_ids), bool(new_ids), events)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler("rail_bot.log",encoding="utf-8"), logging.StreamHandler()])
    parser = argparse.ArgumentParser(description="Israel Railways Event Scanner Bot")
    parser.add_argument("--now",     action="store_true", help="Scan and send email immediately")
    parser.add_argument("--test",    action="store_true", help="Send test email with seed data only")
    parser.add_argument("--preview", action="store_true", help="Save HTML preview to preview.html (no email)")
    args = parser.parse_args()
    init_db()
    if args.test:
        events = get_manual_seed_events()
        for ev in events: upsert_event(ev)
        events = get_upcoming_events()
        send_email(build_email_html(events,[e["id"] for e in events]), True)
        print("Test email sent.")
    elif args.preview:
        events, new_ids = run_scan()
        html = build_email_html(events, new_ids)
        with open("preview.html","w",encoding="utf-8") as f: f.write(html)
        print(f"Preview saved to preview.html ({len(events)} events, {len(new_ids)} new)")
    elif args.now:
        daily_job()
    else:
        send_at = f"{CONFIG['send_hour']:02d}:00"
        schedule.every().day.at(send_at).do(daily_job)
        logging.info(f"Scheduler started. Will send daily at {send_at}.")
        print(f"Bot running. Email scheduled at {send_at} daily. Press Ctrl+C to stop.")
        while True:
            schedule.run_pending()
            time.sleep(30)
