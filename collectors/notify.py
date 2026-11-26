"""Telegram reminders about biathlon airings: a 09:00 digest and a note before each first showing.

Nothing is sent before 09:00 Moscow time, and airings that start before 09:00 get no reminder.
Replays appear in the digest but get no reminder. Only airings are mentioned, never results.
The bot token and chat id come from the TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID secrets.
"""

import argparse
from datetime import datetime, time, timedelta, timezone
import json
import os
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen


MOSCOW = timezone(timedelta(hours=3))
QUIET_UNTIL = time(9, 0)
LEAD = timedelta(minutes=45)
APP_URL = "https://konicaru.github.io/tv_program_match/"
GENDERS = {"men": "Мужчины", "women": "Женщины"}
WEEKDAYS = "понедельник вторник среда четверг пятница суббота воскресенье".split()
MONTHS = "января февраля марта апреля мая июня июля августа сентября октября ноября декабря".split()


def headline(event: dict) -> str:
    if event.get("kind") == "race" and event.get("discipline"):
        parts = [event["discipline"]]
        if event.get("gender") in GENDERS:
            parts.append(GENDERS[event["gender"]])
        if event.get("competition"):
            parts.append(event["competition"])
        return " · ".join(parts)
    return event["title"]


def airings(schedule: dict) -> list[tuple[datetime, dict, dict]]:
    """Every airing with its Moscow start time and its event."""
    events = list(schedule.get("events", []))
    events += [{"event_id": row["broadcast_id"], "kind": "show", "title": row.get("title", ""), "broadcasts": [row]}
               for row in schedule.get("unmatched", [])]
    rows = []
    for event in events:
        for row in event["broadcasts"]:
            start = datetime.fromisoformat(row["start_utc"].replace("Z", "+00:00")).astimezone(MOSCOW)
            rows.append((start, row, event))
    return sorted(rows, key=lambda item: (item[0], item[2]["event_id"]))


def grouped(rows: list[tuple[datetime, dict, dict]]) -> list[tuple[datetime, dict, list[dict]]]:
    """Airings of one event at one time on several channels become one line."""
    groups: dict[tuple[datetime, str], tuple[datetime, dict, list[dict]]] = {}
    for start, row, event in rows:
        groups.setdefault((start, event["event_id"]), (start, event, []))[2].append(row)
    return list(groups.values())


def channels(rows: list[dict]) -> str:
    return ", ".join(dict.fromkeys(row["channel"] for row in rows))


def plan_messages(schedule: dict, state: dict, now: datetime) -> tuple[list[str], dict]:
    """Messages to send now and the state to keep for the next run."""
    now = now.astimezone(MOSCOW)
    if now.time() < QUIET_UNTIL:
        return [], state
    sent = {key: start for key, start in state.get("sent", {}).items()
            if datetime.fromisoformat(start) > now - timedelta(days=1)}
    state = {**state, "sent": sent}
    messages = []

    today = now.date().isoformat()
    if state.get("digest") != today:
        state["digest"] = today
        lines = []
        for start, event, rows in grouped([item for item in airings(schedule)
                                           if item[0].date() == now.date() and item[0] >= now]):
            replay = " (повтор)" if all(row["type"] == "replay" for row in rows) else ""
            lines.append(f"{start:%H:%M} — {headline(event)} — {channels(rows)}{replay}")
        if lines:
            day = f"{WEEKDAYS[now.weekday()]}, {now.day} {MONTHS[now.month - 1]}"
            messages.append(f"📺 Биатлон сегодня, {day} (МСК)\n\n" + "\n".join(lines) + f"\n\n{APP_URL}")

    upcoming = [item for item in airings(schedule)
                if item[1]["type"] != "replay" and now < item[0] < now + LEAD and item[0].time() >= QUIET_UNTIL]
    for start, event, rows in grouped(upcoming):
        key = f"{event['event_id']}@{start.isoformat()}"
        if key in sent:
            continue
        sent[key] = start.isoformat()
        minutes = round((start - now).total_seconds() / 60)
        messages.append(f"⏰ Через {minutes} мин, в {start:%H:%M} МСК\n{headline(event)}\n{channels(rows)}")
    return messages, state


def send(token: str, chat_id: str, text: str) -> None:
    data = urlencode({"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}).encode()
    with urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=20) as response:
        if not json.loads(response.read().decode("utf-8")).get("ok"):
            raise OSError("Telegram не принял сообщение")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schedule", type=Path, default=Path("public/data/schedule.json"))
    parser.add_argument("--state", type=Path, default=Path(".notify-state/state.json"))
    parser.add_argument("--test", action="store_true", help="Отправить проверочное сообщение")
    args = parser.parse_args()
    token, chat_id = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Секреты TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID не заданы — уведомления выключены")
        return
    if args.test:
        send(token, chat_id, f"✅ Бот BIATHLON TV подключён. Сводка — в 09:00 МСК, напоминания — примерно за 40 минут до эфира.\n{APP_URL}")
        print("Проверочное сообщение отправлено")
        return

    try:
        state = json.loads(args.state.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    schedule = json.loads(args.schedule.read_text(encoding="utf-8"))
    messages, state = plan_messages(schedule, state, datetime.now(timezone.utc))
    for text in messages:
        send(token, chat_id, text)
    args.state.parent.mkdir(parents=True, exist_ok=True)
    args.state.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    print(f"Отправлено сообщений: {len(messages)}")


if __name__ == "__main__":
    main()
