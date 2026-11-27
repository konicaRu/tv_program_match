"""Telegram alerts when something breaks, explained in plain Russian for someone new to the project.

Two kinds of alerts, sent through the same bot and only to the user's private chat:

- `sources`: after a collection run, compares the source states before and after it. A source
  that just broke gets one message (what it is, what the error means, what the user sees in
  the app, what to do); a source that works again gets a short "fixed" message. A World Cup
  race in the next two days that no source has an airing for is reported once.
- `workflow`: a GitHub Actions run whose steps failed gets one message naming the step, what
  it does, what the failure means and where the log is. The collector's own "some sources
  failed" step is left to `sources`. For the reminders workflow, which runs every 10 minutes,
  only the first failure in a row is reported.

Without the TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID secrets the messages are only printed.
"""

import argparse
from datetime import datetime, timedelta
import json
import os
from pathlib import Path

from collectors.notify import MOSCOW, ensure_private, send


APP_URL = "https://konicaru.github.io/tv_program_match/"
NEXT_RUN = "Следующий сбор — в 07:15 или 19:15 МСК."
HORIZON = timedelta(hours=48)
NEAR = timedelta(minutes=30)

SOURCES = {
    "match_guide": {
        "name": "Телепрограмма «Матча» (matchtv.ru/tvguide)",
        "role": "Это главный источник: из него берутся все эфиры и повторы на «Матч ТВ», «Арене», «Стране», «Игре» и «Премьере».",
        "backup": "Пока он не работает, прямые эфиры «Матч ТВ» подхватывают раздел биатлона и карточки в Telegram, но повторов на других каналах не будет.",
    },
    "match_live": {
        "name": "Раздел биатлона «Матча» (matchtv.ru/biathlon/live)",
        "role": "Это второй источник «Матча», только прямые эфиры «Матч ТВ». Он страхует телепрограмму.",
        "backup": "Пока телепрограмма работает, в ленте ничего не пропадёт.",
    },
    "match_telegram": {
        "name": "Карточки «Матча» в Telegram (@biathlon_match)",
        "role": "Это запасной источник: картинки-расписания перед российскими этапами, их читает распознавание текста (OCR).",
        "backup": "Пока телепрограмма «Матча» работает, в ленте ничего не пропадёт.",
    },
    "okko_telegram": {
        "name": "Расписание Okko из канала «Зимний спорт» (@okkowintersport)",
        "role": "Это главный источник эфиров Okko (Кубок мира): картинки-расписания этапов, их читает распознавание текста (OCR).",
        "backup": "Его страхует канал «Okko Спорт»: если он работает, эфиры Okko в ленте будут.",
    },
    "okko_sport": {
        "name": "Расписание Okko из канала «Okko Спорт» (@okkosport)",
        "role": "Это запасной источник эфиров Okko: туда перепостят те же расписания.",
        "backup": "Пока работает канал «Зимний спорт», в ленте ничего не пропадёт.",
    },
    "ibu": {
        "name": "Данные IBU (календарь, протоколы, зачёт)",
        "role": "Отсюда вкладки «Гонки» и «Зачёт» и недельная сводка бота по Кубку мира.",
        "backup": "Лента эфиров от этого не зависит.",
    },
}

STEPS = {
    "Install dependencies": (
        "Установка программ, которые нужны для работы (библиотеки Python или Node.js, распознавание текста Tesseract, браузер для тестов).",
        "Почти всегда это временный сбой серверов GitHub или хранилища пакетов, код тут ни при чём.",
        "Ничего: следующий запуск обычно проходит. Если повторяется — открой лог и нажми Re-run jobs.",
    ),
    "Install Playwright browser": (
        "Установка браузера Chromium для проверки интерфейса.",
        "Обычно временный сбой загрузки браузера.",
        "Перезапусти запуск (Re-run jobs). Сайт при этом не обновился, на нём прежняя версия.",
    ),
    "Run tests": (
        "Автотесты сборщика на Python. Они проверяют разбор страниц и картинок на сохранённых примерах до того, как сборщик пойдёт в интернет.",
        "Последнее изменение кода что-то сломало. Сборщик не запускался, расписание не обновилось — на сайте остались прежние данные.",
        "Открой лог, найди строки FAIL или ERROR: там название теста и что он ожидал. Исправить код, прогнать тесты локально, запушить.",
    ),
    "Unit tests": (
        "Автотесты интерфейса (vitest).",
        "Последнее изменение кода приложения что-то сломало. Новая версия сайта не опубликована, работает прежняя.",
        "Открой лог, найди FAIL — там название теста и разница между ожидаемым и полученным.",
    ),
    "Build": (
        "Сборка приложения (TypeScript → файлы сайта).",
        "В коде приложения ошибка типов или синтаксиса. Новая версия сайта не опубликована, работает прежняя.",
        "Открой лог: там файл и строка с ошибкой. Локально то же покажет npm run build.",
    ),
    "Interface scenarios": (
        "Проверка интерфейса в настоящем браузере (Playwright): лента, вкладки, работа без сети.",
        "Какой-то сценарий интерфейса сломался. Новая версия сайта не опубликована, работает прежняя.",
        "Открой лог: там название сценария и шаг, на котором он упал. Локально — npm run test:e2e.",
    ),
    "Commit updated data": (
        "Сохранение свежих данных в репозиторий (git push).",
        "Чаще всего кто-то запушил в main в ту же минуту, и GitHub отклонил отправку. Новые данные не сохранились.",
        "Ничего: следующий сбор соберёт их заново. Если повторяется — открой лог.",
    ),
    "Send due messages": (
        "Отправка напоминаний ботом.",
        "Напоминание или сводка могли не дойти. Частые причины: Telegram временно ограничил отправку, бота заблокировали, секрет с токеном или ID чата неверный.",
        "Открой лог: там объяснение от Telegram. Если «chat not found» или 401 — проверь секреты TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID.",
    ),
}
DEPLOY_STEP = (
    "Публикация сайта на GitHub Pages.",
    "Сайт собран, но GitHub не смог его выложить. Обычно временный сбой GitHub Pages; работает прежняя версия.",
    "Перезапусти запуск (Re-run jobs). Если повторяется — проверь в настройках репозитория Pages → Source: GitHub Actions.",
)
IGNORED_STEPS = {"Report source errors"}  # explained per source by `sources`


