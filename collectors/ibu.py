"""Collect the IBU World Cup calendar, start lists, results and overall standings.

Primary source: the public IBU datacenter endpoint the official site itself loads
(bw.biathlonresults.com). Backup: the same data embedded in biathlonworld.com pages
(`__NEXT_DATA__`), which robots.txt allows. Results and standings go to their own files,
so the broadcast feed never loads them: the app shows them only when asked (no spoilers).
"""

from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import re
from urllib.request import urlopen

from collectors.schedule import content, read_json, utc_text, write_json


API = "https://bw.biathlonresults.com/modules/sportapi/api/"
SITE = "https://www.biathlonworld.com"
# World Cup, World Championships and Olympic Games: the level-1 events of the calendar.
TOP_LEVEL = {"BTSWRLCP", "BTSWRLCH", "BTSWRLOG"}
# Start lists and results are kept for races from a week ago to two weeks ahead.
RACES_BEFORE = timedelta(days=7)
RACES_AFTER = timedelta(days=14)

DISCIPLINES = {
    "IN": "Индивидуальная гонка",
    "SI": "Короткая индивидуальная гонка",
    "SP": "Спринт",
    "PU": "Гонка преследования",
    "MS": "Масс-старт",
    "RL": "Эстафета",
    "SR": "Одиночная смешанная эстафета",
}
GENDERS = {"SM": ("Мужчины", "men"), "SW": ("Женщины", "women"), "MX": ("", "mixed")}
STATES = {
    "START_LIST": "startlist",
    "PROVISIONAL_STARTLIST": "startlist",
    "UNOFFICIAL": "results",
    "OFFICIAL": "results",
}
NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)


def season_id(today: date) -> str:
    """IBU season code: 2026/27 is "2627", running from July to the next June."""
    start = today.year if today.month >= 7 else today.year - 1
    return f"{start % 100:02d}{(start + 1) % 100:02d}"


