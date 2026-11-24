"""Collect biathlon airings from each official Match channel schedule."""

import argparse
from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import date, datetime, time, timedelta, timezone
import json
import re
from urllib.request import urlopen

from bs4 import BeautifulSoup

from collectors.match_tv import Broadcast, MATCH_ORIGIN, utc_timestamp


CHANNEL_NAMES = {
    "matchtv": "Матч ТВ",
    "strana": "Матч Страна",
    "arena": "Матч Арена",
    "igra": "Матч Игра",
    "premier": "Матч Премьер",
}
MOSCOW = timezone(timedelta(hours=3))
MONTHS = "января февраля марта апреля мая июня июля августа сентября октября ноября декабря".split()
TRANSMISSION = "p-tv-guide-schedule-channel-transmission"


def guide_url(channel: str, day: date) -> str:
    return f"{MATCH_ORIGIN}/tvguide/{channel}?date={day.isoformat()}"


def parse_guide(html: str, channel: str, day: date) -> list[Broadcast]:
    page = BeautifulSoup(html, "html.parser")
    channel_name = CHANNEL_NAMES[channel]
    heading = page.select_one("h1")
    selected = page.select_one(".m-weekly-inline-calendar__date--status-selected")
    selected_day = selected.get_text(" ", strip=True).split(",")[0] if selected else ""
    relative_days = {"Сегодня": 0, "Завтра": 1, "Вчера": -1}
    if selected_day in relative_days:
        shown_day = datetime.now(MOSCOW).date() + timedelta(days=relative_days[selected_day])
        selected_day = f"{shown_day.day} {MONTHS[shown_day.month - 1]}"
    expected_day = f"{day.day} {MONTHS[day.month - 1]}"
    if selected_day != expected_day or heading is None or heading.get_text(" ", strip=True) != f"Телепрограмма {channel_name}":
        raise ValueError("Телегид не подтвердил запрошенные дату и канал")

    items = page.select(f".p-tv-guide-schedule-channel .{TRANSMISSION}")
    if not items:
        raise ValueError("Сетка на эту дату недоступна")

    slots: list[tuple[int, str]] = []
    slot_day = day
    previous_time = time.min
    for item in items:
        clock = item.select_one(f".{TRANSMISSION}__time-block-time")
        label = item.select_one(f".{TRANSMISSION}__title")
        if clock is None or label is None:
            raise ValueError("В строке телегида отсутствует время или название")
        slot_time = time.fromisoformat(clock.get_text(strip=True))
        if slot_time < previous_time:
            slot_day += timedelta(days=1)
        previous_time = slot_time
        start = int(datetime.combine(slot_day, slot_time, tzinfo=MOSCOW).timestamp())
        slots.append((start, label.get_text(" ", strip=True)))

    broadcasts = []
    for index, (start, title) in enumerate(slots):
        if not title.casefold().startswith("биатлон."):
            continue
        end = slots[index + 1][0] if index + 1 < len(slots) else None
        title = re.sub(r"\s+\[\d+\+\]$", "", title)
        label = title.casefold()
        kind = "unknown"
        if ". прямая трансляция" in label:
            kind = "live"
        elif label.endswith((". повтор", " (повтор)")):
            kind = "replay"
        broadcasts.append(Broadcast(
            broadcast_id=f"match-{channel}-{start}",
            platform="MATCH",
            channel=channel_name,
            title=title,
            type=kind,
            start_utc=utc_timestamp(start),
            end_utc=utc_timestamp(end) if end is not None and end > start else None,
            url=None,
            source_url=guide_url(channel, day),
        ))
    return broadcasts


def collect_guide(start: date, days: int, read_page: Callable[[str], str],
                  on_error: Callable[[str, Exception], None] | None = None) -> list[Broadcast]:
    if not 1 <= days <= 7:
        raise ValueError("Разрешено запрашивать от одного до семи дней")

    broadcasts: dict[str, Broadcast] = {}
    for offset in range(days):
        day = start + timedelta(days=offset)
        for channel in CHANNEL_NAMES:
            url = guide_url(channel, day)
            try:
                for row in parse_guide(read_page(url), channel, day):
                    broadcasts.setdefault(row.broadcast_id, row)
            except (OSError, ValueError) as error:
                if on_error is None:
                    raise
                on_error(url, error)

    rows = sorted(broadcasts.values(), key=lambda row: row.start_utc)
    live_starts: dict[str, set[str]] = {}
    for row in rows:
        if row.type == "live":
            title = row.title.casefold().replace(". прямая трансляция", ". трансляция")
            live_starts.setdefault(title, set()).add(row.start_utc)

    for index, row in enumerate(rows):
        starts = live_starts.get(row.title.casefold(), set())
        if row.type == "unknown" and len(starts) == 1:
            elapsed = datetime.fromisoformat(row.start_utc) - datetime.fromisoformat(next(iter(starts)))
            if timedelta(0) < elapsed <= timedelta(days=1):
                rows[index] = replace(row, type="replay")
    return rows


def read_page(url: str) -> str:
    with urlopen(url, timeout=15) as response:
        return response.read().decode("utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    today = datetime.now(MOSCOW).date()
    parser.add_argument("--start", type=date.fromisoformat, default=today)
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()
    if not 1 <= args.days <= 7 or args.start < today or args.start + timedelta(days=args.days) > today + timedelta(days=7):
        parser.error("Укажите диапазон в пределах ближайших семи дней по МСК")

    errors: dict[str, str] = {}

    def record_error(url: str, error: Exception) -> None:
        errors[url] = str(error)

    broadcasts = collect_guide(args.start, args.days, read_page, record_error)
    print(json.dumps({"broadcasts": [asdict(row) for row in broadcasts], "errors": errors}, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
