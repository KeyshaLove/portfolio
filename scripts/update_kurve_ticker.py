#!/usr/bin/env python3
"""
KURVE-Ticker-Update: holt die naechsten realen Partien (Bundesliga, danach
Champions League) von der oeffentlichen OpenLigaDB-API und schreibt
kurve/data/ticker.json.

Quoten sind fiktiv (KURVE ist eine fiktive Marke) - OpenLigaDB liefert keine
Wettquoten, deshalb werden sie hier deterministisch pro Paarung berechnet
(leichter Heimvorteil statt Zufall), damit sie nicht bei jedem Lauf springen.

Laeuft ohne API-Key, nur Python-Stdlib. Bei API-Fehler bleibt die bestehende
ticker.json unveraendert (Fallback: letzter bekannter Stand).

Vorgaenger-Version nutzte ESPNs Hidden-API (site.api.espn.com), die ab
August 2026 pauschal mit 403/Access Denied blockt (Akamai) - nicht nur fuer
diesen Runner, sondern auch von privaten Netzen aus getestet. Deshalb Wechsel
auf OpenLigaDB (offizielle, kostenlose Datenquelle fuer deutschen Fussball).
"""
import hashlib
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")
OUT = Path(__file__).resolve().parent.parent / "kurve" / "data" / "ticker.json"

# Wettbewerbe in Prioritaetsreihenfolge (OpenLigaDB-Kuerzel).
COMPETITIONS = [
    ("bl1", "Bundesliga"),
    ("ucl2026", "Champions League"),
]

NUM_ITEMS = 4
# Wie oft pro Wettbewerb der naechste Spieltag nachgefragt wird, falls der
# aktuelle schon komplett durch ist (z.B. Montag/Dienstag nach Spieltag-Ende).
MAX_GROUP_LOOKAHEAD = 4

# Erkennt Platzhalter-Teams vor K.o.-Runden (z.B. "Sieger Achtelfinale 1",
# "TBD") - solche Partien werden ohne Teams und ohne Quote angezeigt, nur
# Runde + Datum/Uhrzeit.
PLACEHOLDER_RE = re.compile(r"winner|loser|tbd|sieger|verlierer", re.IGNORECASE)

# Reguläre Spieltage ("3. Spieltag", "Ligaphase") zeigen den Wettbewerbsnamen;
# alles andere (Achtelfinale, Viertelfinale, ...) ist bereits auf Deutsch und
# wird unveraendert uebernommen.
REGULAR_MATCHDAY_RE = re.compile(r"^\d+\.\s*Spieltag$|^Ligaphase$", re.IGNORECASE)

# OpenLigaDB-Teamname -> kurzer Anzeigename im Ticker (ASCII-Stil, wie im
# Rest der Seite: oe/ae/ue statt Umlaut).
TEAM_DE = {
    "FC Augsburg": "Augsburg",
    "SC Freiburg": "Freiburg",
    "Borussia Dortmund": "Dortmund",
    "SV 07 Elversberg": "Elversberg",
    "1. FSV Mainz 05": "Mainz 05",
    "FC Bayern München": "FC Bayern",
    "FC Schalke 04": "Schalke",
    "Bayer 04 Leverkusen": "Leverkusen",
    "RB Leipzig": "RB Leipzig",
    "VfB Stuttgart": "Stuttgart",
    "SV Werder Bremen": "Werder Bremen",
    "1. FC Köln": "1. FC Koeln",
    "SC Paderborn 07": "Paderborn",
    "Eintracht Frankfurt": "Frankfurt",
    "1. FC Union Berlin": "Union Berlin",
    "TSG Hoffenheim": "Hoffenheim",
    "Borussia Mönchengladbach": "Gladbach",
    "Hamburger SV": "HSV",
}

# Kurzform -> Abkuerzung fuer "Sieg XXX" in der Quote.
TEAM_ABBR = {
    "Augsburg": "FCA", "Freiburg": "SCF", "Dortmund": "BVB",
    "Elversberg": "ELV", "Mainz 05": "M05", "FC Bayern": "FCB",
    "Schalke": "S04", "Leverkusen": "B04", "RB Leipzig": "RBL",
    "Stuttgart": "VFB", "Werder Bremen": "SVW", "1. FC Koeln": "KOE",
    "Paderborn": "SCP", "Frankfurt": "SGE", "Union Berlin": "FCU",
    "Hoffenheim": "TSG", "Gladbach": "BMG", "HSV": "HSV",
    # Bekannte Champions-League-Klubs, damit die Quote nicht mit vollem
    # Namen ("Sieg Real Madrid") sondern als Kuerzel erscheint.
    "Real Madrid": "RMA", "Manchester City": "MCI", "Atletico Madrid": "ATM",
    "FC Liverpool": "LIV", "Manchester United": "MUN", "FC Arsenal": "ARS",
    "FC Chelsea": "CHE", "Paris Saint-Germain": "PSG", "FC Barcelona": "BAR",
    "Inter Mailand": "INT", "AC Mailand": "ACM", "Juventus Turin": "JUV",
}

