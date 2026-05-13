import requests
import smtplib
import sys
from email.message import EmailMessage
from datetime import datetime, timedelta
import json
import os
import pytz
import schedule
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv

load_dotenv()

EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
RECIPIENT = "bbragg1@terpmail.umd.edu"
DATA_FILE = "game_data.json"
REDSOX_ID = "2"
EASTERN = pytz.timezone("US/Eastern")

ESPN_SCHEDULE_URL = f"https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/teams/{REDSOX_ID}/schedule"
ESPN_SUMMARY_URL = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/summary"


# ── Data storage ──────────────────────────────────────────────────────────────

def load_stored_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE) as f:
            return json.load(f)
    return {"games": []}


def store_game(record):
    data = load_stored_data()
    existing_ids = {g["event_id"] for g in data["games"]}
    if record["event_id"] not in existing_ids:
        data["games"].append(record)
        with open(DATA_FILE, "w") as f:
            json.dump(data, f, indent=2)


# ── ESPN API fetching ─────────────────────────────────────────────────────────

def fetch_schedule():
    r = requests.get(ESPN_SCHEDULE_URL, timeout=10)
    r.raise_for_status()
    return r.json()


def fetch_summary(event_id):
    r = requests.get(ESPN_SUMMARY_URL, params={"event": event_id}, timeout=10)
    r.raise_for_status()
    return r.json()


# ── Schedule parsing ──────────────────────────────────────────────────────────

def get_team_record(events):
    """Extract Red Sox W-L record from the most recent event."""
    for event in reversed(events):
        for comp in event.get("competitions", []):
            for c in comp.get("competitors", []):
                if c.get("team", {}).get("id") != REDSOX_ID:
                    continue
                for rec in c.get("records", []):
                    if rec.get("type") == "total":
                        return rec.get("summary", "")
                recs = c.get("record", [])
                if recs:
                    return recs[0].get("summary", "")
    return None


def get_todays_game(events):
    """Return structured info about today's game, or None."""
    today = datetime.now(EASTERN).date()
    for event in events:
        event_date = datetime.fromisoformat(event["date"].replace("Z", "+00:00")).astimezone(EASTERN).date()
        if event_date != today:
            continue

        comp = event["competitions"][0]
        competitors = comp.get("competitors", [])
        game_time = datetime.fromisoformat(event["date"].replace("Z", "+00:00")).astimezone(EASTERN)

        home = away = None
        for c in competitors:
            if c["homeAway"] == "home":
                home = c["team"]["displayName"]
            else:
                away = c["team"]["displayName"]

        is_home = (home == "Boston Red Sox")
        opponent = away if is_home else home

        pitchers = {"sox": "TBD", "opp": "TBD"}
        for c in competitors:
            is_sox = c["team"]["displayName"] == "Boston Red Sox"
            for p in c.get("probables", []):
                name = p.get("athlete", {}).get("displayName", "TBD")
                pitchers["sox" if is_sox else "opp"] = name

        return {
            "event_id": event["id"],
            "date": str(today),
            "game_time": game_time,
            "opponent": opponent,
            "is_home": is_home,
            "pitchers": pitchers,
            "status_state": event.get("status", {}).get("type", {}).get("state", ""),
        }
    return None


def get_last_completed_game(events):
    """Return the most recently completed game event."""
    today = datetime.now(EASTERN).date()
    completed = []
    for event in events:
        if event.get("status", {}).get("type", {}).get("state", "") != "post":
            continue
        event_date = datetime.fromisoformat(event["date"].replace("Z", "+00:00")).astimezone(EASTERN).date()
        if event_date < today:
            completed.append((event_date, event))
    if not completed:
        return None
    completed.sort(key=lambda x: x[0], reverse=True)
    return completed[0][1]


# ── Box score parsing ─────────────────────────────────────────────────────────

def _safe_int(val):
    try:
        return int(val or 0)
    except (ValueError, TypeError):
        return 0


