import requests
import datetime
import os

NTFY_TOPIC = "ricardo-mlb-pitchers-0821"
ODDS_API_KEY = os.environ.get("ODDS_API_KEY")

def get_pitcher_stats(pitcher_id):
    if not pitcher_id:
        return None
    url = f"https://statsapi.mlb.com/api/v1/people/{pitcher_id}/stats?stats=season&group=pitching&season=2026"
    r = requests.get(url, timeout=15)
    if r.status_code != 200:
        return None
    data = r.json()
    try:
        s = data["stats"][0]["splits"][0]["stat"]
        return {"era": s.get("era"), "whip": s.get("whip"), "k9": s.get("strikeoutsPer9Inn"), "bb9": s.get("walksPer9Inn")}
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

def get_team_last10(team_id):
    url = f"https://statsapi.mlb.com/api/v1/teams/{team_id}/stats?stats=lastXGames&limit=10&group=hitting&season=2026"
    r = requests.get(url, timeout=15)
    if r.status_code != 200:
        return None
    # last10 record isn't directly in hitting stats; pull from standings instead
    return None  # placeholder, see get_standings_records below

def get_standings_records():
    """Returns dict of teamId -> last10 record string, e.g. '7-3'"""
    url = "https://statsapi.mlb.com/api/v1/standings?leagueId=103,104&season=2026&standingsTypes=regularSeason"
    r = requests.get(url, timeout=15)
    records = {}
    if r.status_code != 200:
        return records
    data = r.json()
    for record_group in data.get("records", []):
        for team in record_group.get("teamRecords", []):
            l10 = team.get("records", {}).get("splitRecords", [])
            l10_str = next((f"{x['wins']}-{x['losses']}" for x in l10 if x.get("type") == "lastTen"), None)
            records[team["team"]["id"]] = l10_str
    return records

def get_odds():
    """Returns dict keyed by (away_team, home_team) lowercase -> {'moneyline': {...}, 'total': ...}"""
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
        book = game["bookmakers"][0]  # first available book
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

    last10 = get_standings_records()
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

            a_l10 = last10.get(away_id, "N/A")
            h_l10 = last10.get(home_id, "N/A")
            block.append(f"  Last 10: {away_name} {a_l10} | {home_name} {h_l10}")

            odds_entry = odds.get((away_name.lower(), home_name.lower()))
            if odds_entry:
                ml = odds_entry.get("moneyline", {})
                total = odds_entry.get("total")
                ml_str = " | ".join(f"{k}: {v:+d}" for k, v in ml.items())
                block.append(f"  Odds: {ml_str} | O/U {total}")

            lines.append("\n".join(block))

    if not lines:
        return f"No MLB games found for {date}."
    return f"MLB Probable Pitchers & Odds — {date}\n\n" + "\n\n".join(lines)

def send_notification(message):
    resp = requests.post(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=message.encode("utf-8"),
        headers={"Title": "MLB Pitchers & Odds", "Priority": "default"},
        timeout=15,
    )
    print(f"ntfy response status: {resp.status_code}")
    resp.raise_for_status()

if __name__ == "__main__":
    msg = get_pitchers()
    print(msg)
    send_notification(msg)
