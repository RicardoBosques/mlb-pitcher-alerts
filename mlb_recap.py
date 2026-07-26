def get_results():
    date = datetime.date.today().isoformat()
    url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date}&hydrate=linescore,decisions,boxscore"
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    data = r.json()

    lines = []
    for d in data.get("dates", []):
        for g in d.get("games", []):
            status = g.get("status", {}).get("detailedState", "")
            away = g["teams"]["away"]
            home = g["teams"]["home"]
            away_name = away["team"]["name"]
            home_name = home["team"]["name"]

            if status not in ("Final", "Game Over"):
                lines.append(f"{away_name} @ {home_name} — {status} (not final)")
                continue

            away_score = away.get("score")
            home_score = home.get("score")

            decisions = g.get("decisions", {})
            winner = decisions.get("winner", {}).get("fullName")
            loser = decisions.get("loser", {}).get("fullName")
            save = decisions.get("save", {}).get("fullName")

            block = [f"{away_name} {away_score} @ {home_name} {home_score}  (Final)"]
            if winner:
                wl_line = f"  W: {winner}"
                if loser:
                    wl_line += f"  L: {loser}"
                if save:
                    wl_line += f"  SV: {save}"
                block.append(wl_line)

            # Team totals: hits, walks, LOB, errors
            box = g.get("boxscore", {})
            teams_box = box.get("teams", {})
            for side, name in (("away", away_name), ("home", home_name)):
                team_stats = teams_box.get(side, {}).get("teamStats", {})
                batting = team_stats.get("batting", {})
                fielding = team_stats.get("fielding", {})
                hits = batting.get("hits", "N/A")
                walks = batting.get("baseOnBalls", "N/A")
                lob = batting.get("leftOnBase", "N/A")
                errors = fielding.get("errors", "N/A")
                block.append(f"  {name}: H {hits} | BB {walks} | LOB {lob} | E {errors}")

            # Pitching lines: starters and relievers for both teams
            for side, name in (("away", away_name), ("home", home_name)):
                players = teams_box.get(side, {}).get("players", {})
                pitch_lines = []
                for pid, pdata in players.items():
                    pitching = pdata.get("stats", {}).get("pitching", {})
                    if not pitching:
                        continue
                    p_name = pdata.get("person", {}).get("fullName", "Unknown")
                    ip = pitching.get("inningsPitched", "0")
                    er = pitching.get("earnedRuns", 0)
                    h = pitching.get("hits", 0)
                    bb = pitching.get("baseOnBalls", 0)
                    k = pitching.get("strikeOuts", 0)
                    pitch_lines.append(f"    {p_name}: {ip} IP | {er} ER | {h} H | {bb} BB | {k} K")
                if pitch_lines:
                    block.append(f"  {name} pitching:")
                    block.extend(pitch_lines)

            lines.append("\n".join(block))

    if not lines:
        return f"No MLB games found for {date}."
    date_str = datetime.date.today().isoformat()
    return f"MLB Results Recap — {date_str}\n\n" + "\n\n".join(lines)
