from datetime import date
from pathlib import Path
import unittest

from collectors.match_guide import collect_guide, parse_guide


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

    def test_rejects_a_page_for_a_different_date(self):
        html = (FIXTURES / "strana.html").read_text(encoding="utf-8")
        with self.assertRaises(ValueError):
            parse_guide(html, "strana", date(2026, 11, 29))

    def test_rejects_requests_larger_than_one_week(self):
        with self.assertRaises(ValueError):
            collect_guide(DAY, 8, lambda url: self.fail(url))
