import requests
import datetime

NTFY_TOPIC = "ricardo-mlb-pitchers-0821"  # change to your topic

def get_pitchers():
    date = datetime.date.today().isoformat()
    url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date}&hydrate=probablePitcher"
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    data = r.json()

    lines = []
    for d in data.get("dates", []):
        for g in d.get("games", []):
            away = g["teams"]["away"]
            home = g["teams"]["home"]
            away_name = away["team"]["name"]
            home_name = home["team"]["name"]
            away_p = away.get("probablePitcher", {}).get("fullName", "TBD")
            home_p = home.get("probablePitcher", {}).get("fullName", "TBD")
            local_time = g.get("gameDate", "")
            lines.append(f"{away_name} ({away_p}) @ {home_name} ({home_p})")

    if not lines:
        return f"No MLB games found for {date}."
    return f"MLB Probable Pitchers — {date}\n\n" + "\n".join(lines)

def send_notification(message):
    requests.post(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=message.encode("utf-8"),
        headers={"Title": "MLB Probable Pitchers", "Priority": "default"},
        timeout=15,
    )

if __name__ == "__main__":
    msg = get_pitchers()
    print(msg)
    send_notification(msg)
