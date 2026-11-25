from datetime import date, datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from collectors.match_guide import collect_guide
from collectors.match_tv import Broadcast
from collectors.schedule import build_schedule, parse_race, publish


FIXTURES = Path(__file__).parent / "fixtures" / "match" / "guide"
NOW = datetime(2026, 11, 27, 9, 0, tzinfo=timezone.utc)


def guide_rows():
    def read_fixture(url):
        return (FIXTURES / f"{url.split('/')[-1].split('?')[0]}.html").read_text(encoding="utf-8")
    return collect_guide(date(2026, 11, 28), 1, read_fixture)


def airing(title, start_utc, channel="Матч ТВ", kind="unknown"):
    return Broadcast(
        broadcast_id=f"match-test-{start_utc}", platform="MATCH", channel=channel, title=title,
        type=kind, start_utc=start_utc, end_utc=None, url=None, source_url="https://matchtv.ru/tvguide",
    )


class ParseRaceTests(unittest.TestCase):
    def test_reads_competition_discipline_and_gender(self):
        for title in [
            "Биатлон. Альфа-Банк Кубок Содружества. Спринт. Мужчины. Прямая трансляция из Ханты-Мансийска",
            "Биатлон. Альфа-Банк Кубок Содружества. Спринт. Мужчины. Трансляция из Ханты-Мансийска",
            "Биатлон. Альфа-Банк Кубок Содружества. Спринт. Мужчины",
        ]:
            with self.subTest(title=title):
                race = parse_race(title)
                self.assertEqual(race.competition, "Альфа-Банк Кубок Содружества")
                self.assertEqual(race.discipline, "Спринт")
                self.assertEqual(race.gender, "men")
                self.assertEqual(race.category, "russian")

    def test_mixed_relay_needs_no_gender(self):
        race = parse_race("Биатлон. Кубок мира. Смешанная эстафета. Прямая трансляция")
        self.assertEqual((race.discipline, race.gender, race.category), ("Смешанная эстафета", "mixed", "international"))

    def test_leaves_unclear_titles_unparsed(self):
        for title in [
            "Биатлон. Альфа-Банк Кубок Содружества. Спринт",
            "Биатлон. Обзор недели",
            "Биатлон. Спринт. Мужчины",
            "Лыжные гонки. Кубок мира. Спринт. Мужчины",
        ]:
            with self.subTest(title=title):
                self.assertIsNone(parse_race(title))


class BuildScheduleTests(unittest.TestCase):
    def test_groups_all_airings_of_a_race_into_one_event(self):
        schedule = build_schedule(guide_rows(), NOW)
        self.assertEqual(schedule["generated_at"], "2026-11-27T09:00:00Z")
        self.assertEqual(schedule["unmatched"], [])
        self.assertEqual([event["event_id"] for event in schedule["events"]], [
            "alfa-bank-kubok-sodruzhestva-sprint-men-2026-11-28",
            "alfa-bank-kubok-sodruzhestva-sprint-women-2026-11-28",
        ])
        men = schedule["events"][0]
        self.assertEqual((men["kind"], men["category"]), ("race", "russian"))
        self.assertEqual([(row["channel"], row["type"]) for row in men["broadcasts"]], [
            ("Матч ТВ", "live"), ("Матч Арена", "live"), ("Матч Страна", "replay"), ("Матч Арена", "replay"),
        ])
        self.assertTrue(all(row["verified_at"] == "2026-11-27T09:00:00Z" for row in men["broadcasts"]))
        ids = [row["broadcast_id"] for event in schedule["events"] for row in event["broadcasts"]]
        self.assertEqual(len(ids), 8)
        self.assertEqual(len(set(ids)), 8)

    def test_groups_the_weekly_guberniev_show_by_week(self):
        title = "Биатлон с Дмитрием Губерниевым"
        rows = [
            airing(title, "2026-11-29T06:45:00Z"),
            airing(title, "2026-11-29T17:40:00Z", channel="Матч Страна", kind="replay"),
            airing(title, "2026-12-06T06:45:00Z"),
        ]
        schedule = build_schedule(rows, NOW)
        self.assertEqual(schedule["unmatched"], [])
        self.assertEqual([(event["event_id"], event["kind"], len(event["broadcasts"])) for event in schedule["events"]], [
            ("biatlon-s-dmitriem-gubernievym-2026-11-29", "show", 2),
            ("biatlon-s-dmitriem-gubernievym-2026-12-06", "show", 1),
        ])
        self.assertEqual(schedule["events"][0]["title"], title)

    def test_keeps_unclear_airings_separate(self):
        rows = [airing("Биатлон. Обзор недели", "2026-11-28T10:00:00Z")]
        schedule = build_schedule(rows, NOW)
        self.assertEqual(schedule["events"], [])
        self.assertEqual([row["title"] for row in schedule["unmatched"]], ["Биатлон. Обзор недели"])

    def test_same_race_name_on_different_weekends_is_two_events(self):
        title = "Биатлон. Кубок мира. Спринт. Женщины. Прямая трансляция"
        rows = [airing(title, "2026-12-04T11:00:00Z"), airing(title, "2026-12-11T11:00:00Z")]
        events = build_schedule(rows, NOW)["events"]
        self.assertEqual([event["event_id"] for event in events], [
            "kubok-mira-sprint-women-2026-12-04", "kubok-mira-sprint-women-2026-12-11",
        ])

    def test_event_date_is_the_moscow_date_of_the_first_airing(self):
        rows = [airing("Биатлон. Кубок мира. Спринт. Мужчины", "2026-12-04T22:30:00Z")]
        self.assertEqual(build_schedule(rows, NOW)["events"][0]["event_id"], "kubok-mira-sprint-men-2026-12-05")


