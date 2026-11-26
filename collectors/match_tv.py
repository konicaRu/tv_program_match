"""Read scheduled broadcasts from the official Match biathlon page (matchtv.ru/biathlon/live)."""

import argparse
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup


MATCH_ORIGIN = "https://matchtv.ru"
CHANNELS = {"matchtv", "strana", "arena", "igra", "premier"}
MOSCOW = timezone(timedelta(hours=3))


@dataclass(frozen=True)
class Broadcast:
    broadcast_id: str
    platform: str
    channel: str
    title: str
    type: str
    start_utc: str
    end_utc: str | None
    url: str | None
    source_url: str


def parse_broadcasts(html: str, source_url: str) -> list[Broadcast]:
    page = BeautifulSoup(html, "html.parser")
    items = page.select('[data-block="broadcast-item"]')
    if not items:
        # An empty list is a day without biathlon; a page without the list is broken markup.
        if page.select_one(".broadcast__list") is not None:
            return []
        raise ValueError("Нет строк телепрограммы «Матча»")

    broadcasts: dict[str, Broadcast] = {}
    for item in items:
        channel_link = item.select_one(".broadcast__channel-name[href]")
        if channel_link is None:
            raise ValueError("В строке телепрограммы отсутствует канал")
        channel_url = urlsplit(urljoin(MATCH_ORIGIN, channel_link["href"]))
        channel_slug = channel_url.path.rstrip("/").removeprefix("/channel/")
        if channel_url.netloc != "matchtv.ru" or channel_slug not in CHANNELS:
            continue

        title_link = item.select_one(".broadcast__link")
        channel = channel_link.get_text(" ", strip=True)
        title = title_link.get_text(" ", strip=True) if title_link else ""
        if not channel or not title:
            raise ValueError("В строке телепрограммы отсутствует название канала или передачи")
        title = " ".join(title.split())

        try:
            start = int(item["data-time-start"])
            end = int(item["data-time-finish"])
            start_utc = utc_timestamp(start)
            end_utc = utc_timestamp(end)
        except (KeyError, ValueError, OverflowError, OSError) as error:
            raise ValueError("Некорректные метки времени в телепрограмме «Матча»") from error
        if end <= start:
            raise ValueError("Окончание показа должно быть позже начала")

        label = title.casefold()
        if label.endswith((". повтор", " (повтор)")):
            broadcast_type = "replay"
        elif label.endswith((". прямая трансляция", " (прямая трансляция)")):
            broadcast_type = "live"
        else:
            broadcast_type = "unknown"

        url = None
        if href := title_link.get("href"):
            target = urljoin(MATCH_ORIGIN, href)
            parsed = urlsplit(target)
            if parsed.scheme == "https" and parsed.netloc == "matchtv.ru" and parsed.path.startswith("/biathlon/"):
                url = target

        broadcast_id = f"match-{channel_slug}-{start}"
        broadcast = Broadcast(
            broadcast_id=broadcast_id,
            platform="MATCH",
            channel=channel,
            title=title,
            type=broadcast_type,
            start_utc=start_utc,
            end_utc=end_utc,
            url=url,
            source_url=source_url,
        )
        if broadcasts.setdefault(broadcast_id, broadcast) != broadcast:
            raise ValueError(f"Противоречащие строки телепрограммы: {broadcast_id}")

    return sorted(broadcasts.values(), key=lambda broadcast: broadcast.start_utc)


def live_url(day: date) -> str:
    return f"{MATCH_ORIGIN}/biathlon/live?date={day:%d-%m-%Y}"


def collect_live(start: date, read_page: Callable[[str], str],
                 on_error: Callable[[str, Exception], None], days: int = 7) -> list[Broadcast]:
    """The biathlon section day by day: a second official Match source next to the TV guide."""
    broadcasts: dict[str, Broadcast] = {}
    for offset in range(days):
        day = start + timedelta(days=offset)
        url = live_url(day)
        try:
            rows = parse_broadcasts(read_page(url), url)
            shown = {datetime.fromisoformat(row.start_utc).astimezone(MOSCOW).date() for row in rows}
            if shown - {day}:
                raise ValueError("Страница показала не тот день")
        except (OSError, ValueError) as error:
            on_error(url, error)
            continue
        for row in rows:
            broadcasts.setdefault(row.broadcast_id, row)
    return sorted(broadcasts.values(), key=lambda row: row.start_utc)


def utc_timestamp(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("html", type=Path, help="Сохранённая страница телепрограммы")
    parser.add_argument("--source-url", required=True, help="Адрес исходной страницы")
    args = parser.parse_args()
    try:
        broadcasts = parse_broadcasts(args.html.read_text(encoding="utf-8-sig"), args.source_url)
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps([asdict(broadcast) for broadcast in broadcasts], indent=2))


if __name__ == "__main__":
    main()
