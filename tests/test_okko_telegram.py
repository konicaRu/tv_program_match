from datetime import date, datetime, timezone
from pathlib import Path
import shutil
import unittest

from collectors.okko_telegram import (
    CHANNEL_URL, collect_okko, ocr_image, parse_channel, parse_schedule_text,
)
from collectors.schedule import parse_race


FIXTURES = Path(__file__).parent / "fixtures" / "okko"
POSTED = datetime(2026, 11, 25, 10, 20, tzinfo=timezone.utc)


def ocr(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def rows(text, posted=POSTED):
    return [(row.title, row.start_utc) for row in parse_schedule_text(text, posted)]


class ParseScheduleTextTests(unittest.TestCase):
    def test_reads_the_kontiolahti_schedule_despite_ocr_noise(self):
        self.assertEqual(rows(ocr("ocr-2494-1.txt")) + rows(ocr("ocr-2494-2.txt")), [
            ("Биатлон. Кубок мира. Индивидуальная гонка. Мужчины", "2026-11-26T15:15:00Z"),
            ("Биатлон. Кубок мира. Индивидуальная гонка. Женщины", "2026-11-27T15:15:00Z"),
            ("Биатлон. Кубок мира. Одиночная смешанная эстафета", "2026-11-28T12:10:00Z"),
            ("Биатлон. Кубок мира. Смешанная эстафета", "2026-11-28T14:15:00Z"),
            ("Биатлон. Кубок мира. Спринт. Мужчины", "2026-11-29T12:00:00Z"),
            ("Биатлон. Кубок мира. Спринт. Женщины", "2026-11-29T15:15:00Z"),
        ])

    def test_titles_are_understood_as_races(self):
        for title, _ in rows(ocr("ocr-2494-2.txt")):
            with self.subTest(title=title):
                race = parse_race(title)
                self.assertIsNotNone(race)
                self.assertEqual(race.category, "international")

    def test_reads_gender_before_discipline(self):
        self.assertEqual(rows(ocr("ocr-2456-1.txt"), datetime(2026, 11, 19, 15, 10, tzinfo=timezone.utc)), [
            ("Биатлон. Золотая лига. Масс-старт. Женщины", "2026-11-21T07:00:00Z"),
            ("Биатлон. Золотая лига. Масс-старт. Мужчины", "2026-11-21T09:00:00Z"),
            ("Биатлон. Золотая лига. Смешанная эстафета", "2026-11-22T07:00:00Z"),
        ])

    def test_drops_lines_after_an_unreadable_date(self):
        text = "Кубок мира по биатлону\n2Т НОЯБРЯ, СУББОТА\nСпринт, мужчины 15:00\n29 НОЯБРЯ, ВОСКРЕСЕНЬЕ\nСпринт, женщины 18:15\n"
        self.assertEqual(rows(text), [("Биатлон. Кубок мира. Спринт. Женщины", "2026-11-29T15:15:00Z")])

    def test_drops_races_without_gender_or_valid_time(self):
        text = "Кубок мира по биатлону\n29 НОЯБРЯ\nСпринт 15:00\nСпринт, мужчины 25:00\nЭкспертная студия 14:45\n"
        self.assertEqual(rows(text), [])

    def test_ignores_images_without_a_known_competition(self):
        self.assertEqual(rows("Шорт-трек\n29 НОЯБРЯ\nСпринт, мужчины 15:00\n"), [])

    def test_january_races_announced_in_december_get_the_next_year(self):
        text = "Кубок мира по биатлону\n8 ЯНВАРЯ, ЧЕТВЕРГ\nСпринт, женщины 16:30\n"
        posted = datetime(2026, 12, 30, 9, 0, tzinfo=timezone.utc)
        self.assertEqual(rows(text, posted), [("Биатлон. Кубок мира. Спринт. Женщины", "2027-01-08T13:30:00Z")])


class ChannelTests(unittest.TestCase):
    def test_finds_schedule_posts_and_their_images(self):
        posts = parse_channel((FIXTURES / "channel.html").read_text(encoding="utf-8"))
        self.assertEqual([post.post_id for post in posts], [2483, 2489, 2491, 2494])
        schedule = [post for post in posts if post.is_schedule]
        self.assertEqual([post.post_id for post in schedule], [2494])
        self.assertEqual(schedule[0].posted_at, datetime(2026, 11, 25, 10, 20, 1, tzinfo=timezone.utc))
        self.assertEqual(len(schedule[0].images), 2)
        self.assertTrue(all(url.startswith("https://cdn") for url in schedule[0].images))

    def test_collects_okko_airings_for_the_coming_week(self):
        page = (FIXTURES / "channel.html").read_text(encoding="utf-8")
        texts = {}

        def read_page(url):
            self.assertTrue(url.startswith(CHANNEL_URL))
            return page if "before=" not in url else "<html></html>"

        def read_text(image_url):
            texts.setdefault(image_url, f"ocr-2494-{len(texts) + 1}.txt")
            return ocr(texts[image_url])

        broadcasts = collect_okko(date(2026, 11, 26), read_page, read_text)
        self.assertEqual(len(broadcasts), 6)
        first = broadcasts[0]
        self.assertEqual((first.platform, first.channel, first.type), ("OKKO", "Okko", "unknown"))
        self.assertEqual(first.url, "https://t.me/okkowintersport/2494")
        self.assertEqual(first.source_url, "https://t.me/okkowintersport/2494")
        self.assertEqual(len({row.broadcast_id for row in broadcasts}), 6)
        later = collect_okko(date(2026, 11, 29), read_page, read_text)
        self.assertEqual([row.title.split(". ")[-1] for row in later], ["Мужчины", "Женщины"])


@unittest.skipUnless(shutil.which("tesseract"), "Tesseract is not installed")
class OcrTests(unittest.TestCase):
    def test_reads_times_from_the_real_image(self):
        text = ocr_image((FIXTURES / "schedule-2494-2.jpg").read_bytes())
        self.assertEqual([start for _, start in rows(text)][-2:], ["2026-11-29T12:00:00Z", "2026-11-29T15:15:00Z"])


if __name__ == "__main__":
    unittest.main()
