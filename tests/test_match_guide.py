from datetime import date
from pathlib import Path
import unittest

from collectors.match_guide import GuideNotPublished, collect_guide, parse_guide


DAY = date(2026, 11, 28)
FIXTURES = Path(__file__).parent / "fixtures" / "match" / "guide"


class MatchGuideTests(unittest.TestCase):
    def test_collects_the_five_channels_and_keeps_each_showing(self):
        requested = []

        def read_fixture(url):
            channel = url.split("/")[-1].split("?")[0]
            requested.append(channel)
            return (FIXTURES / f"{channel}.html").read_text(encoding="utf-8")

        rows = collect_guide(DAY, 1, read_fixture)
        self.assertEqual(requested, ["matchtv", "strana", "arena", "igra", "premier"])
        self.assertEqual(len(rows), 8)
        self.assertEqual(len({row.broadcast_id for row in rows}), 8)
        self.assertEqual([row.type for row in rows[:4]], ["live"] * 4)
        self.assertEqual([row.type for row in rows[4:]], ["replay"] * 4)
        self.assertTrue(all(row.url is None for row in rows))
        strana = [row for row in rows if row.channel == "Матч Страна"]
        self.assertEqual([row.start_utc for row in strana],
                         ["2026-11-28T17:50:00Z", "2026-11-28T19:40:00Z"])
        self.assertEqual(strana[-1].end_utc, "2026-11-28T21:05:00Z")
        night = [row for row in rows if row.channel == "Матч Арена" and row.type == "replay"]
        self.assertEqual([row.start_utc for row in night],
                         ["2026-11-28T22:55:00Z", "2026-11-29T01:00:00Z"])

    def test_includes_the_guberniev_show_and_marks_its_later_airing_as_replay(self):
        def read_fixture(url):
            channel = url.split("/")[-1].split("?")[0]
            return (FIXTURES / "2026-11-29" / f"{channel}.html").read_text(encoding="utf-8")

        rows = collect_guide(date(2026, 11, 29), 1, read_fixture)
        show = [(row.channel, row.type, row.start_utc) for row in rows if row.title == "Биатлон с Дмитрием Губерниевым"]
        self.assertEqual(show, [
            ("Матч ТВ", "unknown", "2026-11-29T06:45:00Z"),
            ("Матч Арена", "unknown", "2026-11-29T06:45:00Z"),
            ("Матч Страна", "replay", "2026-11-29T17:40:00Z"),
        ])
        self.assertFalse(any(row.title.startswith("Триатлон") for row in rows))

    def test_show_repeat_is_matched_within_its_own_week(self):
        def read_fixture(url):
            channel, day = url.split("/")[-1].split("?date=")
            html = (FIXTURES / "2026-11-29" / f"{channel}.html").read_text(encoding="utf-8")
            label = f"{date.fromisoformat(day).day} {'ноября' if day < '2026-12' else 'декабря'}"
            html = html.replace("29 ноября", label)
            return html if day in ("2026-11-29", "2026-12-04") else html.replace("Биатлон", "Хоккей")

        rows = collect_guide(date(2026, 11, 29), 7, read_fixture)
        show = [(row.channel, row.type, row.start_utc) for row in rows if row.title == "Биатлон с Дмитрием Губерниевым"]
        self.assertEqual(show[3:], [
            ("Матч ТВ", "unknown", "2026-12-04T06:45:00Z"),
            ("Матч Арена", "unknown", "2026-12-04T06:45:00Z"),
            ("Матч Страна", "replay", "2026-12-04T17:40:00Z"),
        ])

    def test_rejects_a_page_for_a_different_date(self):
        html = (FIXTURES / "strana.html").read_text(encoding="utf-8")
        with self.assertRaises(ValueError):
            parse_guide(html, "strana", date(2026, 11, 29))

    def test_reports_a_day_the_channel_has_not_published_yet(self):
        not_published = ('<button class="m-weekly-inline-calendar__date '
                         'm-weekly-inline-calendar__date--availability-not-available">29 ноября,  вс</button>')
        html = (FIXTURES / "strana.html").read_text(encoding="utf-8").replace("<section", not_published + "<section", 1)
        with self.assertRaises(GuideNotPublished):
            parse_guide(html, "strana", date(2026, 11, 29))

        unpublished, errors = [], []
        rows = collect_guide(date(2026, 11, 29), 1, lambda url: html,
                             lambda url, error: errors.append(url), unpublished.append)
        self.assertEqual((rows, errors), ([], []))
        self.assertEqual(unpublished[0], "https://matchtv.ru/tvguide/matchtv?date=2026-11-29")
        self.assertEqual(len(unpublished), 5)

    def test_rejects_requests_larger_than_one_week(self):
        with self.assertRaises(ValueError):
            collect_guide(DAY, 8, lambda url: self.fail(url))
