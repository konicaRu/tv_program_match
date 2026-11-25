"""Group confirmed Match airings into race events and publish schedule JSON."""

import argparse
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import tempfile

from collectors.match_guide import MOSCOW, SHOWS, collect_guide, read_page
from collectors.match_tv import Broadcast


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


def publish(out: Path, broadcasts: list[Broadcast], errors: dict[str, str], checked_at: datetime,
            unpublished: Sequence[str] = ()) -> None:
    """Replace schedule.json only after a clean run; always record the outcome in status.json.

    Days the channels have not published yet are not errors: they are listed so a later run can fill them.
    """
    out.mkdir(parents=True, exist_ok=True)
    status_path = out / "status.json"
    try:
        last_success = json.loads(status_path.read_text(encoding="utf-8")).get("last_success")
    except (OSError, ValueError):
        last_success = None
    if not errors:
        write_json(out / "schedule.json", build_schedule(broadcasts, checked_at))
        last_success = utc_text(checked_at)
    write_json(status_path, {
        "checked_at": utc_text(checked_at),
        "last_success": last_success,
        "stale": bool(errors),
        "errors": errors,
        "unpublished": list(unpublished),
    })


def is_due(status: dict | None, today: date) -> bool:
    """Collect on Sunday and Monday (Moscow); on other days only while this week still has gaps.

    Days after the coming Sunday belong to next week's guide, which Monday's run collects.
    """
    if today.weekday() in (6, 0) or status is None or status.get("stale"):
        return True
    sunday = today + timedelta(days=6 - today.weekday())
    return any(date.fromisoformat(url.rsplit("date=", 1)[1]) <= sunday for url in status.get("unpublished", []))


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
    parser.add_argument("--out", type=Path, default=Path("data"), help="Папка для schedule.json и status.json")
    parser.add_argument("--only-when-due", action="store_true",
                        help="Пропустить сбор, если неделя уже собрана и сегодня не воскресенье и не понедельник")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    if args.only_when_due:
        try:
            status = json.loads((args.out / "status.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            status = None
        if not is_due(status, now.astimezone(MOSCOW).date()):
            print("Неделя уже собрана, сбор не нужен")
            return
    errors: dict[str, str] = {}
    unpublished: list[str] = []

    def record_error(url: str, error: Exception) -> None:
        errors[url] = str(error)

    broadcasts = collect_guide(now.astimezone(MOSCOW).date(), 7, read_page, record_error, unpublished.append)
    publish(args.out, broadcasts, errors, now, unpublished)
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
