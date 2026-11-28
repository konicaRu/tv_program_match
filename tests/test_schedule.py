from datetime import date, datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from collectors.match_guide import collect_guide
from collectors.match_tv import Broadcast, parse_broadcasts
from collectors.schedule import SourceRun, build_schedule, parse_race, publish


FIXTURES = Path(__file__).parent / "fixtures" / "match" / "guide"
LIVE_FIXTURES = Path(__file__).parent / "fixtures" / "match"
NOW = datetime(2026, 11, 27, 9, 0, tzinfo=timezone.utc)
TODAY = date(2026, 11, 27)


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

    def test_a_replay_days_later_stays_with_its_race(self):
        live = "Биатлон. Альфа-Банк Кубок Содружества. Спринт. Мужчины. Прямая трансляция из Ханты-Мансийска"
        recording = "Биатлон. Альфа-Банк Кубок Содружества. Спринт. Мужчины. Трансляция из Ханты-Мансийска"
        schedule = build_schedule([
            airing(live, "2026-11-28T04:45:00Z", kind="live"),
            airing(recording, "2026-12-02T06:00:00Z", "Матч Арена", "replay"),
        ], NOW)
        self.assertEqual(len(schedule["events"]), 1)
        self.assertEqual([row["channel"] for row in schedule["events"][0]["broadcasts"]], ["Матч ТВ", "Матч Арена"])

    def test_event_date_is_the_moscow_date_of_the_first_airing(self):
        rows = [airing("Биатлон. Кубок мира. Спринт. Мужчины", "2026-12-04T22:30:00Z")]
        self.assertEqual(build_schedule(rows, NOW)["events"][0]["event_id"], "kubok-mira-sprint-men-2026-12-05")


def live_rows():
    html = (LIVE_FIXTURES / "2026-11-28.html").read_text(encoding="utf-8")
    return parse_broadcasts(html, "https://matchtv.ru/biathlon/live?date=28-11-2026")


def okko_row(start_utc="2026-11-28T12:10:00Z"):
    return Broadcast(
        broadcast_id=f"okko-single-mixed-relay-{start_utc}", platform="OKKO", channel="Okko",
        title="Биатлон. Кубок мира. Одиночная смешанная эстафета", type="unknown", start_utc=start_utc,
        end_utc=None, url="https://t.me/okkowintersport/2494", source_url="https://t.me/okkowintersport/2494",
    )


def run(name, rows=(), errors=None, unpublished=()):
    return SourceRun(name, list(rows), dict(errors or {}), list(unpublished))


def clean_runs():
    return [run("match_guide", guide_rows()), run("match_live", live_rows()), run("okko_telegram", [okko_row()])]


def read(out, name):
    return json.loads((out / name).read_text(encoding="utf-8"))


