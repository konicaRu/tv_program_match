"""Read Match TV biathlon airings from the schedule cards in the Match biathlon Telegram channel.

https://t.me/biathlon_match ("Матч! Биатлон и лыжи") posts a card before each Russian stage:
a header naming the competition, then date lines ("28 ноября") and "07:45 Спринт. Мужчины"
rows, including «Биатлон с Дмитрием Губерниевым». The same card appears in vk.ru/biathlon_match;
the Telegram preview is used because it keeps a history of posts and needs no login.

The cards name only Match TV and only live showings, so this is a backup: schedule.publish
drops a card airing when the TV guide gives the same race on Match TV near that time.
Anything the OCR text does not state clearly (date, time, discipline, gender) is dropped.
"""

import argparse
from collections.abc import Callable
from dataclasses import asdict
from datetime import date, datetime, timedelta
import json
import re
import subprocess

from collectors.match_guide import MOSCOW, read_page
from collectors.match_tv import Broadcast, utc_timestamp
from collectors.okko_telegram import (
    COMPETITIONS, DISCIPLINES, GENDERS, Airing, Post, date_header, ocr_image, read_posts,
)


CHANNEL = "biathlon_match"
SHOW = "Биатлон с Дмитрием Губерниевым"
# A card row starts with its time ("07:45 Спринт. Мужчины"). A time followed by seconds or
# tenths ("20:00.2", "10:40:30") is a result or a start time, not an airing.
ROW_TIME = re.compile(r"^\W*([01]?\d|2[0-3])[:.]([0-5]\d)(?![.:,]?\d)")


def light_text(image):
    """Match cards: grey-white and gold text on a blue background becomes black on white.

    Text is light in red and green and not much bluer than that; the blue background is not.
    """
    from PIL import Image, ImageChops

    red, green, blue = image.split()
    warm = Image.blend(red, green, 0.5)
    light = warm.point(lambda value: 255 if value > 120 else 0)
    bluish = ImageChops.subtract(blue, warm).point(lambda value: 255 if value > 30 else 0)
    text = ImageChops.subtract(light, bluish)
    return ImageChops.invert(text)


def read_card_text(url: str) -> str:
    from urllib.request import urlopen

    with urlopen(url, timeout=15) as response:
        return ocr_image(response.read(), light_text)


def competition_of(label: str) -> str | None:
    """The competition as the TV guide names it, sponsor included ("Альфа-Банк Кубок Содружества")."""
    name = next((name for key, name in COMPETITIONS if key in label), None)
    if name and "альфа" in label:
        name = f"Альфа-Банк {name}"
    return name


def parse_card(text: str, posted_at: datetime) -> list[Airing]:
    """Turn the OCR text of one schedule card into airings, in Moscow time."""
    label = text.casefold()
    competition = competition_of(label)
    if competition is None or "биатлон" not in label:
        return []

    airings = []
    day: date | None = None
    posted_day = posted_at.astimezone(MOSCOW).date()
    for line in text.splitlines():
        header, header_day = date_header(line, posted_day)
        if header:
            day = header_day
            continue
        clock = ROW_TIME.search(line)
        if day is None or clock is None:
            continue
        start = datetime.combine(day, datetime.min.time().replace(hour=int(clock.group(1)), minute=int(clock.group(2))),
                                 tzinfo=MOSCOW)
        words = (line[:clock.start()] + " " + line[clock.end():]).casefold()
        if "губерни" in words:
            airings.append(Airing(SHOW, "show", start))
            continue
        discipline = next((item for item in DISCIPLINES if all(key in words for key in item[0])), None)
        if discipline is None:
            continue
        _, name, slug, needs_gender = discipline
        gender = next((value for key, value in GENDERS.items() if key in words), None)
        if needs_gender and gender is None:
            continue
        title = f"Биатлон. {competition}. {name}"
        if needs_gender:
            title += f". {gender[0]}"
        airings.append(Airing(title, slug, start))
    return airings


def collect_match_telegram(start: date, read_page: Callable[[str], str] = read_page,
                           read_text: Callable[[str], str] = read_card_text, channel: str = CHANNEL) -> list[Broadcast]:
    """Match TV airings from the channel's schedule cards for the seven days from `start` (Moscow).

    Every image of the posts read is OCR'd: the cards carry no fixed caption. A newer card
    about the same race on the same day replaces an older one.
    """
    window_start = datetime.combine(start, datetime.min.time(), tzinfo=MOSCOW)
    window_end = window_start + timedelta(days=7)
    latest: dict[tuple[str, date], tuple[Airing, Post]] = {}
    for post in read_posts(window_start, read_page, channel):
        if post.posted_at < window_start - timedelta(days=8):
            continue
        for image in post.images:
            try:
                text = read_text(image)
            except (OSError, ValueError, subprocess.SubprocessError):
                # Most photos are not cards: one that fails to load or OCR is skipped,
                # only an unreadable channel page fails the source.
                continue
            for airing in parse_card(text, post.posted_at):
                latest[(airing.title, airing.start.date())] = (airing, post)

    broadcasts = []
    for airing, post in latest.values():
        if not window_start <= airing.start < window_end:
            continue
        stamp = int(airing.start.timestamp())
        broadcasts.append(Broadcast(
            # The TV guide's id for a Match TV airing, so the guide's row wins when both have it.
            broadcast_id=f"match-matchtv-{stamp}",
            platform="MATCH",
            channel="Матч ТВ",
            title=airing.title,
            type="unknown" if airing.title == SHOW else "live",
            start_utc=utc_timestamp(stamp),
            end_utc=None,
            url=None,
            source_url=post.url,
        ))
    return sorted(broadcasts, key=lambda row: row.start_utc)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, default=datetime.now(MOSCOW).date())
    args = parser.parse_args()
    print(json.dumps([asdict(row) for row in collect_match_telegram(args.start)], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
