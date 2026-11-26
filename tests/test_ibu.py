from datetime import date, datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from collectors.ibu import API, SITE, collect_ibu, publish_ibu, season_id


FIXTURES = Path(__file__).parent / "fixtures" / "ibu"
TODAY = date(2026, 11, 26)
NOW = datetime(2026, 11, 26, 18, 0, tzinfo=timezone.utc)
API_FILES = {
    "Events?SeasonId=2627&Level=1&Language=EN": "events-2627.json",
    "Competitions?EventId=BT2627SWRLCP01&Language=EN": "competitions-BT2627SWRLCP01.json",
    "Competitions?EventId=BT2627SWRLCP02&Language=EN": "competitions-BT2627SWRLCP02.json",
    "CupResults?CupId=BT2627SWRLCP__SMTS&Language=EN": "cup-BT2627SWRLCP__SMTS.json",
    "CupResults?CupId=BT2627SWRLCP__SWTS&Language=EN": "cup-BT2627SWRLCP__SWTS.json",
    "Results?RaceId=BT2627SWRLCP01SWIN&Language=EN": "race-BT2627SWRLCP01SWIN.json",
    "Results?RaceId=BT2627SWRLCP01SMIN&Language=EN": "race-BT2627SWRLCP01SMIN.json",
    "Results?RaceId=BT2627SWRLCP01MXRL&Language=EN": "race-BT2627SWRLCP01MXRL.json",
    "Results?RaceId=BT2627SWRLCP01MXSR&Language=EN": "race-BT2627SWRLCP01MXRL.json",
}


def fake_api(missing=()):
    requested = []

    def get_json(url):
        requested.append(url)
        path = url.removeprefix(API)
        if path in missing:
            raise OSError("HTTP 503")
        if path.startswith("Competitions?") and path not in API_FILES:
            return []
        return json.loads((FIXTURES / API_FILES[path]).read_text(encoding="utf-8"))
    get_json.requested = requested
    return get_json


def page(url):
    if url == f"{SITE}/startlist/BT2627SWRLCP01SWIN":
        return (FIXTURES / "page-startlist-BT2627SWRLCP01SWIN.html").read_text(encoding="utf-8")
    if url == f"{SITE}/calendar":
        return (FIXTURES / "page-calendar.html").read_text(encoding="utf-8")
    raise OSError("HTTP 404")


class SeasonTests(unittest.TestCase):
    def test_season_runs_from_autumn_to_spring(self):
        self.assertEqual(season_id(date(2026, 11, 26)), "2627")
        self.assertEqual(season_id(date(2027, 3, 1)), "2627")
        self.assertEqual(season_id(date(2027, 7, 1)), "2728")

    def test_calendar_names_races_in_russian_with_their_state(self):
        files, errors = collect_ibu(TODAY, fake_api(), page)
        self.assertEqual(errors, {})
        season = files["season.json"]
        self.assertEqual((season["season_id"], season["season"]), ("2627", "2026/2027"))
        self.assertEqual(len(season["events"]), 11)
        first = season["events"][0]
        self.assertEqual((first["event_id"], first["name"], first["country"]), ("BT2627SWRLCP01", "Kontiolahti", "FIN"))
        self.assertEqual([race["title"] for race in first["races"]], [
            "Индивидуальная гонка · Мужчины · 20 км",
            "Индивидуальная гонка · Женщины · 15 км",
            "Одиночная смешанная эстафета",
            "Смешанная эстафета · 4x6 км",
            "Спринт · Мужчины · 10 км",
            "Спринт · Женщины · 7.5 км",
        ])
        men, women = first["races"][:2]
        self.assertEqual((men["state"], men["has_results"], men["has_startlist"]), ("results", True, True))
        self.assertEqual((women["state"], women["has_results"], women["has_startlist"]), ("startlist", False, True))
        self.assertEqual(women["start_utc"], "2026-11-27T15:15:00Z")
        self.assertEqual(women["url"], f"{SITE}/startlist/BT2627SWRLCP01SWIN")
        self.assertEqual(first["races"][4]["state"], "scheduled")


