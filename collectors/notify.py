"""Telegram reminders about biathlon airings.

- Tuesday 09:00: the week's races by competition, with where to watch them (World Cup races
  come from the IBU calendar, as Okko posts a stage's schedule only mid-week).
- 09:00: the day's airings.  19:00: tomorrow's airings.
- About 40 minutes before each first showing: a reminder.

Nothing is sent before 09:00 Moscow time, and airings that start before 09:00 get no reminder.
Replays appear in the digests but get no reminder. Only airings are mentioned, never results.
The bot token and chat id come from the TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID secrets.
"""

import argparse
from datetime import datetime, time, timedelta, timezone
import json
import os
from pathlib import Path
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.request import urlopen


MOSCOW = timezone(timedelta(hours=3))
QUIET_UNTIL = time(9, 0)
EVE_AT = time(19, 0)
WEEKLY_ON = 1  # Tuesday
LEAD = timedelta(minutes=45)
APP_URL = "https://konicaru.github.io/tv_program_match/"
GENDERS = {"men": "Мужчины", "women": "Женщины"}
WEEKDAYS = "понедельник вторник среда четверг пятница суббота воскресенье".split()
SHORT_WEEKDAYS = "пн вт ср чт пт сб вс".split()
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


def day_list(schedule: dict, day, after: datetime | None = None) -> list[str]:
    """Lines for one day's airings, replays marked."""
    lines = []
    for start, event, rows in grouped([item for item in airings(schedule)
                                       if item[0].date() == day and (after is None or item[0] >= after)]):
        replay = " (повтор)" if all(row["type"] == "replay" for row in rows) else ""
        lines.append(f"{start:%H:%M} — {headline(event)} — {channels(rows)}{replay}")
    return lines


def day_title(day) -> str:
    return f"{WEEKDAYS[day.weekday()]}, {day.day} {MONTHS[day.month - 1]}"


def weekly(schedule: dict, season: dict | None, now: datetime) -> str | None:
    """The races from today to Sunday, grouped by competition, with where to watch them."""
    end = now.date() + timedelta(days=6 - now.weekday())
    live = [item for item in airings(schedule) if item[1]["type"] != "replay"]
    by_start: dict[str, list[dict]] = {}
    for _, row, _ in live:
        by_start.setdefault(row["start_utc"], []).append(row)

    sections: dict[str, list[tuple[datetime, str, str]]] = {}
    covered = set()
    for event in (season or {}).get("events", []):
        for race in event["races"]:
            start = datetime.fromisoformat(race["start_utc"].replace("Z", "+00:00")).astimezone(MOSCOW)
            if not now.date() <= start.date() <= end or race.get("state") == "cancelled":
                continue
            rows = by_start.get(race["start_utc"], [])
            covered.add(race["start_utc"])
            where = channels(rows) if rows else "трансляция пока не объявлена"
            label = f"{event.get('kind', 'Кубок мира')} · {event['name']} ({event['country']})"
            sections.setdefault(label, []).append((start, race["title"], where))

    firsts: dict[str, tuple[datetime, dict, list[dict]]] = {}
    for start, row, event in live:
        if event.get("kind") == "race" and event["event_id"] not in firsts and row["start_utc"] not in covered:
            firsts[event["event_id"]] = (start, event, [r for s, r, e in live if e is event and s == start])
    for start, event, rows in firsts.values():
        if now.date() <= start.date() <= end:
            title = headline({**event, "competition": None})
            sections.setdefault(event.get("competition") or "Другие гонки", []).append((start, title, channels(rows)))

    if not sections:
        return None
    blocks = []
    for label, races in sorted(sections.items(), key=lambda item: min(race[0] for race in item[1])):
        lines = [f"{SHORT_WEEKDAYS[start.weekday()]} {start.day}.{start.month} {start:%H:%M} — {title} — {where}"
                 for start, title, where in sorted(races)]
        blocks.append(label + "\n" + "\n".join(lines))
    count = sum(len(races) for races in sections.values())
    period = f"{now.day}–{end.day} {MONTHS[end.month - 1]}" if now.month == end.month else \
        f"{now.day} {MONTHS[now.month - 1]} – {end.day} {MONTHS[end.month - 1]}"
    return f"🏁 Биатлон на этой неделе, {period} (МСК)\n\n" + "\n\n".join(blocks) + \
        f"\n\nВсего гонок: {count}\n{APP_URL}"