class PublishTests(unittest.TestCase):
    def test_merges_sources_and_keeps_the_richer_copy_of_a_shared_airing(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            self.assertTrue(publish(out, clean_runs(), NOW, TODAY))
            schedule, status = read(out, "schedule.json"), read(out, "status.json")
            stored = sorted(path.name for path in (out / "sources").iterdir())
        self.assertEqual(stored, ["match_guide.json", "match_live.json", "okko_telegram.json"])
        rows = [row for event in schedule["events"] for row in event["broadcasts"]]
        self.assertEqual(len(rows), 9)  # 8 from the guide (2 also in the live page) + 1 from Okko
        sprint = next(row for row in rows if row["broadcast_id"] == "match-matchtv-1795841100")
        self.assertEqual((sprint["channel"], sprint["type"], sprint["end_utc"]), ("Матч ТВ", "live", "2026-11-28T07:15:00Z"))
        self.assertEqual(sprint["url"], "https://matchtv.ru/biathlon/matchtvvideo_NI2386909_translation_Alfa_Bank_Kubok_Sodruzhestva_Sprint_Muzhchiny")
        self.assertEqual(status["stale"], False)
        self.assertEqual(status["sources"]["okko_telegram"], {"last_success": "2026-11-27T09:00:00Z", "stale": False, "errors": {}})

    def test_a_failed_source_keeps_its_last_airings_and_is_reported_alone(self):
        later = NOW.replace(hour=15)
        failure = {"https://t.me/s/okkowintersport": "timed out"}
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            publish(out, clean_runs(), NOW, TODAY)
            runs = [run("match_guide", guide_rows()[:-1]), run("match_live", live_rows()), run("okko_telegram", errors=failure)]
            self.assertTrue(publish(out, runs, later, TODAY))
            schedule, status = read(out, "schedule.json"), read(out, "status.json")
        rows = [row for event in schedule["events"] for row in event["broadcasts"]]
        self.assertIn("OKKO", {row["platform"] for row in rows})
        self.assertEqual(len(rows), 8)
        self.assertEqual(status["stale"], True)
        self.assertEqual(status["errors"], failure)
        self.assertEqual(status["last_success"], "2026-11-27T15:00:00Z")
        self.assertEqual(status["sources"]["okko_telegram"], {"last_success": "2026-11-27T09:00:00Z", "stale": True, "errors": failure})
        self.assertEqual(status["sources"]["match_guide"]["last_success"], "2026-11-27T15:00:00Z")

    def test_cached_airings_from_past_days_are_dropped(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            publish(out, clean_runs(), NOW, TODAY)
            runs = [run("match_guide", []), run("match_live", []), run("okko_telegram", errors={"x": "down"})]
            publish(out, runs, NOW.replace(day=29), date(2026, 11, 29))
            schedule = read(out, "schedule.json")
        self.assertEqual(schedule["events"], [])

    def test_does_not_rewrite_unchanged_data(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            self.assertTrue(publish(out, clean_runs(), NOW, TODAY))
            files = sorted(out.rglob("*.json"))
            before = [path.read_bytes() for path in files]
            self.assertFalse(publish(out, clean_runs(), NOW.replace(hour=19), TODAY))
            self.assertEqual([path.read_bytes() for path in files], before)

    def test_a_repeated_failure_is_recorded_once(self):
        failure = {"https://matchtv.ru/tvguide/arena": "HTTP 503"}
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            publish(out, clean_runs(), NOW, TODAY)
            broken = [run("match_guide", errors=failure), run("match_live", live_rows()), run("okko_telegram", [okko_row()])]
            self.assertTrue(publish(out, broken, NOW.replace(hour=15), TODAY))
            self.assertFalse(publish(out, broken, NOW.replace(hour=19), TODAY))
            self.assertTrue(publish(out, clean_runs(), NOW.replace(hour=20), TODAY))
            status = read(out, "status.json")
        self.assertEqual((status["stale"], status["sources"]["match_guide"]["last_success"]), (False, "2026-11-27T20:00:00Z"))

    def test_a_source_without_its_own_file_falls_back_to_the_old_schedule(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            publish(out, clean_runs(), NOW, TODAY)
            (out / "sources" / "okko_telegram.json").unlink()
            runs = [run("match_guide", guide_rows()), run("match_live", live_rows()), run("okko_telegram", errors={"x": "down"})]
            publish(out, runs, NOW.replace(hour=15), TODAY)
            rows = [row for event in read(out, "schedule.json")["events"] for row in event["broadcasts"]]
        self.assertEqual([row["broadcast_id"] for row in rows if row["platform"] == "OKKO"], [okko_row().broadcast_id])

    def test_first_failed_run_still_publishes_the_other_sources(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            runs = [run("match_guide", errors={"https://matchtv.ru/tvguide/arena": "HTTP 503"}),
                    run("match_live", live_rows()), run("okko_telegram", [okko_row()])]
            publish(out, runs, NOW, TODAY)
            schedule, status = read(out, "schedule.json"), read(out, "status.json")
        self.assertEqual(len([row for event in schedule["events"] for row in event["broadcasts"]]), 3)
        self.assertIsNone(status["sources"]["match_guide"]["last_success"])


if __name__ == "__main__":
    unittest.main()
