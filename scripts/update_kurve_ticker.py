#!/usr/bin/env python3
"""
KURVE-Ticker-Update: holt die naechsten realen Partien (WM 2026, danach
Bundesliga/CL) von der oeffentlichen ESPN-API und schreibt kurve/data/ticker.json.
Quoten sind fiktiv (KURVE ist eine fiktive Marke), aber plausibel und
deterministisch pro Paarung, damit sie nicht bei jedem Lauf springen.

Laeuft ohne API-Key, nur Python-Stdlib. Bei API-Fehler bleibt die bestehende
ticker.json unveraendert (Fallback: letzter bekannter Stand).
"""
import hashlib
import json
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")
OUT = Path(__file__).resolve().parent.parent / "kurve" / "data" / "ticker.json"

# Wettbewerbe in Prioritaetsreihenfolge (ESPN-Slugs)
COMPETITIONS = [
    ("fifa.world", "WM 2026"),
    ("ger.1", "Bundesliga"),
    ("uefa.champions", "Champions League"),
]

# Wie viele Tage nach vorn geschaut wird
LOOKAHEAD_DAYS = 14
NUM_ITEMS = 3

ROUND_DE = {
    "Round of 32": "Sechzehntelfinale",
    "Round of 16": "Achtelfinale",
    "Quarterfinals": "Viertelfinale",
    "Quarterfinal": "Viertelfinale",
    "Semifinals": "Halbfinale",
    "Semifinal": "Halbfinale",
    "3rd Place Game": "Spiel um Platz 3",
    "Third Place": "Spiel um Platz 3",
    "Final": "Finale",
    "Group Stage": "Gruppenphase",
    "Regular Season": "",
}

TEAM_DE = {
    "Spain": "Spanien", "Belgium": "Belgien", "Norway": "Norwegen",
    "England": "England", "Argentina": "Argentinien", "Switzerland": "Schweiz",
    "France": "Frankreich", "Germany": "Deutschland", "Brazil": "Brasilien",
    "Portugal": "Portugal", "Netherlands": "Niederlande", "Italy": "Italien",
    "Croatia": "Kroatien", "Morocco": "Marokko", "Mexico": "Mexiko",
    "United States": "USA", "USA": "USA", "Canada": "Kanada",
    "Japan": "Japan", "South Korea": "Suedkorea", "Australia": "Australien",
    "Egypt": "Aegypten", "Ghana": "Ghana", "Colombia": "Kolumbien",
    "Cape Verde": "Kap Verde", "Uruguay": "Uruguay", "Ecuador": "Ecuador",
    "Senegal": "Senegal", "Denmark": "Daenemark", "Austria": "Oesterreich",
    "Poland": "Polen", "Scotland": "Schottland", "Turkey": "Tuerkei",
    "Bayern Munich": "FC Bayern", "Borussia Dortmund": "Dortmund",
    "Borussia Monchengladbach": "Gladbach", "Bayer Leverkusen": "Leverkusen",
    "RB Leipzig": "RB Leipzig", "Eintracht Frankfurt": "Frankfurt",
    "VfB Stuttgart": "Stuttgart", "SC Freiburg": "Freiburg",
    "1. FC Union Berlin": "Union Berlin", "FC Cologne": "1. FC Koeln",
    "1. FC Koln": "1. FC Koeln", "TSG Hoffenheim": "Hoffenheim",
    "Werder Bremen": "Werder Bremen", "VfL Wolfsburg": "Wolfsburg",
    "Hamburg SV": "HSV", "FC St. Pauli": "St. Pauli", "Mainz": "Mainz 05",
    "FC Augsburg": "Augsburg", "1. FC Heidenheim": "Heidenheim",
}

WEEKDAY_DE = ["Mo.", "Di.", "Mi.", "Do.", "Fr.", "Sa.", "So."]


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def de_name(name):
    return TEAM_DE.get(name, name)


def de_round(event, data, league_label):
    note = event.get("season", {}).get("slug") or ""
    alt = event.get("competitions", [{}])[0].get("altGameNote") or ""
    stype = (data.get("season") or {}).get("type")
    # season.type kann dict oder int sein, je nach Liga
    tname = ""
    if isinstance(stype, dict):
        tname = stype.get("name", "")
    for src in (alt.split(", ")[-1] if alt else "", tname, note):
        if src in ROUND_DE:
            r = ROUND_DE[src]
            return r if r else league_label
        if src:
            for en, de in ROUND_DE.items():
                if en.lower() in src.lower():
                    return de if de else league_label
    return league_label


def fictional_quote(home_abbr, away_abbr, kickoff_iso):
    """Deterministisch-plausible fiktive Quote pro Paarung."""
    seed = int(hashlib.sha256(f"{home_abbr}{away_abbr}{kickoff_iso}".encode()).hexdigest(), 16)
    market = seed % 3
    if market == 0:
        fav = home_abbr if (seed >> 8) % 2 == 0 else away_abbr
        odd = 1.55 + ((seed >> 16) % 130) / 100.0  # 1.55 - 2.84
        return f"Sieg {fav}", odd
    if market == 1:
        odd = 1.55 + ((seed >> 16) % 60) / 100.0   # 1.55 - 2.14
        return "Über 2,5 Tore", odd
    odd = 1.70 + ((seed >> 16) % 40) / 100.0       # 1.70 - 2.09
    return "Beide treffen", odd


def collect():
    now = datetime.now(timezone.utc)
    date_range = f"{now.strftime('%Y%m%d')}-{(now + timedelta(days=LOOKAHEAD_DAYS)).strftime('%Y%m%d')}"
    items = []
    for slug, label in COMPETITIONS:
        if len(items) >= NUM_ITEMS:
            break
        url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard?dates={date_range}"
        try:
            data = fetch_json(url)
        except Exception as e:
            print(f"WARN: {slug} nicht erreichbar: {e}", file=sys.stderr)
            continue
        events = data.get("events", [])
        upcoming = []
        for ev in events:
            state = ev.get("status", {}).get("type", {}).get("state", "")
            if state != "pre":
                continue
            comp = ev.get("competitions", [{}])[0]
            teams = comp.get("competitors", [])
            if len(teams) != 2:
                continue
            home = next((t for t in teams if t.get("homeAway") == "home"), teams[0])
            away = next((t for t in teams if t.get("homeAway") == "away"), teams[1])
            kickoff = datetime.strptime(ev["date"], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
            upcoming.append((kickoff, ev, home, away, label, data))
        upcoming.sort(key=lambda x: x[0])
        for kickoff, ev, home, away, label, data in upcoming:
            if len(items) >= NUM_ITEMS:
                break
            local = kickoff.astimezone(BERLIN)
            when = f"{WEEKDAY_DE[local.weekday()]}, {local.strftime('%H:%M')}"
            rnd = de_round(ev, data, label)
            market, odd = fictional_quote(
                home.get("team", {}).get("abbreviation", "HEIM"),
                away.get("team", {}).get("abbreviation", "GAST"),
                ev["date"],
            )
            items.append({
                "teams": f"{de_name(home.get('team', {}).get('displayName', ''))} vs. "
                         f"{de_name(away.get('team', {}).get('displayName', ''))}",
                "match": f"{rnd} · {when}",
                "quote": f"{market}  {odd:.2f}",
            })
    return items


def main():
    items = collect()
    if len(items) == 0:
        print("Keine kommenden Partien gefunden - ticker.json bleibt unveraendert.")
        return 0
    payload = {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "items": items,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"OK: {len(items)} Partien -> {OUT}")
    for it in items:
        print(f"  {it['teams']} | {it['match']} | {it['quote']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
