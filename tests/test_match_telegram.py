from datetime import date, datetime, timezone
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from collectors.match_telegram import SHOW, collect_match_telegram, light_text, parse_card
from collectors.match_tv import Broadcast
from collectors.okko_telegram import channel_url, ocr_image
from collectors.schedule import SourceRun, parse_race, publish


FIXTURES = Path(__file__).parent / "fixtures" / "match_telegram"
POSTED = datetime(2026, 11, 26, 12, 6, tzinfo=timezone.utc)
CARD = (FIXTURES / "ocr-18519.txt").read_text(encoding="utf-8")
EXPECTED = [
    ("Биатлон. Альфа-Банк Кубок Содружества. Спринт. Мужчины", "2026-11-28T04:45:00Z"),
    ("Биатлон. Альфа-Банк Кубок Содружества. Спринт. Женщины", "2026-11-28T07:40:00Z"),
    ("Биатлон. Альфа-Банк Кубок Содружества. Масс-старт. Мужчины", "2026-11-29T05:30:00Z"),
    (SHOW, "2026-11-29T06:45:00Z"),
    ("Биатлон. Альфа-Банк Кубок Содружества. Масс-старт. Женщины", "2026-11-29T07:50:00Z"),
]


def rows(text, posted=POSTED):
    return [(row.title, row.start_utc) for row in parse_card(text, posted)]


class ParseCardTests(unittest.TestCase):
    def test_reads_the_khanty_mansiysk_card(self):
        self.assertEqual(rows(CARD), EXPECTED)

    def test_race_titles_group_with_the_tv_guide(self):
        for title, _ in EXPECTED:
            if title != SHOW:
                with self.subTest(title=title):
                    race = parse_race(title)
                    self.assertEqual((race.competition, race.category), ("Альфа-Банк Кубок Содружества", "russian"))

    def test_ignores_cards_that_are_not_biathlon(self):
        skiing = "ЛЫЖНЫЕ ГОНКИ. КУБОК РОССИИ\n28 ноября\n10:00 Спринт. Мужчины\n"
        self.assertEqual(rows(skiing), [])

    def test_ignores_results_and_start_lists(self):
        results = ("Биатлон. Кубок Содружества\n🥇 Анастасия Томшина (ХМАО) – 20:00.2 (1)\n"
                   "28 ноября\n1 Иванов Иван 10:40:30\n")
        self.assertEqual(rows(results), [])

    @unittest.skipUnless(shutil.which("tesseract"), "tesseract is not installed")
    def test_ocr_reads_the_real_card(self):
        text = ocr_image((FIXTURES / "card-18519.jpg").read_bytes(), light_text)
        self.assertEqual(rows(text), EXPECTED)


class CollectTests(unittest.TestCase):
    PAGE = (FIXTURES / "channel.html").read_text(encoding="utf-8")

    def collect(self, start=date(2026, 11, 27)):
        requested, images = [], []

        def read_page(url):
            requested.append(url)
            self.assertTrue(url.startswith(channel_url("biathlon_match")))
            return self.PAGE if len(requested) == 1 else "<html></html>"

        def read_text(url):
            images.append(url)
            return CARD if len(images) == 1 else ""

        return collect_match_telegram(start, read_page, read_text), images

    def test_collects_match_tv_airings_from_the_card(self):
        broadcasts, images = self.collect()
        self.assertGreater(len(images), 1)  # every photo is read: cards have no fixed caption
        self.assertEqual([(row.title, row.start_utc) for row in broadcasts], EXPECTED)
        first = broadcasts[0]
        self.assertEqual((first.broadcast_id, first.platform, first.channel, first.type),
                         ("match-matchtv-1795841100", "MATCH", "Матч ТВ", "live"))
        self.assertTrue(first.source_url.startswith("https://t.me/biathlon_match/"))
        show = next(row for row in broadcasts if row.title == SHOW)
        self.assertEqual(show.type, "unknown")

    def test_keeps_only_the_coming_week(self):
        broadcasts, _ = self.collect(date(2026, 11, 29))
        self.assertEqual([row.start_utc[:10] for row in broadcasts], ["2026-11-29"] * 3)


def guide(title, start_utc, channel="Матч ТВ", minutes_id=None):
    return Broadcast(
        broadcast_id=f"match-{'matchtv' if channel == 'Матч ТВ' else 'arena'}-{minutes_id or start_utc}", platform="MATCH",
        channel=channel, title=title, type="live", start_utc=start_utc, end_utc="2026-11-28T07:15:00Z",
        url=None, source_url="https://matchtv.ru/tvguide/matchtv?date=2026-11-28",
    )


class BackupTests(unittest.TestCase):
    def cards(self):
        return [Broadcast(broadcast_id=f"match-matchtv-{start}", platform="MATCH", channel="Матч ТВ", title=title,
                          type="unknown" if title == SHOW else "live", start_utc=start, end_utc=None, url=None,
                          source_url="https://t.me/biathlon_match/18519") for title, start in EXPECTED]

    def publish(self, guide_rows):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            publish(out, [SourceRun("match_guide", guide_rows), SourceRun("match_telegram", self.cards())],
                    datetime(2026, 11, 27, 9, 0, tzinfo=timezone.utc), date(2026, 11, 27))
            schedule = json.loads((out / "schedule.json").read_text(encoding="utf-8"))
        return [row for event in schedule["events"] for row in event["broadcasts"]] + schedule["unmatched"]

    def test_fills_the_feed_when_the_guide_has_nothing(self):
        self.assertEqual(len(self.publish([])), 5)

    def test_the_guide_wins_for_the_same_airing(self):
        title = "Биатлон. Альфа-Банк Кубок Содружества. Спринт. Мужчины. Прямая трансляция из Ханты-Мансийска"
        published = self.publish([guide(title, "2026-11-28T04:45:00Z")])
        sprint = [row for row in published if row["title"].startswith("Биатлон. Альфа-Банк Кубок Содружества. Спринт. Мужчины")]
        self.assertEqual(len(sprint), 1)
        self.assertEqual((sprint[0]["title"], sprint[0]["end_utc"]), (title, "2026-11-28T07:15:00Z"))

    def test_a_card_time_near_the_guide_time_is_not_a_second_airing(self):
        title = "Биатлон. Альфа-Банк Кубок Содружества. Спринт. Мужчины. Прямая трансляция из Ханты-Мансийска"
        published = self.publish([guide(title, "2026-11-28T04:40:00Z")])
        starts = [row["start_utc"] for row in published if "Спринт. Мужчины" in row["title"]]
        self.assertEqual(starts, ["2026-11-28T04:40:00Z"])
        self.assertEqual(len(published), 5)


if __name__ == "__main__":
    unittest.main()
