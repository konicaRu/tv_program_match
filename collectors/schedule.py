"""Group confirmed Match airings into race events and publish schedule JSON."""

import argparse
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

from collectors.match_guide import MOSCOW, SHOWS, collect_guide, read_page
from collectors.match_tv import Broadcast, collect_live
from collectors.okko_telegram import channel_url, collect_okko


SCHEDULE_VERSION = 1
# Airings of one race name further apart than this belong to different stages.
EVENT_WINDOW = timedelta(days=3)

DISCIPLINES = {
    "спринт": ("Спринт", "sprint"),
    "суперспринт": ("Суперспринт", "super-sprint"),
    "гонка преследования": ("Гонка преследования", "pursuit"),
    "пасьют": ("Гонка преследования", "pursuit"),
    "индивидуальная гонка": ("Индивидуальная гонка", "individual"),
    "короткая индивидуальная гонка": ("Короткая индивидуальная гонка", "short-individual"),
    "масс-старт": ("Масс-старт", "mass-start"),
    "эстафета": ("Эстафета", "relay"),
}
MIXED_DISCIPLINES = {
    "смешанная эстафета": ("Смешанная эстафета", "mixed-relay"),
    "одиночная смешанная эстафета": ("Одиночная смешанная эстафета", "single-mixed-relay"),
}
GENDERS = {"мужчины": "men", "женщины": "women"}
INTERNATIONAL = ("кубок мира", "чемпионат мира", "кубок ibu", "чемпионат европы", "олимпийские игры")
RUSSIAN = ("кубок содружества", "кубок россии", "чемпионат россии", "спартакиада", "золотая лига")

TRANSLIT = dict(zip(
    "абвгдеёжзийклмнопрстуфхцчшщыэюя",
    "a b v g d e e zh z i y k l m n o p r s t u f kh ts ch sh shch y e yu ya".split(),
)) | {"ъ": "", "ь": ""}


@dataclass(frozen=True)
class Race:
    competition: str
    discipline: str
    discipline_slug: str
    gender: str
    category: str


def parse_race(title: str) -> Race | None:
    """Read a race from a TV title, or None when the title is not specific enough."""
    parts = [part.strip() for part in title.split(". ")]
    if len(parts) < 3 or parts[0].casefold() != "биатлон":
        return None
    for index, part in enumerate(parts[2:], start=2):
        key = part.casefold()
        if key in MIXED_DISCIPLINES:
            discipline, slug = MIXED_DISCIPLINES[key]
            gender = "mixed"
        elif key in DISCIPLINES and index + 1 < len(parts) and parts[index + 1].casefold() in GENDERS:
            discipline, slug = DISCIPLINES[key]
            gender = GENDERS[parts[index + 1].casefold()]
        else:
            continue
        competition = ". ".join(parts[1:index])
        label = competition.casefold()
        category = ("international" if any(name in label for name in INTERNATIONAL)
                    else "russian" if any(name in label for name in RUSSIAN) else "unknown")
        return Race(competition, discipline, slug, gender, category)
    return None


def slugify(text: str) -> str:
    latin = "".join(TRANSLIT.get(char, char) for char in text.casefold())
    return re.sub(r"[^a-z0-9]+", "-", latin.replace("-", " ")).strip("-")


def build_schedule(broadcasts: list[Broadcast], checked_at: datetime) -> dict:
    stamp = utc_text(checked_at)
    events: list[dict] = []
    open_events: dict[Race | str, tuple[datetime, dict]] = {}
    unmatched = []
    for row in sorted(broadcasts, key=lambda row: row.start_utc):
        item = {**asdict(row), "verified_at": stamp}
        race = parse_race(row.title)
        key = race or (row.title if row.title.casefold() in SHOWS else None)
        if key is None:
            unmatched.append(item)
            continue
        start = datetime.fromisoformat(row.start_utc)
        first_start, event = open_events.get(key, (None, None))
        if event is None or start - first_start > EVENT_WINDOW:
            day = start.astimezone(MOSCOW).date().isoformat()
            event = race_event(race, day) if race else show_event(row.title, day)
            first_start = start
            open_events[key] = (first_start, event)
            events.append(event)
        event["broadcasts"].append(item)
    return {"schedule_version": SCHEDULE_VERSION, "generated_at": stamp, "events": events, "unmatched": unmatched}


def race_event(race: Race, day: str) -> dict:
    return {
        "event_id": f"{slugify(race.competition)}-{race.discipline_slug}-{race.gender}-{day}",
        "kind": "race",
        "title": f"{race.competition}. {race.discipline}",
        "competition": race.competition,
        "discipline": race.discipline,
        "gender": race.gender,
        "category": race.category,
        "broadcasts": [],
    }


def show_event(title: str, day: str) -> dict:
    return {
        "event_id": f"{slugify(title)}-{day}",
        "kind": "show",
        "title": title,
        "competition": None,
        "discipline": None,
        "gender": None,
        "category": "show",
        "broadcasts": [],
    }


@dataclass
class SourceRun:
    """One source's result in this run. A source with errors keeps its last stored airings."""
    name: str
    broadcasts: list[Broadcast]
    errors: dict[str, str] = field(default_factory=dict)
    unpublished: list[str] = field(default_factory=list)