class PublishTests(unittest.TestCase):
    def test_writes_schedule_and_status_after_a_clean_run(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            publish(out, guide_rows(), {}, NOW)
            schedule = json.loads((out / "schedule.json").read_text(encoding="utf-8"))
            status = json.loads((out / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(len(schedule["events"]), 2)
        self.assertEqual(status, {
            "checked_at": "2026-11-27T09:00:00Z",
            "last_success": "2026-11-27T09:00:00Z",
            "stale": False,
            "errors": {},
            "unpublished": [],
        })

    def test_keeps_last_valid_schedule_when_a_source_fails(self):
        later = NOW.replace(hour=15)
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            publish(out, guide_rows(), {}, NOW)
            before = (out / "schedule.json").read_bytes()
            publish(out, guide_rows()[:1], {"https://matchtv.ru/tvguide/strana": "timed out"}, later,
                    ["https://matchtv.ru/tvguide/strana?date=2026-11-30"])
            self.assertEqual((out / "schedule.json").read_bytes(), before)
            status = json.loads((out / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(sorted(path.name for path in out.iterdir()), ["schedule.json", "status.json"])
        self.assertEqual(status, {
            "checked_at": "2026-11-27T15:00:00Z",
            "last_success": "2026-11-27T09:00:00Z",
            "stale": True,
            "errors": {"https://matchtv.ru/tvguide/strana": "timed out"},
            "unpublished": ["https://matchtv.ru/tvguide/strana?date=2026-11-30"],
        })

    def test_does_not_rewrite_unchanged_data(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            self.assertTrue(publish(out, guide_rows(), {}, NOW))
            before = [(out / name).read_bytes() for name in ("schedule.json", "status.json")]
            self.assertFalse(publish(out, guide_rows(), {}, NOW.replace(hour=19)))
            self.assertEqual([(out / name).read_bytes() for name in ("schedule.json", "status.json")], before)
            self.assertTrue(publish(out, guide_rows()[:-1], {}, NOW.replace(hour=20)))
            status = json.loads((out / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["last_success"], "2026-11-27T20:00:00Z")

    def test_a_repeated_failure_is_recorded_once(self):
        failure = {"https://matchtv.ru/tvguide/arena": "HTTP 503"}
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            publish(out, guide_rows(), {}, NOW)
            self.assertTrue(publish(out, [], failure, NOW.replace(hour=15)))
            self.assertFalse(publish(out, [], failure, NOW.replace(hour=19)))
            recovered = publish(out, guide_rows(), {}, NOW.replace(hour=20))
            status = json.loads((out / "status.json").read_text(encoding="utf-8"))
        self.assertTrue(recovered)
        self.assertEqual((status["stale"], status["last_success"]), (False, "2026-11-27T20:00:00Z"))

    def test_first_failed_run_writes_only_status(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            publish(out, [], {"https://matchtv.ru/tvguide/arena": "HTTP 503"}, NOW)
            self.assertFalse((out / "schedule.json").exists())
            status = json.loads((out / "status.json").read_text(encoding="utf-8"))
        self.assertIsNone(status["last_success"])
        self.assertTrue(status["stale"])


if __name__ == "__main__":
    unittest.main()