def plan_messages(schedule: dict, state: dict, now: datetime, season: dict | None = None) -> tuple[list[str], dict]:
    """Messages to send now and the state to keep for the next run."""
    now = now.astimezone(MOSCOW)
    if now.time() < QUIET_UNTIL:
        return [], state
    sent = {key: start for key, start in state.get("sent", {}).items()
            if datetime.fromisoformat(start) > now - timedelta(days=1)}
    state = {**state, "sent": sent}
    messages = []

    today = now.date().isoformat()
    if now.weekday() == WEEKLY_ON and state.get("weekly") != today:
        state["weekly"] = today
        text = weekly(schedule, season, now)
        if text:
            messages.append(text)

    if state.get("digest") != today:
        state["digest"] = today
        lines = day_list(schedule, now.date(), after=now)
        if lines:
            messages.append(f"📺 Биатлон сегодня, {day_title(now.date())} (МСК)\n\n" + "\n".join(lines) + f"\n\n{APP_URL}")

    if now.time() >= EVE_AT and state.get("eve") != today:
        state["eve"] = today
        tomorrow = now.date() + timedelta(days=1)
        lines = day_list(schedule, tomorrow)
        if lines:
            messages.append(f"🗓 Завтра, {day_title(tomorrow)} (МСК)\n\n" + "\n".join(lines) + f"\n\n{APP_URL}")

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
    try:
        with urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=20) as response:
            answer = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        # Telegram explains the refusal in the body ("chat not found", "bot was blocked by the user", ...).
        try:
            answer = json.loads(error.read().decode("utf-8"))
        except ValueError:
            answer = {}
        hint = {400: "проверьте TELEGRAM_CHAT_ID и что вы написали боту /start",
                401: "проверьте TELEGRAM_BOT_TOKEN", 403: "бот заблокирован или не может писать в этот чат",
                404: "проверьте TELEGRAM_BOT_TOKEN"}.get(error.code, "")
        raise SystemExit(f"Telegram отказал: HTTP {error.code} {answer.get('description', '')}. {hint}") from None
    if not answer.get("ok"):
        raise SystemExit(f"Telegram не принял сообщение: {answer.get('description', '')}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schedule", type=Path, default=Path("public/data/schedule.json"))
    parser.add_argument("--season", type=Path, default=Path("public/data/ibu/season.json"))
    parser.add_argument("--state", type=Path, default=Path(".notify-state/state.json"))
    parser.add_argument("--test", action="store_true", help="Отправить проверочное сообщение")
    args = parser.parse_args()
    token, chat_id = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Секреты TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID не заданы — уведомления выключены")
        return
    if args.test:
        send(token, chat_id, "✅ Бот BIATHLON TV подключён. Во вторник в 09:00 — гонки недели, в 09:00 — эфиры дня, "
                             f"в 19:00 — эфиры на завтра, примерно за 40 минут — напоминание.\n{APP_URL}")
        print("Проверочное сообщение отправлено")
        return

    try:
        state = json.loads(args.state.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    schedule = json.loads(args.schedule.read_text(encoding="utf-8"))
    try:
        season = json.loads(args.season.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        season = None
    messages, state = plan_messages(schedule, state, datetime.now(timezone.utc), season)
    for text in messages:
        send(token, chat_id, text)
    args.state.parent.mkdir(parents=True, exist_ok=True)
    args.state.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    print(f"Отправлено сообщений: {len(messages)}")


if __name__ == "__main__":
    main()