# Where each source's airings point, to recover them from a schedule.json written before
# sources were stored separately.
SOURCE_URLS = {
    "match_guide": "https://matchtv.ru/tvguide/",
    "match_live": "https://matchtv.ru/biathlon/live",
    "okko_telegram": "https://t.me/okkowintersport/",
    "okko_sport": "https://t.me/okkosport/",
}
OKKO_CHANNELS = {"okko_telegram": "okkowintersport", "okko_sport": "okkosport"}


def merge(first: dict, second: dict) -> dict:
    """The same airing from two sources: keep the first, fill what it lacks from the second."""
    merged = dict(first)
    if merged["type"] == "unknown":
        merged["type"] = second["type"]
    for key in ("end_utc", "url"):
        merged[key] = merged[key] or second[key]
    return merged


def publish(out: Path, runs: list[SourceRun], checked_at: datetime, today: date) -> bool:
    """Store each source's airings, then build schedule.json and status.json from all of them.

    Runs are given in priority order: for an airing two sources share, the earlier one wins.
    A failed source contributes its last stored airings, so one broken source never empties
    or freezes the feed; status.json says which source is behind. Airings before `today`
    (Moscow) are dropped. Nothing is rewritten when nothing but timestamps changed.
    Returns whether anything was written.
    """
    stamp = utc_text(checked_at)
    sources = out / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    previous_status = read_json(out / "status.json") or {}
    previous_sources = previous_status.get("sources", {})
    previous_schedule = read_json(out / "schedule.json") or {}
    previous_rows = [row for event in previous_schedule.get("events", []) for row in event["broadcasts"]]
    previous_rows += previous_schedule.get("unmatched", [])
    changed = False
    rows: dict[str, dict] = {}
    source_status = {}
    for run in runs:
        path = sources / f"{run.name}.json"
        stored = read_json(path)
        if stored is None and run.name in SOURCE_URLS:
            fields = Broadcast.__dataclass_fields__
            stored = {"broadcasts": [{key: row[key] for key in fields} for row in previous_rows
                                     if row["source_url"].startswith(SOURCE_URLS[run.name])]}
        last_success = previous_sources.get(run.name, {}).get("last_success")
        if not run.errors:
            stored = {"source": run.name, "fetched_at": stamp,
                      "broadcasts": [asdict(row) for row in sorted(run.broadcasts, key=lambda row: row.start_utc)]}
            if content(stored) != content(read_json(path)):
                write_json(path, stored)
                changed = True
            last_success = stamp
        source_status[run.name] = {"last_success": last_success, "stale": bool(run.errors), "errors": run.errors}
        for row in (stored or {}).get("broadcasts", []):
            rows[row["broadcast_id"]] = merge(rows[row["broadcast_id"]], row) if row["broadcast_id"] in rows else row

    window = datetime.combine(today, datetime.min.time(), tzinfo=MOSCOW)
    current = [Broadcast(**row) for row in rows.values() if datetime.fromisoformat(row["start_utc"]) >= window]
    schedule = build_schedule(current, checked_at)
    if content(schedule) != content(previous_schedule or None):
        write_json(out / "schedule.json", schedule)
        changed = True

    errors = {url: text for run in runs for url, text in run.errors.items()}
    status = {
        "checked_at": stamp,
        "last_success": stamp if any(not run.errors for run in runs) else previous_status.get("last_success"),
        "stale": bool(errors),
        "errors": errors,
        "unpublished": [url for run in runs for url in run.unpublished],
        "sources": source_status,
    }
    if not changed and content(status) == content(previous_status):
        return False
    write_json(out / "status.json", status)
    return True


def content(data: dict | None) -> object:
    """The data without the timestamps that change on every run."""
    if data is None:
        return None
    text = json.dumps(data, ensure_ascii=False, sort_keys=True)
    return re.sub(r'"(generated_at|verified_at|checked_at|last_success|fetched_at)": ("[^"]*"|null)', "", text)


def read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_json(path: Path, data: dict) -> None:
    handle, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.write("\n")
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def utc_text(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("public/data"), help="Папка для schedule.json и status.json")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    today = now.astimezone(MOSCOW).date()

    guide = SourceRun("match_guide", [])
    guide.broadcasts = collect_guide(today, 7, read_page, lambda url, error: guide.errors.update({url: str(error)}),
                                     guide.unpublished.append)
    live = SourceRun("match_live", [])
    live.broadcasts = collect_live(today, read_page, lambda url, error: live.errors.update({url: str(error)}))
    runs = [guide, live]
    for name, channel in OKKO_CHANNELS.items():
        okko = SourceRun(name, [])
        stored = (read_json(args.out / "sources" / f"{name}.json") or {}).get("broadcasts", [])
        try:
            okko.broadcasts = collect_okko(today, channel=channel, previous=[Broadcast(**row) for row in stored])
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            okko.errors[channel_url(channel)] = str(error)
        runs.append(okko)

    changed = publish(args.out, runs, now, today)
    for run in runs:
        print(f"{run.name}: {len(run.broadcasts)} показов, ошибок {len(run.errors)}")

    from collectors.ibu import collect_ibu, publish_ibu  # imports this module's helpers

    ibu_files, ibu_errors = collect_ibu(today)
    changed = publish_ibu(args.out / "ibu", ibu_files, ibu_errors, now) or changed
    print(f"ibu: файлов {len(ibu_files)}, ошибок {len(ibu_errors)}")
    print("Данные обновлены" if changed else "Данные не изменились")
    if ibu_errors or any(run.errors for run in runs):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
