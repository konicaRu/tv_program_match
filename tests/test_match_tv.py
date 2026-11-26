from datetime import date
from pathlib import Path
import unittest

from collectors.match_tv import collect_live, live_url, parse_broadcasts


FIXTURES = Path(__file__).parent / "fixtures" / "match"
SOURCE = "https://matchtv.ru/biathlon/live?date=28-11-2026"


class MatchTvParserTests(unittest.TestCase):
    def test_extracts_real_broadcasts_with_utc_times(self):
        expected = {
            "2026-11-28": ["2026-11-28T04:45:00Z", "2026-11-28T07:40:00Z"],
            "2026-11-29": ["2026-11-29T05:30:00Z", "2026-11-29T07:50:00Z"],
        }
        for day, start_times in expected.items():
            with self.subTest(day=day):
                date = "-".join(reversed(day.split("-")))
                source_url = f"https://matchtv.ru/biathlon/live?date={date}"
                html = (FIXTURES / f"{day}.html").read_text(encoding="utf-8")
                rows = parse_broadcasts(html, source_url)
                self.assertEqual([row.start_utc for row in rows], start_times)
                self.assertEqual([row.channel for row in rows], ["МАТЧ ТВ"] * 2)
                self.assertEqual([row.source_url for row in rows], [source_url] * 2)
                self.assertEqual([row.type for row in rows], ["unknown"] * 2)
                self.assertTrue(all(row.url.startswith("https://matchtv.ru/biathlon/") for row in rows))

    def test_keeps_distinct_airings_and_only_labels_an_explicit_repeat(self):
        html = (FIXTURES / "2026-11-28.html").read_text(encoding="utf-8")
        first = html.split('<div class="broadcast__item"')[1]
        first = '<div class="broadcast__item"' + first
        replay = first.replace('data-channel-id="130"', 'data-channel-id="131"')
        replay = replay.replace("/channel/matchtv", "/channel/strana")
        replay = replay.replace("МАТЧ ТВ", "МАТЧ СТРАНА")
        replay = replay.replace("1795841100", "1795888200").replace("1795850100", "1795894800")
        replay = replay.replace("Спринт. Мужчины", "Спринт. Мужчины. Повтор")
        rows = parse_broadcasts(first + first + replay, SOURCE)
        self.assertEqual([row.type for row in rows], ["unknown", "replay"])
        self.assertEqual(rows[1].start_utc, "2026-11-28T17:50:00Z")
        self.assertNotEqual(rows[0].broadcast_id, rows[1].broadcast_id)

    def test_filters_out_football_channels(self):
        html = (FIXTURES / "2026-11-28.html").read_text(encoding="utf-8")
        self.assertEqual(parse_broadcasts(html.replace("/channel/matchtv", "/channel/football1"), SOURCE), [])

    def test_rejects_missing_schedule_markup(self):
        with self.assertRaises(ValueError):
            parse_broadcasts("<html><p>Unavailable</p></html>", SOURCE)

    def test_an_empty_day_is_not_an_error(self):
        html = (FIXTURES / "2026-11-26-empty.html").read_text(encoding="utf-8")
        self.assertEqual(parse_broadcasts(html, SOURCE), [])


class CollectLiveTests(unittest.TestCase):
    def test_collects_a_week_and_reports_failed_days(self):
        pages = {
            live_url(date(2026, 11, 28)): (FIXTURES / "2026-11-28.html").read_text(encoding="utf-8"),
            live_url(date(2026, 11, 29)): (FIXTURES / "2026-11-29.html").read_text(encoding="utf-8"),
        }
        empty = (FIXTURES / "2026-11-26-empty.html").read_text(encoding="utf-8")
        errors = {}

        def read_page(url):
            if url == live_url(date(2026, 11, 30)):
                raise OSError("timed out")
            return pages.get(url, empty)

        rows = collect_live(date(2026, 11, 27), read_page, lambda url, error: errors.setdefault(url, str(error)))
        self.assertEqual(live_url(date(2026, 11, 28)), "https://matchtv.ru/biathlon/live?date=28-11-2026")
        self.assertEqual([row.start_utc for row in rows],
                         ["2026-11-28T04:45:00Z", "2026-11-28T07:40:00Z", "2026-11-29T05:30:00Z", "2026-11-29T07:50:00Z"])
        self.assertEqual(errors, {live_url(date(2026, 11, 30)): "timed out"})

    def test_rejects_a_page_that_shows_another_day(self):
        html = (FIXTURES / "2026-11-28.html").read_text(encoding="utf-8")
        errors = {}
        rows = collect_live(date(2026, 11, 29), lambda url: html, lambda url, error: errors.setdefault(url, str(error)))
        self.assertEqual(rows, [])
        self.assertIn(live_url(date(2026, 11, 29)), errors)