def explain_error(text: str) -> str:
    """What an error string means, in plain words."""
    lower = text.casefold()
    if "timed out" in lower or "timeout" in lower:
        return "сайт не ответил вовремя — обычно временная перегрузка или сбой сети"
    if "403" in lower:
        return "сайт запретил доступ (ошибка 403) — возможно, включил защиту от ботов"
    if "404" in lower:
        return "страница не найдена (ошибка 404) — возможно, сайт поменял адреса"
    if any(code in lower for code in ("http error 5", " 500", " 502", " 503", " 504")):
        return "ошибка на стороне сайта (5xx) — у них что-то сломалось, обычно ненадолго"
    if "name or service not known" in lower or "urlopen error" in lower or "connection" in lower:
        return "не удалось соединиться с сайтом — сбой сети или DNS"
    if "tesseract" in lower:
        return "не сработало распознавание текста на картинке (Tesseract)"
    if "другой день" in lower or "другую дату" in lower:
        return "сайт показал не тот день, что просили, — данные не взяты, чтобы не перепутать даты"
    return "сборщик не понял страницу — похоже, сайт поменял вёрстку, и разбор надо поправить"


def moscow(stamp: str | None) -> str:
    if not stamp:
        return "ещё ни разу"
    moment = datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(MOSCOW)
    return f"{moment:%d.%m в %H:%M} МСК"


def source_states(status: dict | None, ibu_status: dict | None) -> dict[str, dict]:
    states = dict((status or {}).get("sources", {}))
    if ibu_status:
        states["ibu"] = ibu_status
    return states


def broken_message(name: str, state: dict) -> str:
    info = SOURCES.get(name, {"name": name, "role": "", "backup": ""})
    errors = state.get("errors") or {}
    first = next(iter(errors.items()), ("", ""))
    reason = explain_error(first[1])
    more = f" (и ещё мест с ошибкой: {len(errors) - 1})" if len(errors) > 1 else ""
    happened = f"Что случилось: {reason}{more}."
    if first[1]:
        happened += f"\nТехническая ошибка: {first[1][:200]}"
    if first[0]:
        happened += f"\nГде: {first[0]}"
    return "\n\n".join(part for part in [
        f"⚠️ Сломался источник: {info['name']}",
        info["role"],
        happened,
        f"Что ты увидишь: в приложении эти данные остались от {moscow(state.get('last_success'))} и помечены как устаревшие. "
        + info["backup"],
        "Что делать: если сбой разовый — ничего, он пройдёт сам. " + NEXT_RUN
        + " Если это сообщение не сменится на «снова работает» в течение суток — открой лог сбора в GitHub Actions и поправь разбор.",
    ] if part)


def fixed_message(name: str) -> str:
    return f"✅ Снова работает: {SOURCES.get(name, {'name': name})['name']}. Данные обновлены."


def missing_races(schedule: dict | None, season: dict | None, now: datetime) -> dict[str, str]:
    """World Cup races starting within HORIZON that no source has a live airing for."""
    starts = []
    for event in (schedule or {}).get("events", []):
        for row in event["broadcasts"]:
            if row["type"] != "replay":
                starts.append(datetime.fromisoformat(row["start_utc"].replace("Z", "+00:00")))
    missing = {}
    for event in (season or {}).get("events", []):
        for race in event["races"]:
            start = datetime.fromisoformat(race["start_utc"].replace("Z", "+00:00"))
            if race.get("state") == "cancelled" or not now < start <= now + HORIZON:
                continue
            if not any(abs(start - other) <= NEAR for other in starts):
                missing[race["race_id"]] = (f"{race['title']} — {event.get('kind', 'Кубок мира')}, {event['name']}, "
                                            f"{start.astimezone(MOSCOW):%d.%m в %H:%M} МСК")
    return missing


