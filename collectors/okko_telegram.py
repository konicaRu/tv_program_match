"""Read Okko biathlon airings from the schedule images in the official Okko Telegram channels.

Okko posts each stage's schedule as an image in https://t.me/okkowintersport and reposts it in
https://t.me/okkosport. The public web preview of a channel needs no login. The images are read with Tesseract OCR; anything the
OCR text does not state clearly (date, time, discipline, gender) is dropped, never guessed.
"""

import argparse
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
import difflib
import io
import json
import re
import subprocess

from bs4 import BeautifulSoup

from collectors.match_guide import MOSCOW, read_page
from collectors.match_tv import Broadcast, utc_timestamp


CHANNEL = "okkowintersport"
CHANNEL_URL = f"https://t.me/s/{CHANNEL}"
# Page back until posts are this much older than the window: a stage's schedule is posted
# a few days ahead, and a busy channel pushes it off the first pages within a day.
HISTORY = timedelta(days=8)
MAX_PAGES = 30
MONTHS = "ЯНВАРЯ ФЕВРАЛЯ МАРТА АПРЕЛЯ МАЯ ИЮНЯ ИЮЛЯ АВГУСТА СЕНТЯБРЯ ОКТЯБРЯ НОЯБРЯ ДЕКАБРЯ".split()
COMPETITIONS = [
    ("кубок мира", "Кубок мира"),
    ("чемпионат мира", "Чемпионат мира"),
    ("чемпионат европы", "Чемпионат Европы"),
    ("кубок ibu", "Кубок IBU"),
    ("золотая лига", "Золотая лига"),
    ("кубок содружества", "Кубок Содружества"),
    ("кубок россии", "Кубок России"),
    ("чемпионат россии", "Чемпионат России"),
]
# (keywords that must all appear, discipline as the Match guide names it, slug, needs gender)
DISCIPLINES = [
    (("сингл",), "Одиночная смешанная эстафета", "single-mixed-relay", False),
    (("одиночн", "эстафет"), "Одиночная смешанная эстафета", "single-mixed-relay", False),
    (("смешанн", "эстафет"), "Смешанная эстафета", "mixed-relay", False),
    (("суперспринт",), "Суперспринт", "super-sprint", True),
    (("спринт",), "Спринт", "sprint", True),
    (("коротк", "индивидуальн"), "Короткая индивидуальная гонка", "short-individual", True),
    (("индивидуальн",), "Индивидуальная гонка", "individual", True),
    (("масс",), "Масс-старт", "mass-start", True),
    (("мосс", "старт"), "Масс-старт", "mass-start", True),  # how OCR reads "Масс" on Match cards
    (("преследован",), "Гонка преследования", "pursuit", True),
    (("пасьют",), "Гонка преследования", "pursuit", True),
    (("эстафет",), "Эстафета", "relay", True),
]
GENDERS = {"мужчин": ("Мужчины", "men"), "женщин": ("Женщины", "women")}
TIME = re.compile(r"(?<!\d)([01]?\d|2[0-3])[:.]?([0-5]\d)(?!\d)")
WORD = re.compile(r"[А-ЯЁA-Z]{5,}")


@dataclass(frozen=True)
class Post:
    post_id: int
    posted_at: datetime
    text: str
    images: list[str]
    channel: str = CHANNEL

    @property
    def url(self) -> str:
        return f"https://t.me/{self.channel}/{self.post_id}"

    @property
    def is_schedule(self) -> bool:
        label = self.text.casefold()
        return bool(self.images) and "расписан" in label and "биатлон" in label


@dataclass(frozen=True)
class Airing:
    title: str
    slug: str
    start: datetime

    @property
    def start_utc(self) -> str:
        return utc_timestamp(int(self.start.timestamp()))


def channel_url(channel: str) -> str:
    return f"https://t.me/s/{channel}"


def parse_channel(html: str, channel: str = CHANNEL) -> list[Post]:
    page = BeautifulSoup(html, "html.parser")
    posts = []
    for message in page.select(f'.tgme_widget_message[data-post^="{channel}/"]'):
        stamp = message.select_one(".tgme_widget_message_date time[datetime]")
        if stamp is None:
            continue
        text = message.select_one(".tgme_widget_message_text")
        images = []
        for photo in message.select(".tgme_widget_message_photo_wrap[style]"):
            found = re.search(r"url\('([^']+)'\)", photo["style"])
            if found and found.group(1).startswith("https://"):
                images.append(found.group(1))
        posts.append(Post(
            post_id=int(message["data-post"].split("/")[1]),
            posted_at=datetime.fromisoformat(stamp["datetime"]),
            text=text.get_text("\n", strip=True) if text else "",
            images=images,
            channel=channel,
        ))
    return sorted(posts, key=lambda post: post.post_id)


def month_of(word: str) -> int | None:
    match = difflib.get_close_matches(word, MONTHS, n=1, cutoff=0.7)
    return MONTHS.index(match[0]) + 1 if match else None


