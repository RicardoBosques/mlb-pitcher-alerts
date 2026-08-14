import requests
import datetime
import os
import json
import smtplib
from email.mime.text import MIMEText

NTFY_TOPIC = "ricardo-mlb-pitchers-0821"
ODDS_API_KEY = os.environ.get("ODDS_API_KEY")
GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")
SEASON = "2026"
FIP_CONSTANT = 3.10  # approximate league constant; update yearly if you want precision

ODDS_SNAPSHOT_FILE = "opening_odds.json"


def parse_ip(ip_str):
    """MLB reports IP like '5.2' meaning 5 and 2/3 innings — convert to true decimal."""
    if not ip_str:
        return 0.0
    try:
        whole, _, frac = str(ip_str).partition(".")
        whole = int(whole)
        frac = int(frac) if frac else 0
        return whole + (frac / 3.0)
    except ValueError:
        return 0.0


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
        ip = parse_ip(s.get("inningsPitched"))
        hr = s.get("homeRuns", 0)
        bb = s.get("baseOnBalls", 0)
        hbp = s.get("hitBatsmen", 0)
        k = s.get("strikeOuts", 0)
        fip = round(((13 * hr) + (3 * (bb + hbp)) - (2 * k)) / ip + FIP_CONSTANT, 2) if ip > 0 else None
        hr9 = round(hr * 9 / ip, 2) if ip > 0 else None
        return {
            "era": s.get("era"),
            "whip": s.get("whip"),
            "k9": s.get("strikeoutsPer9Inn"),
            "bb9": s.get("walksPer9Inn"),
            "hr9": hr9,
            "fip": fip,
            "ip": ip,
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


def get_last20_and_diff(team_id):
    """Pull team's last 20 completed games, compute record + run differential."""
    end = datetime.date.today()
    start = end - datetime.timedelta(days=35)  # wide window to ensure 20 games are captured
    url = (f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&teamId={team_id}"
           f"&startDate={start.isoformat()}&endDate={end.isoformat()}&hydrate=linescore")
    r = requests.get(url, timeout=15)
    if r.status_code != 200:
        return "N/A", "N/A"
    data = r.json()
    games = []
    for d in data.get("dates", []):
        for g in d.get("games", []):
            if g.get("status", {}).get("detailedState") not in ("Final", "Game Over"):
                continue
            games.append(g)
    games = games[-20:]
    if not games:
        return "N/A", "N/A"
    wins, losses, diff = 0, 0, 0
    for g in games:
        away = g["teams"]["away"]
        home = g["teams"]["home"]
        is_home = home["team"]["id"] == team_id
        team_side = home if is_home else away
        opp_side = away if is_home else home
        team_score = team_side.get("score", 0)
        opp_score = opp_side.get("score", 0)
        diff += (team_score - opp_score)
        if team_side.get("isWinner"):
            wins += 1
        else:
            losses += 1
    return f"{wins}-{losses}", f"{diff:+d}"


def get_bullpen_stats(team_id, starter_id):
    """Approximate bullpen ERA/WHIP by aggregating season stats for active pitchers minus today's starter."""
    url = f"https://statsapi.mlb.com/api/v1/teams/{team_id}/roster/active"
    r = requests.get(url, timeout=15)
    if r.status_code != 200:
        return None
    roster = r.json().get("roster", [])
    pitcher_ids = [p["person"]["id"] for p in roster
                   if p.get("position", {}).get("code") == "1" and p["person"]["id"] != starter_id]

    total_er, total_ip, total_walks_hits = 0.0, 0.0, 0.0
    for pid in pitcher_ids:
        stat_url = f"https://statsapi.mlb.com/api/v1/people/{pid}/stats?stats=season&group=pitching&season={SEASON}"
        sr = requests.get(stat_url, timeout=15)
        if sr.status_code != 200:
            continue
        sdata = sr.json()
        try:
            s = sdata["stats"][0]["splits"][0]["stat"]
        except (IndexError, KeyError):
            continue
        ip = parse_ip(s.get("inningsPitched"))
        if ip == 0:
            continue
        total_ip += ip
        total_er += s.get("earnedRuns", 0)
        total_walks_hits += s.get("baseOnBalls", 0) + s.get("hits", 0)

    if total_ip == 0:
        return None
    bullpen_era = round((total_er * 9) / total_ip, 2)
    bullpen_whip = round(total_walks_hits / total_ip, 2)
    return {"era": bullpen_era, "whip": bullpen_whip}


def get_odds():
    if not ODDS_API_KEY:
        return {}
    url = f"https://api.the-odds-api.com/v4/sports/baseball_mlb/odds/?apiKey={ODDS_API_KEY}&regions=us&markets=h2h,totals&oddsFormat=american"
    r = requests.get(url, timeout=15)
    if r.status_code != 200:
        print(f"Odds API error: {r.status_code} {r.text}")
        return {}
    odds = {}
    for game in r.json():
        key = f"{game['away_team'].lower()}|{game['home_team'].lower()}"
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


def load_opening_odds():
    if os.path.exists(ODDS_SNAPSHOT_FILE):
        with open(ODDS_SNAPSHOT_FILE, "r") as f:
            return json.load(f)
    return {}


def save_opening_odds(snapshot):
    with open(ODDS_SNAPSHOT_FILE, "w") as f:
        json.dump(snapshot, f, indent=2)


def get_pitchers():
    date = datetime.date.today().isoformat()
    url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date}&hydrate=probablePitcher"
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    data = r.json()

    standings = get_standings_data()
    odds = get_odds()
    opening_odds = load_opening_odds()
    opening_updated = False

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
            away_p_name = away_p.get("fullName")
            home_p_name = home_p.get("fullName")

            block = [f"{away_name} ({away_p_name or 'TBD'}) @ {home_name} ({home_p_name or 'TBD'})"]

            for p, p_name, side_name in ((away_p, away_p_name, away_name), (home_p, home_p_name, home_name)):
                if not p_name:
                    block.append(f"  {side_name} starter: TBD")
                    continue
                hand = p.get("pitchHand", {}).get("code", "N/A")
                stats = get_pitcher_stats(p.get("id"))
                l5 = get_last5_era(p.get("id"))
                if stats:
                    block.append(
                        f"  {p_name} ({hand}): ERA {stats['era']} | WHIP {stats['whip']} | "
                        f"K/9 {stats['k9']} | BB/9 {stats['bb9']} | HR/9 {stats['hr9']} | "
                        f"FIP {stats['fip']} | L5 ERA {l5 or 'N/A'}"
                    )
                else:
                    block.append(f"  {p_name} ({hand}): stats N/A")

            a_rec = standings.get(away_id, {})
            h_rec = standings.get(home_id, {})
            block.append(f"  Last 10: {away_name} {a_rec.get('last10','N/A')} | {home_name} {h_rec.get('last10','N/A')}")

            a_l20, a_diff = get_last20_and_diff(away_id)
            h_l20, h_diff = get_last20_and_diff(home_id)
            block.append(f"  Last 20: {away_name} {a_l20} (diff {a_diff}) | {home_name} {h_l20} (diff {h_diff})")

            block.append(f"  Home/Away: {away_name} away {a_rec.get('away','N/A')} | {home_name} home {h_rec.get('home','N/A')}")

            a_bp = get_bullpen_stats(away_id, away_p.get("id"))
            h_bp = get_bullpen_stats(home_id, home_p.get("id"))
            a_bp_str = f"ERA {a_bp['era']} | WHIP {a_bp['whip']}" if a_bp else "N/A"
            h_bp_str = f"ERA {h_bp['era']} | WHIP {h_bp['whip']}" if h_bp else "N/A"
            block.append(f"  Bullpen: {away_name} {a_bp_str} | {home_name} {h_bp_str}")

            odds_key = f"{away_name.lower()}|{home_name.lower()}"
            odds_entry = odds.get(odds_key)
            if odds_entry:
                if odds_key not in opening_odds:
                    opening_odds[odds_key] = odds_entry
                    opening_updated = True
                open_entry = opening_odds.get(odds_key, {})

                ml = odds_entry.get("moneyline", {})
                ml_str = " | ".join(f"{k}: {v:+d}" for k, v in ml.items())
                open_ml = open_entry.get("moneyline", {})
                open_ml_str = " | ".join(f"{k}: {v:+d}" for k, v in open_ml.items()) if open_ml else "N/A"

                total = odds_entry.get("total")
                open_total = open_entry.get("total", "N/A")

                block.append(f"  Odds (current): {ml_str} | O/U {total}")
                block.append(f"  Odds (opening): {open_ml_str} | O/U {open_total}")
            else:
                block.append("  Odds: N/A")

            lines.append("\n".join(block))

    if opening_updated:
        save_opening_odds(opening_odds)

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
