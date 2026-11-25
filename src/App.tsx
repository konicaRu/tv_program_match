import { useEffect, useMemo, useState } from 'react'
import {
  airingState, buildDays, formatDayLong, formatDayShort, formatStamp, formatTime, headline, initialDay,
  moscowDay, originalDay, typeLabel, type Broadcast, type DayEntry, type Schedule, type Status,
} from './lib/schedule'

type Load =
  | { state: 'loading' }
  | { state: 'failed' }
  | { state: 'ready'; schedule: Schedule; status: Status | null }

async function fetchJson<T>(name: string): Promise<T> {
  const response = await fetch(`${import.meta.env.BASE_URL}data/${name}`, { cache: 'no-cache' })
  if (!response.ok) throw new Error(`${name}: ${response.status}`)
  return response.json() as Promise<T>
}

function useData(): Load {
  const [load, setLoad] = useState<Load>({ state: 'loading' })
  useEffect(() => {
    Promise.all([fetchJson<Schedule>('schedule.json'), fetchJson<Status>('status.json').catch(() => null)])
      .then(([schedule, status]) => setLoad({ state: 'ready', schedule, status }))
      .catch(() => setLoad({ state: 'failed' }))
  }, [])
  return load
}

function useNow(): Date {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 60_000)
    return () => window.clearInterval(timer)
  }, [])
  return now
}

export default function App() {
  const load = useData()
  const now = useNow()
  const today = moscowDay(now)
  const days = useMemo(() => (load.state === 'ready' ? buildDays(load.schedule) : new Map<string, DayEntry[]>()), [load])
  const dayList = [...days.keys()]
  const [picked, setPicked] = useState<string | null>(null)
  const selected = picked && days.has(picked) ? picked : initialDay(dayList, today)
  const status = load.state === 'ready' ? load.status : null

  return (
    <div className="app">
      <header className="top">
        <div className="brand">
          <h1>BIATHLON TV</h1>
          <p>Москва · МСК</p>
        </div>
        {status?.last_success && <p className="updated">Обновлено {formatStamp(status.last_success)}</p>}
      </header>

      {status?.stale && (
        <p className="notice" role="status">
          Расписание устарело: часть страниц «Матча» не загрузилась.
          {status.last_success ? ` Показаны данные от ${formatStamp(status.last_success)}.` : ''}
        </p>
      )}

      {load.state === 'loading' && <p className="empty">Загружаем расписание…</p>}
      {load.state === 'failed' && (
        <p className="notice" role="alert">Не удалось загрузить расписание. Проверьте подключение и обновите страницу.</p>
      )}

      {load.state === 'ready' && dayList.length === 0 && (
        <p className="empty">На ближайшие дни биатлона в программе «Матча» пока нет.</p>
      )}

      {selected && (
        <>
          <nav className="days" aria-label="Дни">
            {dayList.map((day) => (
              <button
                key={day}
                type="button"
                className="day"
                aria-pressed={day === selected}
                onClick={() => setPicked(day)}
              >
                <span>{day === today ? 'Сегодня' : formatDayShort(day).split(', ')[0]}</span>
                <strong>{formatDayShort(day).split(', ')[1] ?? formatDayShort(day)}</strong>
              </button>
            ))}
          </nav>

          <main>
            <h2 className="day-title">{formatDayLong(selected)}</h2>
            <ul className="feed">
              {days.get(selected)!.map((entry) => (
                <li key={entry.event.event_id}>
                  <EventCard entry={entry} day={selected} now={now} />
                </li>
              ))}
            </ul>
          </main>
        </>
      )}

      <footer className="foot">
        Время московское. Показы взяты из официальной телепрограммы «Матча» и расписаний Okko в его Telegram-канале. Результатов гонок здесь нет.
      </footer>
    </div>
  )
}

function EventCard({ entry, day, now }: { entry: DayEntry; day: string; now: Date }) {
  const [open, setOpen] = useState(false)
  const { title, subtitle } = headline(entry.event)
  const times = [...new Set(entry.airings.map((row) => formatTime(row.start_utc)))]
  const types = [...new Set(entry.airings.map((row) => row.type))]
  const channels = [...new Set(entry.airings.map((row) => row.channel))]
  const live = entry.airings.some((row) => airingState(row, now) === 'now')
  const detailsId = `details-${entry.event.event_id}`

  return (
    <article className={`card${entry.event.category === 'show' ? ' card--show' : ''}`}>
      <button type="button" className="card-head" aria-expanded={open} aria-controls={detailsId} onClick={() => setOpen(!open)}>
        <span className="times">
          {times.map((time) => <time key={time}>{time}</time>)}
        </span>
        <span className="body">
          <span className="title">{title}</span>
          {subtitle && <span className="subtitle">{subtitle}</span>}
          <span className="badges">
            {live && <span className="badge badge--now">Сейчас</span>}
            {types.filter((type) => type !== 'unknown').map((type) => (
              <span key={type} className={`badge badge--${type}`}>{typeLabel(type)}</span>
            ))}
            {channels.map((channel) => (
              <span key={channel} className={`badge ${channel === 'Okko' ? 'badge--okko' : 'badge--channel'}`}>{channel}</span>
            ))}
          </span>
        </span>
        <span className="chevron" aria-hidden="true">{open ? '▴' : '▾'}</span>
      </button>
      {open && <Airings id={detailsId} event={entry.event} day={day} now={now} />}
    </article>
  )
}

function Airings({ id, event, day, now }: { id: string; event: DayEntry['event']; day: string; now: Date }) {
  const rows = [...event.broadcasts].sort((a, b) => a.start_utc.localeCompare(b.start_utc))
  const raceDay = originalDay(event)
  return (
    <div className="airings" id={id}>
      <h3>Время трансляций</h3>
      <ul>
        {rows.map((row) => <AiringRow key={row.broadcast_id} row={row} day={day} raceDay={raceDay} now={now} />)}
      </ul>
      {event.kind === 'race' && event.title && <p className="source-title">{event.broadcasts[0].title}</p>}
    </div>
  )
}

function AiringRow({ row, day, raceDay, now }: { row: Broadcast; day: string; raceDay: string | null; now: Date }) {
  const rowDay = moscowDay(row.start_utc)
  const state = airingState(row, now)
  const note = row.type === 'replay'
    ? `Повтор записи${raceDay && raceDay !== rowDay ? ` · гонка ${formatDayShort(raceDay)}` : ''}`
    : row.type === 'unknown' ? null : typeLabel(row.type)
  return (
    <li className={`airing${rowDay === day ? ' airing--today' : ''}${state === 'finished' ? ' airing--past' : ''}`}>
      <span className="airing-when">
        {rowDay !== day && <span className="airing-day">{formatDayShort(rowDay)}</span>}
        <time dateTime={row.start_utc}>{formatTime(row.start_utc)}</time>
        {row.end_utc && <span className="airing-end">–{formatTime(row.end_utc)}</span>}
      </span>
      <span className="airing-what">
        <span className="airing-channel">{row.channel}</span>
        {note && <span className="airing-note">{note}</span>}
        {state === 'now' && <span className="badge badge--now">Сейчас</span>}
        {state === 'finished' && <span className="airing-note">Завершено</span>}
      </span>
      <a className="airing-link" href={row.url ?? row.source_url} target="_blank" rel="noreferrer"
        aria-label={row.platform === 'OKKO' ? 'Расписание Okko в Telegram' : `Телепрограмма: ${row.channel}`}
        title={row.platform === 'OKKO' ? 'Расписание Okko в Telegram' : 'Телепрограмма «Матча»'}>
        ↗
      </a>
    </li>
  )
}
