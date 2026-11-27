from datetime import datetime, timezone
import unittest

from collectors.alert import explain_error, missing_races, plan_source_alerts, plan_workflow_alert


def status(checked_at="2026-11-27T04:15:00Z", **stale):
    sources = {}
    for name in ("match_guide", "match_live", "match_telegram", "okko_telegram", "okko_sport"):
        broken = stale.get(name)
        sources[name] = {"last_success": "2026-11-26T16:15:00Z" if broken else checked_at,
                         "stale": bool(broken), "errors": {f"https://example/{name}": broken} if broken else {}}
    return {"checked_at": checked_at, "sources": sources}


SEASON = {"events": [{"name": "Hochfilzen", "kind": "Кубок мира", "races": [
    {"race_id": "SPRINT-W", "title": "Спринт · Женщины · 7.5 км", "start_utc": "2026-12-04T10:25:00Z", "state": "scheduled"},
    {"race_id": "SPRINT-M", "title": "Спринт · Мужчины · 10 км", "start_utc": "2026-12-04T13:15:00Z", "state": "scheduled"},
]}]}


def schedule(*starts, kind="unknown"):
    return {"events": [{"broadcasts": [{"start_utc": start, "type": kind} for start in starts]}]}


def sources(before, after, **extra):
    args = {"ibu_before": None, "ibu_after": None, "schedule_before": None, "schedule_after": None, "season": None}
    args.update(extra)
    return plan_source_alerts(before, after, args["ibu_before"], args["ibu_after"],
                              args["schedule_before"], args["schedule_after"], args["season"])


class SourceAlertTests(unittest.TestCase):
    def test_a_source_that_breaks_is_explained_once(self):
        broken = status(okko_telegram="<urlopen error timed out>")
        messages = sources(status(), broken)
        self.assertEqual(len(messages), 1)
        text = messages[0]
        self.assertTrue(text.startswith("⚠️ Сломался источник: Расписание Okko из канала «Зимний спорт»"))
        self.assertIn("сайт не ответил вовремя", text)
        self.assertIn("остались от 26.11 в 19:15 МСК", text)
        self.assertIn("Okko Спорт", text)  # which backup still works
        self.assertEqual(sources(broken, status(checked_at="2026-11-27T16:15:00Z", okko_telegram="timed out")), [])

    def test_a_source_that_works_again_is_reported(self):
        messages = sources(status(match_guide="HTTP Error 403: Forbidden"), status())
        self.assertEqual(messages, ["✅ Снова работает: Телепрограмма «Матча» (matchtv.ru/tvguide). Данные обновлены."])

    def test_ibu_is_watched_too(self):
        messages = sources(status(), status(), ibu_before={"stale": False, "errors": {}},
                           ibu_after={"stale": True, "last_success": None, "errors": {"Events": "HTTP Error 404"}})
        self.assertEqual(len(messages), 1)
        self.assertIn("Данные IBU", messages[0])
        self.assertIn("ошибка 404", messages[0])
        self.assertIn("ещё ни разу", messages[0])

    def test_a_world_cup_race_without_an_airing_is_reported_when_it_comes_close(self):
        before = status(checked_at="2026-12-02T04:15:00Z")  # Friday's races are more than 48 h away
        after = status(checked_at="2026-12-02T16:15:00Z")
        found = schedule("2026-12-04T10:25:00Z")  # only the women's sprint has an airing
        messages = sources(before, after, schedule_before=found, schedule_after=found, season=SEASON)
        self.assertEqual(len(messages), 1)
        self.assertTrue(messages[0].startswith("🔎 Не нашёл трансляцию"))
        self.assertIn("Спринт · Мужчины · 10 км — Кубок мира, Hochfilzen, 04.12 в 16:15 МСК", messages[0])
        self.assertNotIn("Женщины", messages[0])
        later = status(checked_at="2026-12-03T04:15:00Z")
        self.assertEqual(sources(after, later, schedule_before=found, schedule_after=found, season=SEASON), [])


class MissingRacesTests(unittest.TestCase):
    NOW = datetime(2026, 12, 3, 4, 15, tzinfo=timezone.utc)

    def test_replays_do_not_count_and_near_times_do(self):
        replay = schedule("2026-12-04T13:15:00Z", kind="replay")
        self.assertIn("SPRINT-M", missing_races(replay, SEASON, self.NOW))
        near = schedule("2026-12-04T13:25:00Z", "2026-12-04T10:25:00Z")
        self.assertEqual(missing_races(near, SEASON, self.NOW), {})


class WorkflowAlertTests(unittest.TestCase):
    URL = "https://github.com/o/r/actions/runs/1"

    def jobs(self, *failed, job="collect"):
        return [{"name": job, "conclusion": "failure",
                 "steps": [{"name": name, "conclusion": "failure"} for name in failed]}]

    def test_failed_tests_are_explained_with_the_log_link(self):
        text = plan_workflow_alert("Сбор расписания", self.jobs("Run tests"), self.URL, None)
        self.assertTrue(text.startswith("⚠️ Сбой в GitHub Actions: «Сбор расписания»"))
        self.assertIn("Шаг: Run tests", text)
        self.assertIn("расписание не обновилось", text)
        self.assertIn("FAIL", text)
        self.assertIn(self.URL, text)

    def test_source_errors_alone_send_nothing(self):
        self.assertIsNone(plan_workflow_alert("Сбор расписания", self.jobs("Report source errors"), self.URL, None))

    def test_only_the_first_failure_in_a_row_when_asked(self):
        jobs = self.jobs("Send due messages", job="notify")
        self.assertIsNotNone(plan_workflow_alert("Напоминания", jobs, self.URL, "success", only_first=True))
        self.assertIsNone(plan_workflow_alert("Напоминания", jobs, self.URL, "failure", only_first=True))

    def test_an_unknown_step_still_gets_a_message(self):
        text = plan_workflow_alert("Публикация сайта", self.jobs("Run actions/deploy-pages@v4", job="deploy"), self.URL, None)
        self.assertIn("Публикация сайта на GitHub Pages", text)


class ExplainErrorTests(unittest.TestCase):
    def test_plain_words_for_common_errors(self):
        self.assertIn("5xx", explain_error("HTTP Error 502: Bad Gateway"))
        self.assertIn("вёрстку", explain_error("Не найден список передач"))
        self.assertIn("Tesseract", explain_error("Command '['tesseract', ...]' returned non-zero exit status 1"))


if __name__ == "__main__":
    unittest.main()