def parse_boxscore(summary):
    """Extract normalized team and player performance data from a game summary."""
    result = {
        "sox_score": "?", "opp_score": "?", "opponent": "?",
        "won": None,
        "batting": [], "pitching": [],
        "hr_leaders": [],
        "winning_pitcher": None, "losing_pitcher": None, "save_pitcher": None,
    }

    # Scores and winner
    for comp in summary.get("header", {}).get("competitions", []):
        for c in comp.get("competitors", []):
            team_id = c.get("team", {}).get("id", "")
            if team_id == REDSOX_ID:
                result["sox_score"] = c.get("score", "?")
                result["won"] = c.get("winner", False)
            else:
                result["opp_score"] = c.get("score", "?")
                result["opponent"] = c.get("team", {}).get("displayName", "?")

    # Player stats
    for team_box in summary.get("boxscore", {}).get("teams", []):
        if team_box.get("team", {}).get("id") != REDSOX_ID:
            continue
        for category in team_box.get("statistics", []):
            cat = category.get("name", "")
            keys = category.get("keys", [])
            for entry in category.get("athletes", []):
                athlete = entry.get("athlete", {})
                stats = entry.get("stats", [])
                stat_dict = dict(zip(keys, stats))
                player = {
                    "name": athlete.get("displayName", "Unknown"),
                    "position": athlete.get("position", {}).get("abbreviation", ""),
                    **stat_dict,
                }
                if cat == "batting":
                    result["batting"].append(player)
                    hrs = stat_dict.get("HR", stat_dict.get("homeRuns", "0"))
                    if _safe_int(hrs) > 0:
                        result["hr_leaders"].append(f"{player['name']} ({hrs} HR)")
                elif cat == "pitching":
                    result["pitching"].append(player)

    # Winning / losing / save pitchers from game notes
    for note in summary.get("notes", []):
        text = note.get("text", "")
        for segment in text.replace(". ", ",").split(","):
            s = segment.strip().rstrip(".")
            if s.startswith("W:"):
                result["winning_pitcher"] = s[2:].strip()
            elif s.startswith("L:"):
                result["losing_pitcher"] = s[2:].strip()
            elif s.startswith("S:"):
                result["save_pitcher"] = s[2:].strip()

    return result


# ── Email formatting ──────────────────────────────────────────────────────────

def _fmt_time(dt):
    return dt.strftime("%I:%M %p ET").lstrip("0")


def format_recap(boxscore, record):
    outcome = "WIN" if boxscore["won"] else "LOSS"
    lines = [f"Red Sox {outcome} — {boxscore['sox_score']}-{boxscore['opp_score']} vs {boxscore['opponent']}"]
    if record:
        lines.append(f"Record: {record}")
    lines.append("")

    if boxscore["winning_pitcher"]:
        lines.append(f"W: {boxscore['winning_pitcher']}")
    if boxscore["losing_pitcher"]:
        lines.append(f"L: {boxscore['losing_pitcher']}")
    if boxscore["save_pitcher"]:
        lines.append(f"S: {boxscore['save_pitcher']}")
    if any([boxscore["winning_pitcher"], boxscore["losing_pitcher"]]):
        lines.append("")

    if boxscore["hr_leaders"]:
        lines.append("Home Runs: " + ", ".join(boxscore["hr_leaders"]))
        lines.append("")

    # Batting highlights: 2+ hits or 2+ RBI
    highlights = [
        p for p in boxscore["batting"]
        if _safe_int(p.get("H", p.get("hits", "0"))) >= 2
        or _safe_int(p.get("RBI", "0")) >= 2
    ]
    if highlights:
        lines.append("Batting Highlights:")
        for p in highlights[:6]:
            h   = p.get("H",   p.get("hits",         "?"))
            ab  = p.get("AB",  p.get("atBats",        "?"))
            rbi = p.get("RBI", "0")
            hr  = p.get("HR",  p.get("homeRuns",      "0"))
            bb  = p.get("BB",  p.get("baseOnBalls",   "0"))
            detail = f"{h}/{ab}"
            extras = []
            if _safe_int(hr)  > 0: extras.append(f"{hr} HR")
            if _safe_int(rbi) > 0: extras.append(f"{rbi} RBI")
            if _safe_int(bb)  > 0: extras.append(f"{bb} BB")
            if extras:
                detail += ", " + ", ".join(extras)
            lines.append(f"  {p['name']} ({p['position']}): {detail}")
        lines.append("")

    # Starting pitcher line
    if boxscore["pitching"]:
        sp  = boxscore["pitching"][0]
        ip  = sp.get("IP",  sp.get("inningsPitched", "?"))
        h   = sp.get("H",   sp.get("hits",           "?"))
        er  = sp.get("ER",  sp.get("earnedRuns",     "?"))
        k   = sp.get("SO",  sp.get("strikeOuts",     "?"))
        bb  = sp.get("BB",  sp.get("baseOnBalls",    "?"))
        lines.append(f"Starting Pitcher — {sp['name']}: {ip} IP, {h} H, {er} ER, {k} K, {bb} BB")

    return "\n".join(lines)