WEEKDAY_DE = ["Mo.", "Di.", "Mi.", "Do.", "Fr.", "Sa.", "So."]


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def de_name(name):
    return TEAM_DE.get(name, name)


def abbr(display_name, full_name):
    if display_name in TEAM_ABBR:
        return TEAM_ABBR[display_name]
    if full_name in TEAM_ABBR:
        return TEAM_ABBR[full_name]
    # Fallback: erste drei Buchstaben des ersten "eigenstaendigen" Worts.
    words = [w for w in re.findall(r"[A-Za-zÄÖÜäöüß]+", display_name)]
    if not words:
        return "GAST"
    return words[0][:3].upper()


def de_round(group_name, league_label):
    if not group_name or REGULAR_MATCHDAY_RE.match(group_name.strip()):
        return league_label
    return group_name


def fictional_quote(home_disp, away_disp, kickoff_iso):
    """Fiktive Quote pro Paarung, deterministisch (nicht zufaellig bei jedem
    Lauf), mit leichtem Heimvorteil - OpenLigaDB liefert keine echten
    Wettquoten, anders als frueher ESPN."""
    seed = int(hashlib.sha256(f"{home_disp}{away_disp}{kickoff_iso}".encode()).hexdigest(), 16)
    jitter_bucket = (seed >> 16) % 100
    market = seed % 3

    if market == 0:
        fav = home_disp
        odd = round(1.65 + (jitter_bucket % 90) / 100.0, 2)  # 1.65 - 2.54
        return f"Sieg {abbr(fav, fav)}", odd

    if market == 1:
        odd = round(1.55 + (jitter_bucket % 60) / 100.0, 2)  # 1.55 - 2.14
        return "Über 2,5 Tore", odd

    odd = round(1.70 + (jitter_bucket % 40) / 100.0, 2)  # 1.70 - 2.09
    return "Beide treffen", odd


def fetch_matchday(shortcut, season=None, group=None):
    if season is not None and group is not None:
        url = f"https://api.openligadb.de/getmatchdata/{shortcut}/{season}/{group}"
    else:
        url = f"https://api.openligadb.de/getmatchdata/{shortcut}"
    return fetch_json(url)


def upcoming_matches(shortcut, needed):
    """Liefert bis zu `needed` kommende (nicht beendete) Partien, sortiert
    nach Anstoss. Wenn der aktuell laufende Spieltag schon durch ist, wird
    automatisch der naechste Spieltag nachgeladen."""
    try:
        matches = fetch_matchday(shortcut)
    except Exception as e:
        print(f"WARN: {shortcut} nicht erreichbar: {e}", file=sys.stderr)
        return []
    if not matches:
        return []

    season = matches[0]["leagueSeason"]
    group_order = matches[0]["group"]["groupOrderID"]
    collected = [m for m in matches if not m.get("matchIsFinished")]

    lookahead = 0
    while len(collected) < needed and lookahead < MAX_GROUP_LOOKAHEAD:
        group_order += 1
        lookahead += 1
        try:
            more = fetch_matchday(shortcut, season, group_order)
        except Exception as e:
            print(f"WARN: {shortcut} Spieltag {group_order} nicht erreichbar: {e}", file=sys.stderr)
            break
        if not more:
            break
        collected += [m for m in more if not m.get("matchIsFinished")]

    collected.sort(key=lambda m: m["matchDateTimeUTC"])
    return collected[:needed]


def collect():
    items = []
    for shortcut, label in COMPETITIONS:
        remaining = NUM_ITEMS - len(items)
        if remaining <= 0:
            break
        for m in upcoming_matches(shortcut, remaining):
            kickoff = datetime.strptime(m["matchDateTimeUTC"], "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
            local = kickoff.astimezone(BERLIN)
            when = f"{WEEKDAY_DE[local.weekday()]}, {local.strftime('%H:%M')}"
            rnd = de_round(m.get("group", {}).get("groupName", ""), label)

            home_full = (m.get("team1") or {}).get("teamName") or ""
            away_full = (m.get("team2") or {}).get("teamName") or ""

            if (
                not home_full
                or not away_full
                or PLACEHOLDER_RE.search(home_full)
                or PLACEHOLDER_RE.search(away_full)
            ):
                items.append({
                    "teams": rnd,
                    "match": f"{WEEKDAY_DE[local.weekday()]}, {local.strftime('%d.%m.')} · {local.strftime('%H:%M')} Uhr",
                    "quote": "",
                })
                continue

            home_disp = de_name(home_full)
            away_disp = de_name(away_full)
            market, odd = fictional_quote(home_disp, away_disp, m["matchDateTimeUTC"])
            items.append({
                "teams": f"{home_disp} vs. {away_disp}",
                "match": f"{rnd} · {when}",
                "quote": f"{market}  {odd:.2f}",
            })
            if len(items) >= NUM_ITEMS:
                break
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