def read_api(url: str) -> object:
    with urlopen(url, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def read_site(url: str) -> str:
    with urlopen(url, timeout=20) as response:
        return response.read().decode("utf-8")


def page_props(html: str) -> dict:
    found = NEXT_DATA.search(html)
    if found is None:
        raise ValueError("На странице IBU нет встроенных данных")
    return json.loads(found.group(1))["props"]["pageProps"]


def race_title(race: dict) -> str:
    discipline = DISCIPLINES.get(race["DisciplineId"], race["ShortDescription"])
    if race["DisciplineId"] == "RL" and race["catId"] == "MX":
        discipline = "Смешанная эстафета"
    parts = [discipline]
    gender = GENDERS.get(race["catId"], ("", None))[0]
    if gender:
        parts.append(gender)
    if race.get("km"):
        parts.append(f"{race['km']} км")
    return " · ".join(parts)


def race_entry(race: dict) -> dict:
    state = STATES.get(race.get("ResultStatus") or "", "scheduled")
    if (race.get("ScheduleStatus") or "").upper() in ("CANCELLED", "CANCELED"):
        state = "cancelled"
    elif race.get("IsLive"):
        state = "live"
    return {
        "race_id": race["RaceId"],
        "title": race_title(race),
        "discipline": race["DisciplineId"],
        "gender": GENDERS.get(race["catId"], ("", None))[1],
        "km": race.get("km") or None,
        "start_utc": race["StartTime"],
        "state": state,
        "has_startlist": state in ("startlist", "live", "results"),
        "has_results": state == "results",
        "url": f"{SITE}/startlist/{race['RaceId']}",
    }


def race_rows(data: dict) -> dict:
    rows = []
    for row in data.get("Results") or []:
        name = row["Name"] if row.get("IsTeam") else f"{row['FamilyName']} {row['GivenName']}".strip()
        rows.append({
            "bib": row.get("Bib"),
            "name": name,
            "nat": row.get("Nat"),
            "is_team": bool(row.get("IsTeam")),
            "leg": row.get("Leg") or None,
            "start_time": row.get("StartTime"),
            "start_info": row.get("StartInfo"),
            "rank": row.get("Rank"),
            "total_time": row.get("TotalTime"),
            "behind": row.get("Behind"),
            "shootings": row.get("ShootingTotal"),
            "wc_points": row.get("WC"),
            "irm": row.get("IRM"),
        })
    competition = data["Competition"]
    return {
        "race_id": data["RaceId"],
        "title": race_title(competition),
        "start_utc": competition["StartTime"],
        "is_result": bool(data.get("IsResult")),
        "status": competition.get("StatusText"),
        "url": f"{SITE}/startlist/{data['RaceId']}",
        "rows": rows,
    }


def standings(data: dict) -> dict:
    return {
        "cup_id": data["CupId"],
        "name": data.get("CupName"),
        "as_of": data.get("AsOf"),
        "race_count": data.get("RaceCount"),
        "total_races": data.get("TotalRaces"),
        "url": f"{SITE}/standings",
        "rows": [{"rank": row.get("Rank"), "name": f"{row['FamilyName']} {row['GivenName']}".strip(),
                  "nat": row.get("Nat"), "score": row.get("Score"), "ibu_id": row.get("IBUId")}
                 for row in data.get("Rows") or []],
    }


def collect_ibu(today: date, get_json: Callable[[str], object] = read_api,
                get_page: Callable[[str], str] = read_site) -> tuple[dict[str, dict], dict[str, str]]:
    """Files to publish (name → content) and the errors of pieces no source could give."""
    season = season_id(today)
    files: dict[str, dict] = {}
    errors: dict[str, str] = {}
    calendar_page: dict | None = None

    def calendar() -> dict:
        nonlocal calendar_page
        if calendar_page is None:
            calendar_page = page_props(get_page(f"{SITE}/calendar"))
        return calendar_page

    def first_of(*attempts: tuple[str, Callable[[], object]]) -> object | None:
        """Try each source in turn; record the primary one's error if all fail."""
        for _, attempt in attempts:
            try:
                return attempt()
            except (OSError, ValueError, KeyError) as error:
                last = error
        errors[attempts[0][0]] = str(last)
        return None

    events_url = f"{API}Events?SeasonId={season}&Level=1&Language=EN"
    events = first_of((events_url, lambda: get_json(events_url)),
                      ("calendar", lambda: calendar()["eventInitialState"]["events"])) or []
    calendar_events = []
    for event in events:
        if event.get("EventClassificationId", "BTSWRLCP") not in TOP_LEVEL:
            continue
        url = f"{API}Competitions?EventId={event['EventId']}&Language=EN"

        def from_page(event_id=event["EventId"]):
            races = calendar()["competitionsInitialState"]
            if not races or not races[0]["RaceId"].startswith(event_id):
                raise ValueError("Календарь IBU не показывает гонки этого этапа")
            return races
        races = first_of((url, lambda url=url: get_json(url)), ("calendar", from_page)) or []
        calendar_events.append({
            "event_id": event["EventId"],
            "name": event["ShortDescription"],
            "country": event["Nat"],
            "kind": {"BTSWRLCH": "Чемпионат мира", "BTSWRLOG": "Олимпийские игры"}.get(
                event.get("EventClassificationId"), "Кубок мира"),
            "start_date": event["StartDate"][:10],
            "end_date": event["EndDate"][:10],
            "first_race_utc": event.get("FirstCompetitionDate"),
            "races": [race_entry(race) for race in sorted(races, key=lambda race: race["StartTime"])],
        })
    if events:
        files["season.json"] = {"season_id": season, "season": f"20{season[:2]}/20{season[2:]}",
                                "events": calendar_events}

    window_start = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc) - RACES_BEFORE
    window_end = window_start + RACES_BEFORE + RACES_AFTER
    for event in calendar_events:
        for race in event["races"]:
            start = datetime.fromisoformat(race["start_utc"].replace("Z", "+00:00"))
            if not race["has_startlist"] or not window_start <= start < window_end:
                continue
            url = f"{API}Results?RaceId={race['race_id']}&Language=EN"
            page_url = f"{SITE}/startlist/{race['race_id']}"
            data = first_of((url, lambda url=url: get_json(url)),
                            (page_url, lambda page_url=page_url: page_props(get_page(page_url))["startlistInitialState"]))
            if data is not None:
                files[f"races/{race['race_id']}.json"] = race_rows(data)

    tables = {}
    for gender, code in (("men", "SMTS"), ("women", "SWTS")):
        url = f"{API}CupResults?CupId=BT{season}SWRLCP__{code}&Language=EN"
        data = first_of((url, lambda url=url: get_json(url)))
        if data is not None:
            tables[gender] = standings(data)
    if len(tables) == 2:
        files["standings.json"] = tables
    return files, errors


def publish_ibu(out: Path, files: dict[str, dict], errors: dict[str, str], checked_at: datetime) -> bool:
    """Write the files that changed; a piece that failed keeps its last file. Returns whether anything was written."""
    stamp = utc_text(checked_at)
    changed = False
    for name, data in files.items():
        path = out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if content(data) != content(read_json(path)):
            write_json(path, data)
            changed = True
    previous = read_json(out / "status.json") or {}
    status = {"last_success": stamp if not errors else previous.get("last_success"),
              "stale": bool(errors), "errors": errors}
    if changed or content(status) != content(previous or None):
        out.mkdir(parents=True, exist_ok=True)
        write_json(out / "status.json", status)
        return True
    return False