class RaceTests(unittest.TestCase):
    def test_fetches_start_lists_and_results_for_races_near_today(self):
        get_json = fake_api()
        files, _ = collect_ibu(TODAY, get_json, page)
        races = sorted(name for name in files if name.startswith("races/"))
        self.assertEqual(races, [f"races/BT2627SWRLCP01{code}.json" for code in ("MXRL", "MXSR", "SMIN", "SWIN")])

        startlist = files["races/BT2627SWRLCP01SWIN.json"]
        self.assertEqual((startlist["is_result"], len(startlist["rows"])), (False, 103))
        self.assertEqual(startlist["rows"][0], {
            "bib": "1", "name": "TODOROVA Milena", "nat": "BUL", "is_team": False, "leg": None,
            "start_time": "2026-11-27T15:15:30Z", "start_info": "17:15:30", "rank": None, "total_time": None,
            "behind": None, "shootings": None, "wc_points": None, "irm": None,
        })

        results = files["races/BT2627SWRLCP01SMIN.json"]
        self.assertTrue(results["is_result"])
        ranked = [row for row in results["rows"] if row["rank"]]
        self.assertEqual(ranked[0]["rank"], "1")
        self.assertTrue(all(row["total_time"] for row in ranked))
        self.assertIn("DNS", {row["irm"] for row in results["rows"]})

        relay = files["races/BT2627SWRLCP01MXRL.json"]
        self.assertTrue(all(row["is_team"] for row in relay["rows"]))

    def test_falls_back_to_the_race_page_when_the_api_fails(self):
        files, errors = collect_ibu(TODAY, fake_api(missing={"Results?RaceId=BT2627SWRLCP01SWIN&Language=EN"}), page)
        self.assertEqual(len(files["races/BT2627SWRLCP01SWIN.json"]["rows"]), 103)
        self.assertEqual(errors, {})

    def test_reports_a_race_neither_source_gives(self):
        files, errors = collect_ibu(TODAY, fake_api(missing={"Results?RaceId=BT2627SWRLCP01SMIN&Language=EN"}), page)
        self.assertNotIn("races/BT2627SWRLCP01SMIN.json", files)
        self.assertEqual(list(errors), [f"{API}Results?RaceId=BT2627SWRLCP01SMIN&Language=EN"])

    def test_calendar_falls_back_to_the_calendar_page(self):
        files, errors = collect_ibu(TODAY, fake_api(missing={"Events?SeasonId=2627&Level=1&Language=EN"}), page)
        self.assertEqual(errors, {})
        self.assertEqual(len(files["season.json"]["events"]), 11)
        self.assertEqual(len(files["season.json"]["events"][0]["races"]), 6)


class StandingsTests(unittest.TestCase):
    def test_standings_for_men_and_women(self):
        files, _ = collect_ibu(TODAY, fake_api(), page)
        standings = files["standings.json"]
        men, women = standings["men"], standings["women"]
        self.assertEqual((men["race_count"], men["total_races"], len(men["rows"])), (1, 21, 40))
        self.assertEqual(set(men["rows"][0]), {"rank", "name", "nat", "score", "ibu_id"})
        self.assertEqual(women["rows"], [])
        self.assertEqual(men["url"], f"{SITE}/standings")


class PublishTests(unittest.TestCase):
    def test_writes_files_and_keeps_old_ones_on_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            files, errors = collect_ibu(TODAY, fake_api(), page)
            self.assertTrue(publish_ibu(out, files, errors, NOW))
            self.assertFalse(publish_ibu(out, files, errors, NOW.replace(hour=20)))
            broken = {"https://bw.biathlonresults.com/x": "HTTP 503"}
            self.assertTrue(publish_ibu(out, {}, broken, NOW.replace(hour=21)))
            status = json.loads((out / "status.json").read_text(encoding="utf-8"))
            self.assertTrue((out / "races" / "BT2627SWRLCP01SWIN.json").exists())
        self.assertEqual(status, {"last_success": "2026-11-26T18:00:00Z", "stale": True, "errors": broken})


if __name__ == "__main__":
    unittest.main()