def date_header(line: str, posted_day: date) -> tuple[bool, date | None]:
    """Whether an OCR line is a date header ("28 НОЯБРЯ"), and its date when the day number is readable.

    A date earlier than a week before the post belongs to the next year (January races
    announced in December). An unreadable day gives (True, None): the lines under it are dropped.
    """
    months = [month for word in WORD.findall(line.upper()) if (month := month_of(word))]
    if not months:
        return False, None
    found = re.search(r"(?<!\d)(\d{1,2})\s+[А-ЯЁA-Z]", line.upper())
    if not found or not 1 <= int(found.group(1)) <= 31:
        return True, None
    try:
        day = date(posted_day.year, months[0], int(found.group(1)))
    except ValueError:
        return True, None
    if day < posted_day - timedelta(days=7):
        day = day.replace(year=day.year + 1)
    return True, day


def parse_schedule_text(text: str, posted_at: datetime) -> list[Airing]:
    """Turn the OCR text of one schedule image into airings, in Moscow time."""
    label = text.casefold()
    competition = next((name for key, name in COMPETITIONS if key in label), None)
    if competition is None:
        return []

    airings = []
    day: date | None = None
    posted_day = posted_at.astimezone(MOSCOW).date()
    for line in text.splitlines():
        header, header_day = date_header(line, posted_day)
        if header:
            day = header_day
            continue

        clock = TIME.search(line)
        if day is None or clock is None:
            continue
        words = line[:clock.start()].casefold()
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
            slug += f"-{gender[1]}"
        start = datetime.combine(day, datetime.min.time().replace(hour=int(clock.group(1)), minute=int(clock.group(2))),
                                 tzinfo=MOSCOW)
        airings.append(Airing(title, slug, start))
    return airings


def white_text(image):
    """Okko cards: near-white text on a colour background becomes black on white."""
    from PIL import ImageOps

    return ImageOps.invert(image.convert("L").point(lambda value: 255 if value > 170 else 0))


def ocr_image(data: bytes, binarize: Callable = white_text) -> str:
    """OCR a schedule card: enlarge it, keep only its text (`binarize`), read it as Russian."""
    from PIL import Image

    image = Image.open(io.BytesIO(data)).convert("RGB")
    image = binarize(image.resize((image.width * 3, image.height * 3), Image.LANCZOS))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    result = subprocess.run(["tesseract", "stdin", "stdout", "-l", "rus", "--psm", "4"],
                            input=buffer.getvalue(), capture_output=True, check=True, timeout=60)
    return result.stdout.decode("utf-8")


def read_image_text(url: str) -> str:
    from urllib.request import urlopen

    with urlopen(url, timeout=15) as response:
        return ocr_image(response.read())


def read_posts(window_start: datetime, read_page: Callable[[str], str], channel: str) -> list[Post]:
    """A channel's posts, paging back until they are HISTORY older than the window, oldest first."""
    posts: dict[int, Post] = {}
    url = channel_url(channel)
    for _ in range(MAX_PAGES):
        found = parse_channel(read_page(url), channel)
        if not found:
            break
        posts.update((post.post_id, post) for post in found)
        if found[0].posted_at < window_start - HISTORY:
            break
        url = f"{channel_url(channel)}?before={found[0].post_id}"
    return sorted(posts.values(), key=lambda post: post.post_id)


def collect_okko(start: date, read_page: Callable[[str], str] = read_page,
                 read_text: Callable[[str], str] = read_image_text, channel: str = CHANNEL,
                 previous: Sequence[Broadcast] = ()) -> list[Broadcast]:
    """Okko airings from a channel's schedule posts for the seven days from `start` (Moscow).

    `previous` are the airings this channel gave before. One whose post is no longer on the
    pages read is kept while its day is in the window, unless a post read now gives the same
    race on the same day, which replaces it.
    """
    window_start = datetime.combine(start, datetime.min.time(), tzinfo=MOSCOW)
    window_end = window_start + timedelta(days=7)
    latest: dict[tuple[str, date], tuple[Airing, Post]] = {}
    for post in read_posts(window_start, read_page, channel):
        if not post.is_schedule:
            continue
        for image in post.images:
            for airing in parse_schedule_text(read_text(image), post.posted_at):
                # A newer post about the same race on the same day replaces the older one.
                latest[(airing.title, airing.start.date())] = (airing, post)

    broadcasts = []
    for row in previous:
        start_at = datetime.fromisoformat(row.start_utc)
        if window_start <= start_at < window_end and (row.title, start_at.astimezone(MOSCOW).date()) not in latest:
            broadcasts.append(row)
    for airing, post in latest.values():
        if not window_start <= airing.start < window_end:
            continue
        stamp = int(airing.start.timestamp())
        broadcasts.append(Broadcast(
            broadcast_id=f"okko-{airing.slug}-{stamp}",
            platform="OKKO",
            channel="Okko",
            title=airing.title,
            type="unknown",
            start_utc=utc_timestamp(stamp),
            end_utc=None,
            url=post.url,
            source_url=post.url,
        ))
    return sorted(broadcasts, key=lambda row: row.start_utc)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, default=datetime.now(MOSCOW).date())
    args = parser.parse_args()
    print(json.dumps([asdict(row) for row in collect_okko(args.start)], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