def missing_message(races: list[str]) -> str:
    listed = "\n".join(f"• {race}" for race in races)
    return (f"🔎 Не нашёл трансляцию для гонок в ближайшие двое суток:\n{listed}\n\n"
            "Что это значит: по календарю IBU гонка будет, но ни Okko, ни «Матч» её эфир пока не объявили "
            "(или он не распознался). В ленте её нет, напоминание не придёт.\n\n"
            "Что делать: загляни в канал @okkowintersport. Если там уже есть картинка с расписанием — значит, "
            "распознавание текста её не осилило: пришли картинку, поправим разбор. Если расписания нет — подождём, "
            f"Okko обычно выкладывает его за 1–3 дня. {NEXT_RUN}")


def plan_source_alerts(before: dict, after: dict, ibu_before: dict | None, ibu_after: dict | None,
                       schedule_before: dict | None, schedule_after: dict | None, season: dict | None) -> list[str]:
    messages = []
    was, now_states = source_states(before, ibu_before), source_states(after, ibu_after)
    for name, state in now_states.items():
        stale_before = was.get(name, {}).get("stale", False)
        if state.get("stale") and not stale_before:
            messages.append(broken_message(name, state))
        elif stale_before and not state.get("stale"):
            messages.append(fixed_message(name))

    checked_after = (after or {}).get("checked_at")
    if checked_after and season:
        checked_before = (before or {}).get("checked_at")
        known = missing_races(schedule_before, season, datetime.fromisoformat(checked_before.replace("Z", "+00:00"))) \
            if checked_before and schedule_before else {}
        current = missing_races(schedule_after, season, datetime.fromisoformat(checked_after.replace("Z", "+00:00")))
        new = [text for race_id, text in current.items() if race_id not in known]
        if new:
            messages.append(missing_message(new))
    return messages


def plan_workflow_alert(workflow: str, jobs: list[dict], run_url: str, previous_conclusion: str | None,
                        only_first: bool = False) -> str | None:
    """One message about the failed steps of a run, or None."""
    failed = [(job["name"], step["name"]) for job in jobs for step in job.get("steps", [])
              if step.get("conclusion") == "failure" and step["name"] not in IGNORED_STEPS]
    failed += [(job["name"], "") for job in jobs
               if job.get("conclusion") == "failure" and not any(step.get("conclusion") == "failure" for step in job.get("steps", []))]
    if not failed or (only_first and previous_conclusion == "failure"):
        return None
    blocks = []
    for job, step in failed:
        what, meaning, todo = STEPS.get(step) or (DEPLOY_STEP if "deploy" in (job + step).casefold() else (
            f"Шаг «{step or job}».", "Он завершился с ошибкой.", "Открой лог запуска: ошибка обычно в последних строках шага."))
        blocks.append(f"Шаг: {step or job}\nЧто он делает: {what}\nЧто это значит: {meaning}\nЧто делать: {todo}")
    tail = "\n\nЕсли запуск повторится и снова упадёт — придёт ещё одно сообщение." if not only_first else \
        "\n\nПока сбой не пройдёт, повторять это сообщение не буду."
    return f"⚠️ Сбой в GitHub Actions: «{workflow}»\n\n" + "\n\n".join(blocks) + f"\n\nЛог: {run_url}" + tail


def read_json(path: Path | None) -> dict | None:
    if path is None:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def deliver(messages: list[str]) -> None:
    for text in messages:
        print(text, end="\n\n")
    token, chat_id = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not messages or not token or not chat_id:
        return
    ensure_private(token, chat_id)
    for text in messages:
        send(token, chat_id, text)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    sources = commands.add_parser("sources", help="Сравнить состояние источников до и после сбора")
    sources.add_argument("--before", type=Path, required=True, help="Папка с данными до сбора")
    sources.add_argument("--after", type=Path, default=Path("public/data"))
    workflow = commands.add_parser("workflow", help="Сообщить о сбое шагов запуска GitHub Actions")
    workflow.add_argument("--name", required=True)
    workflow.add_argument("--jobs", type=Path, required=True, help="JSON из gh run view --json jobs")
    workflow.add_argument("--run-url", required=True)
    workflow.add_argument("--previous", default="", help="Итог предыдущего запуска (success, failure)")
    workflow.add_argument("--only-first", action="store_true", help="Не повторять, пока сбой не пройдёт")
    args = parser.parse_args()

    if args.command == "sources":
        messages = plan_source_alerts(
            read_json(args.before / "status.json"), read_json(args.after / "status.json"),
            read_json(args.before / "ibu" / "status.json"), read_json(args.after / "ibu" / "status.json"),
            read_json(args.before / "schedule.json"), read_json(args.after / "schedule.json"),
            read_json(args.after / "ibu" / "season.json"))
    else:
        jobs = (read_json(args.jobs) or {}).get("jobs", [])
        text = plan_workflow_alert(args.name, jobs, args.run_url, args.previous or None, args.only_first)
        messages = [text] if text else []
    deliver(messages)


if __name__ == "__main__":
    main()
