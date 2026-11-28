from datetime import date
from pathlib import Path
import unittest

from dataclasses import replace

from bs4 import BeautifulSoup

from collectors.match_guide import CHANNEL_NAMES, GuideIncomplete, GuideNotPublished, TRANSMISSION, collect_guide, parse_guide


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

    def test_a_race_recording_days_after_the_live_airing_is_a_replay(self):
        live = "Биатлон. Альфа-Банк Кубок Содружества. Спринт. Мужчины. Прямая трансляция из Ханты-Мансийска"
        recording = "Биатлон. Альфа-Банк Кубок Содружества. Спринт. Мужчины. Трансляция из Ханты-Мансийска"
        pages = {"2026-11-28": [("matchtv", "07:45", live)], "2026-12-02": [("arena", "09:00", recording)],
                 "2026-12-09": [("arena", "09:00", recording)]}

        def read_page(url):
            channel, day = url.split("/")[-1].split("?date=")
            html = (FIXTURES / "strana.html").read_text(encoding="utf-8")
            html = html.replace("28 ноября", f"{date.fromisoformat(day).day} {'ноября' if day < '2026-12' else 'декабря'}")
            html = html.replace("Телепрограмма Матч Страна", f"Телепрограмма {CHANNEL_NAMES[channel]}")
            page = BeautifulSoup(html, "html.parser")
            items = page.select(f".p-tv-guide-schedule-channel .{TRANSMISSION}")
            for item in items[1:]:
                item.decompose()
            item = items[0]
            rows = [row for row in pages.get(day, []) if row[0] == channel]
            if not rows:
                item.select_one(f".{TRANSMISSION}__title").string = "Футбол"
                return str(page)
            item.select_one(f".{TRANSMISSION}__time-block-time").string = rows[0][1]
            item.select_one(f".{TRANSMISSION}__title").string = rows[0][2]
            return str(page)

        early = collect_guide(DAY, 7, read_page)
        self.assertEqual([(row.channel, row.type) for row in early], [("Матч ТВ", "live"), ("Матч Арена", "replay")])
        late = collect_guide(date(2026, 12, 3), 7, read_page, previous=())  # a week on, with no live airing
        self.assertEqual([row.type for row in late], ["unknown"])

    def test_at_night_keeps_the_stored_airings_of_today_s_incomplete_page(self):
        def read_fixture(url):
            channel = url.split("/")[-1].split("?")[0]
            return (FIXTURES / f"{channel}.html").read_text(encoding="utf-8")

        full = collect_guide(DAY, 1, read_fixture, today=DAY)
        page = BeautifulSoup(read_fixture("arena"), "html.parser")
        items = page.select(f".p-tv-guide-schedule-channel .{TRANSMISSION}")
        times = [item.select_one(f".{TRANSMISSION}__time-block-time").get_text(strip=True) for item in items]
        midnight = next(index for index in range(1, len(times)) if times[index] < times[index - 1])
        for item in items[:midnight]:  # what the site shows at 03:00: only the hours after midnight
            item.decompose()
        night = str(page)
        with self.assertRaises(GuideIncomplete):
            parse_guide(night, "arena", DAY, today=DAY)

        def read_at_night(url):
            return night if "/arena?" in url else read_fixture(url.split("/")[-1].split("?")[0])

        elsewhere = replace(full[0], broadcast_id="match-arena-1", source_url="https://matchtv.ru/tvguide/arena?date=2026-11-27")
        errors = []
        rows = collect_guide(DAY, 1, read_at_night, lambda url, error: errors.append(url),
                             previous=full + [elsewhere], today=DAY)
        self.assertEqual(errors, [])
        self.assertEqual(rows, full)
        self.assertIn("Матч Арена", {row.channel for row in rows})

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
