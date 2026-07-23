import requests
import datetime
import os
import smtplib
from email.mime.text import MIMEText

NTFY_TOPIC = "ricardo-mlb-pitchers-0821"
ODDS_API_KEY = os.environ.get("ODDS_API_KEY")
GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")
SEASON = "2026"


def get_pitcher_stats(pitcher_id):
    if not pitcher_id:
        return None
    url = f"https://statsapi.mlb.com/api/v1/people/{pitcher_id}/stats?stats=season&group=pitching&season={SEASON}"
    r = requests.get(url, timeout=15)
    if r.status_code != 200:
        return None
    data = r.json()
    try:
        s = data["stats"][0]["splits"][0]["stat"]
        return {
            "era": s.get("era"),
            "whip": s.get("whip"),
            "k9": s.get("strikeoutsPer9Inn"),
            "bb9": s.get("walksPer9Inn"),
        }
    except (IndexError, KeyError):
        return None


def get_last5_era(pitcher_id):
    if not pitcher_id:
        return None
    url = f"https://statsapi.mlb.com/api/v1/people/{pitcher_id}/stats?stats=lastXGames&limit=5&group=pitching"
    r = requests.get(url, timeout=15)
    if r.status_code != 200:
        return None
    data = r.json()
    try:
        return data["stats"][0]["splits"][0]["stat"].get("era")
    except (IndexError, KeyError):
        return None


def get_standings_data():
    """Returns dict of teamId -> {'last10': str, 'home': str, 'away': str}"""
    url = f"https://statsapi.mlb.com/api/v1/standings?leagueId=103,104&season={SEASON}&standingsTypes=regularSeason"
    r = requests.get(url, timeout=15)
    result = {}
    if r.status_code != 200:
        return result
    data = r.json()
    for record_group in data.get("records", []):
        for team in record_group.get("teamRecords", []):
            splits = team.get("records", {}).get("splitRecords", [])
            def find(t):
                return next((f"{x['wins']}-{x['losses']}" for x in splits if x.get("type") == t), "N/A")
            result[team["team"]["id"]] = {
                "last10": find("lastTen"),
                "home": find("home"),
                "away": find("away"),
            }
    return result


def get_ops_vs_hand(team_id, hand):
    """hand = 'vl' (vs lefty) or 'vr' (vs righty)"""
    url = f"https://statsapi.mlb.com/api/v1/teams/{team_id}/stats?stats=statSplits&group=hitting&sitCodes={hand}&season={SEASON}"
    r = requests.get(url, timeout=15)
    if r.status_code != 200:
        return None
    data = r.json()
    try:
        return data["stats"][0]["splits"][0]["stat"].get("ops")
    except (IndexError, KeyError):
        return None


def get_odds():
    """Returns dict keyed by (away_team_lower, home_team_lower) -> {'moneyline': {...}, 'total': ...}"""
    if not ODDS_API_KEY:
        return {}
    url = f"https://api.the-odds-api.com/v4/sports/baseball_mlb/odds/?apiKey={ODDS_API_KEY}&regions=us&markets=h2h,totals&oddsFormat=american"
    r = requests.get(url, timeout=15)
    if r.status_code != 200:
        print(f"Odds API error: {r.status_code} {r.text}")
        return {}
    odds = {}
    for game in r.json():
        key = (game["away_team"].lower(), game["home_team"].lower())
        if not game.get("bookmakers"):
            continue
        book = game["bookmakers"][0]
        entry = {}
        for market in book.get("markets", []):
            if market["key"] == "h2h":
                entry["moneyline"] = {o["name"]: o["price"] for o in market["outcomes"]}
            elif market["key"] == "totals":
                entry["total"] = market["outcomes"][0].get("point")
        odds[key] = entry
    return odds


def get_pitchers():
    date = datetime.date.today().isoformat()
    url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date}&hydrate=probablePitcher"
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    data = r.json()

    standings = get_standings_data()
    odds = get_odds()

    lines = []
    for d in data.get("dates", []):
        for g in d.get("games", []):
            away = g["teams"]["away"]
            home = g["teams"]["home"]
            away_name = away["team"]["name"]
            home_name = home["team"]["name"]
            away_id = away["team"]["id"]
            home_id = home["team"]["id"]
            away_p = away.get("probablePitcher", {})
            home_p = home.get("probablePitcher", {})
            away_p_name = away_p.get("fullName", "TBD")
            home_p_name = home_p.get("fullName", "TBD")

            block = [f"{away_name} ({away_p_name}) @ {home_name} ({home_p_name})"]

            a_stats = get_pitcher_stats(away_p.get("id"))
            h_stats = get_pitcher_stats(home_p.get("id"))
            a_l5 = get_last5_era(away_p.get("id"))
            h_l5 = get_last5_era(home_p.get("id"))
            if a_stats:
                block.append(f"  {away_p_name}: ERA {a_stats['era']} | WHIP {a_stats['whip']} | K/9 {a_stats['k9']} | L5 ERA {a_l5}")
            if h_stats:
                block.append(f"  {home_p_name}: ERA {h_stats['era']} | WHIP {h_stats['whip']} | K/9 {h_stats['k9']} | L5 ERA {h_l5}")

            a_rec = standings.get(away_id, {})
            h_rec = standings.get(home_id, {})
            block.append(f"  Last 10: {away_name} {a_rec.get('last10','N/A')} | {home_name} {h_rec.get('last10','N/A')}")
            block.append(f"  Home/Away: {away_name} away {a_rec.get('away','N/A')} | {home_name} home {h_rec.get('home','N/A')}")

            away_p_hand = away_p.get("pitchHand", {}).get("code")
            home_p_hand = home_p.get("pitchHand", {}).get("code")
            if away_p_hand:
                sit = "vl" if away_p_hand == "L" else "vr"
                home_ops = get_ops_vs_hand(home_id, sit)
                if home_ops:
                    block.append(f"  {home_name} OPS vs {'LHP' if away_p_hand=='L' else 'RHP'}: {home_ops}")
            if home_p_hand:
                sit = "vl" if home_p_hand == "L" else "vr"
                away_ops = get_ops_vs_hand(away_id, sit)
                if away_ops:
                    block.append(f"  {away_name} OPS vs {'LHP' if home_p_hand=='L' else 'RHP'}: {away_ops}")

            odds_entry = odds.get((away_name.lower(), home_name.lower()))
            if odds_entry:
                ml = odds_entry.get("moneyline", {})
                total = odds_entry.get("total")
                ml_str = " | ".join(f"{k}: {v:+d}" for k, v in ml.items())
                block.append(f"  Odds: {ml_str} | O/U {total}")

            lines.append("\n".join(block))

    if not lines:
        return f"No MLB games found for {date}."
    return f"MLB Probable Pitchers & Stats — {date}\n\n" + "\n\n".join(lines)


def send_notification(message):
    resp = requests.post(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=message.encode("utf-8"),
        headers={"Title": "MLB Pitchers & Stats", "Priority": "default"},
        timeout=15,
    )
    print(f"ntfy response status: {resp.status_code}")
    resp.raise_for_status()


def send_email(message, subject):
    msg = MIMEText(message)
    msg["Subject"] = subject
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = GMAIL_ADDRESS
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_ADDRESS, GMAIL_ADDRESS, msg.as_string())


if __name__ == "__main__":
    msg = get_pitchers()
    print(msg)
    date = datetime.date.today().isoformat()
    send_email(msg, f"MLB Pitchers & Stats — {date}")
    send_notification(f"Today's MLB report is ready — check email ({date})")