def format_preview(today_game, record):
    venue = "Home" if today_game["is_home"] else f"@ {today_game['opponent']}"
    lines = [f"Red Sox vs {today_game['opponent']} — {_fmt_time(today_game['game_time'])} ({venue})"]
    if record:
        lines.append(f"Record: {record}")
    lines.append("")
    lines.append(f"Red Sox SP:          {today_game['pitchers']['sox']}")
    lines.append(f"{today_game['opponent']} SP: {today_game['pitchers']['opp']}")
    return "\n".join(lines)


# ── Email send ────────────────────────────────────────────────────────────────

def send_email(subject, body):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = EMAIL_ADDRESS
    msg["To"] = RECIPIENT
    msg.set_content(body)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        smtp.send_message(msg)


# ── Scheduled tasks ───────────────────────────────────────────────────────────

def send_game_start_alert(today_game):
    send_email(
        "Red Sox Are LIVE!",
        f"The Red Sox vs {today_game['opponent']} is starting now!\n"
        f"First pitch: {_fmt_time(today_game['game_time'])}",
    )


def daily_task():
    try:
        data = fetch_schedule()
        events = data.get("events", [])
        record = get_team_record(events)

        sections = []
        subject_tags = []

        # Last completed game recap
        last_game = get_last_completed_game(events)
        if last_game:
            try:
                summary = fetch_summary(last_game["id"])
                boxscore = parse_boxscore(summary)

                store_game({
                    "event_id": last_game["id"],
                    "date": last_game["date"][:10],
                    "opponent": boxscore["opponent"],
                    "sox_score": boxscore["sox_score"],
                    "opp_score": boxscore["opp_score"],
                    "won": boxscore["won"],
                    "batting": boxscore["batting"],
                    "pitching": boxscore["pitching"],
                })

                outcome = "W" if boxscore["won"] else "L"
                subject_tags.append(f"Last: {outcome} {boxscore['sox_score']}-{boxscore['opp_score']}")
                sections.append("=== LAST GAME ===\n" + format_recap(boxscore, record))
            except Exception as e:
                sections.append(f"=== LAST GAME ===\n(Could not load recap: {e})")

        # Today's game preview
        today_game = get_todays_game(events)
        if today_game:
            subject_tags.append(f"Game Today {_fmt_time(today_game['game_time'])}")
            sections.append("=== TODAY'S GAME ===\n" + format_preview(today_game, record))

            now = datetime.now(EASTERN)
            delay = (today_game["game_time"] + timedelta(minutes=5)) - now
            if delay.total_seconds() > 0:
                t = threading.Timer(delay.total_seconds(), send_game_start_alert, args=[today_game])
                t.daemon = True
                t.start()
        else:
            sections.append("=== NO GAME TODAY ===")

        subject = "Red Sox Daily | " + " | ".join(subject_tags) if subject_tags else "Red Sox Daily Update"
        send_email(subject, "\n\n".join(sections))

    except Exception as e:
        send_email("Red Sox Bot — Error", f"daily_task failed:\n{e}")


if "--now" in sys.argv:
    daily_task()
    sys.exit(0)

schedule.every().day.at("13:00").do(daily_task)


# ── Keep-alive server (Render) ────────────────────────────────────────────────

PORT = int(os.environ.get("PORT", 10000))


class KeepAliveHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"alive")

    def log_message(self, *args):
        pass


threading.Thread(
    target=lambda: HTTPServer(("0.0.0.0", PORT), KeepAliveHandler).serve_forever(),
    daemon=True,
).start()

while True:
    schedule.run_pending()
    time.sleep(60)
