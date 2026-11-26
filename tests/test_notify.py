from datetime import datetime, timedelta, timezone
import unittest

from collectors.notify import plan_messages


MSK = timezone(timedelta(hours=3))


def at(text):
    return datetime.fromisoformat(text).replace(tzinfo=MSK)


def utc(text):
    return at(text).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def broadcast(start, channel="Матч ТВ", kind="live", platform="MATCH", end=None):
    return {"broadcast_id": f"{platform}-{channel}-{start}", "platform": platform, "channel": channel,
            "type": kind, "start_utc": utc(start), "end_utc": utc(end) if end else None}


def event(title, *broadcasts, discipline="Спринт", gender="men", kind="race"):
    return {"event_id": title, "kind": kind, "title": title, "competition": title.split(".")[0],
            "discipline": discipline, "gender": gender, "category": "russian", "broadcasts": list(broadcasts)}


SCHEDULE = {"events": [
    event("Кубок Содружества. Спринт",
          broadcast("2026-11-28 07:45"), broadcast("2026-11-28 07:45", "Матч Арена"),
          broadcast("2026-11-28 20:50", "Матч Страна", "replay")),
    event("Кубок мира. Эстафета", broadcast("2026-11-28 15:10", "Okko", "unknown", "OKKO"),
          discipline="Одиночная смешанная эстафета", gender="mixed"),
    event("Кубок мира. Спринт", broadcast("2026-11-28 17:15", "Okko", "unknown", "OKKO")),
    event("Биатлон с Дмитрием Губерниевым", broadcast("2026-11-29 09:45"), broadcast("2026-11-29 09:45", "Матч Арена"),
          discipline=None, gender=None, kind="show"),
], "unmatched": []}


class DigestTests(unittest.TestCase):
    def test_nothing_before_nine(self):
        messages, state = plan_messages(SCHEDULE, {}, at("2026-11-28 08:55"))
        self.assertEqual((messages, state), ([], {}))

    def test_morning_digest_lists_the_rest_of_the_day_once(self):
        messages, state = plan_messages(SCHEDULE, {}, at("2026-11-28 09:05"))
        self.assertEqual(len(messages), 1)
        digest = messages[0]
        self.assertTrue(digest.startswith("📺 Биатлон сегодня, суббота, 28 ноября"))
        self.assertIn("15:10 — Одиночная смешанная эстафета · Кубок мира — Okko", digest)
        self.assertIn("20:50 — Спринт · Мужчины · Кубок Содружества — Матч Страна (повтор)", digest)
        self.assertNotIn("07:45", digest)
        again, _ = plan_messages(SCHEDULE, state, at("2026-11-28 09:15"))
        self.assertEqual(again, [])

    def test_a_day_without_airings_sends_no_digest(self):
        empty = {"events": [], "unmatched": []}
        messages, state = plan_messages(empty, {}, at("2026-11-30 09:05"))
        self.assertEqual(messages, [])
        self.assertEqual(state["digest"], "2026-11-30")


class ReminderTests(unittest.TestCase):
    def setUp(self):
        _, self.state = plan_messages(SCHEDULE, {}, at("2026-11-28 09:05"))

    def test_reminds_about_45_minutes_before_and_only_once(self):
        early, state = plan_messages(SCHEDULE, self.state, at("2026-11-28 14:20"))
        self.assertEqual(early, [])
        messages, state = plan_messages(SCHEDULE, state, at("2026-11-28 14:30"))
        self.assertEqual(messages, ["⏰ Через 40 мин, в 15:10 МСК\nОдиночная смешанная эстафета · Кубок мира\nOkko"])
        later, _ = plan_messages(SCHEDULE, state, at("2026-11-28 14:40"))
        self.assertEqual(later, [])

    def test_a_late_run_still_reminds_before_the_start_but_not_after(self):
        messages, state = plan_messages(SCHEDULE, self.state, at("2026-11-28 15:02"))
        self.assertEqual(len(messages), 1)
        self.assertTrue(messages[0].startswith("⏰ Через 8 мин, в 15:10 МСК"))
        missed, _ = plan_messages(SCHEDULE, self.state, at("2026-11-28 15:11"))
        self.assertEqual(missed, [])

    def test_no_reminders_for_replays(self):
        messages, _ = plan_messages(SCHEDULE, self.state, at("2026-11-28 20:20"))
        self.assertEqual(messages, [])

    def test_simultaneous_channels_share_one_reminder(self):
        _, state = plan_messages(SCHEDULE, {}, at("2026-11-29 09:00"))
        messages, _ = plan_messages(SCHEDULE, state, at("2026-11-29 09:10"))
        self.assertEqual(messages, ["⏰ Через 35 мин, в 09:45 МСК\nБиатлон с Дмитрием Губерниевым\nМатч ТВ, Матч Арена"])

    def test_old_reminders_are_forgotten(self):
        _, state = plan_messages(SCHEDULE, self.state, at("2026-11-28 14:30"))
        _, state = plan_messages(SCHEDULE, state, at("2026-11-30 10:00"))
        self.assertEqual(state["sent"], {})


if __name__ == "__main__":
    unittest.main()
