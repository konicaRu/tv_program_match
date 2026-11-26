export type BroadcastType = 'live' | 'delayed' | 'replay' | 'recording' | 'unknown'

export interface Broadcast {
  broadcast_id: string
  platform: string
  channel: string
  title: string
  type: BroadcastType
  start_utc: string
  end_utc: string | null
  url: string | null
  source_url: string
  verified_at: string
}

export interface ScheduleEvent {
  event_id: string
  kind: 'race' | 'show'
  title: string
  competition: string | null
  discipline: string | null
  gender: 'men' | 'women' | 'mixed' | null
  category: string
  broadcasts: Broadcast[]
}

export interface Schedule {
  schedule_version: number
  generated_at: string
  events: ScheduleEvent[]
  unmatched: Broadcast[]
}

export interface Status {
  checked_at: string
  last_success: string | null
  stale: boolean
  errors: Record<string, string>
  unpublished: string[]
  sources?: Record<string, { last_success: string | null; stale: boolean; errors: Record<string, string> }>
}

const SOURCE_NAMES: Record<string, string> = {
  match_guide: 'телепрограмма «Матча»',
  match_live: 'раздел биатлона «Матча»',
  okko_telegram: 'расписание Okko',
}

/** Sentences naming each source that failed in the last run and how old its data is. */
export function staleSources(status: Status): string[] {
  if (!status.sources) return status.stale ? ['часть источников не загрузилась'] : []
  return Object.entries(status.sources)
    .filter(([, source]) => source.stale)
    .map(([name, source]) => {
      const label = SOURCE_NAMES[name] ?? name
      return source.last_success ? `${label}: данные от ${formatStamp(source.last_success)}` : `${label}: данных пока нет`
    })
}

/** One card in a day's feed: an event together with its airings on that day. */
export interface DayEntry {
  event: ScheduleEvent
  airings: Broadcast[]
}

const MOSCOW = 'Europe/Moscow'
const GENDERS = { men: 'Мужчины', women: 'Женщины', mixed: 'Смешанная' } as const
const TYPES: Record<BroadcastType, string> = {
  live: 'Прямой эфир',
  delayed: 'Отложенный эфир',
  replay: 'Повтор',
  recording: 'Запись',
  unknown: 'Эфир',
}

const dayKey = new Intl.DateTimeFormat('en-CA', { timeZone: MOSCOW, year: 'numeric', month: '2-digit', day: '2-digit' })
const clock = new Intl.DateTimeFormat('ru-RU', { timeZone: MOSCOW, hour: '2-digit', minute: '2-digit' })
const dayLong = new Intl.DateTimeFormat('ru-RU', { timeZone: MOSCOW, weekday: 'long', day: 'numeric', month: 'long' })
const dayShort = new Intl.DateTimeFormat('ru-RU', { timeZone: MOSCOW, weekday: 'short', day: 'numeric', month: 'short' })
const stamp = new Intl.DateTimeFormat('ru-RU', { timeZone: MOSCOW, day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })

/** Moscow calendar day of an instant, as YYYY-MM-DD. */
export function moscowDay(iso: string | Date): string {
  return dayKey.format(typeof iso === 'string' ? new Date(iso) : iso)
}

export const formatTime = (iso: string) => clock.format(new Date(iso))
export const formatStamp = (iso: string) => stamp.format(new Date(iso))
export const formatDayLong = (day: string) => {
  const text = dayLong.format(new Date(`${day}T12:00:00+03:00`))
  return text.charAt(0).toUpperCase() + text.slice(1)
}
export const formatDayShort = (day: string) => dayShort.format(new Date(`${day}T12:00:00+03:00`))
export const typeLabel = (type: BroadcastType) => TYPES[type]

export function headline(event: ScheduleEvent): { title: string; subtitle: string | null } {
  if (event.kind === 'race' && event.discipline) {
    const gender = event.gender && event.gender !== 'mixed' ? ` · ${GENDERS[event.gender]}` : ''
    return { title: `${event.discipline}${gender}`, subtitle: event.competition }
  }
  return { title: event.title, subtitle: event.category === 'show' ? 'Программа' : null }
}

/** Airings without a recognised race become one-airing pseudo events, so they are still listed. */
function unmatchedEvent(row: Broadcast): ScheduleEvent {
  return {
    event_id: row.broadcast_id, kind: 'show', title: row.title, competition: null,
    discipline: null, gender: null, category: 'unknown', broadcasts: [row],
  }
}

/** Group the schedule into Moscow days; an event appears on every day it is aired. */
export function buildDays(schedule: Schedule): Map<string, DayEntry[]> {
  const days = new Map<string, DayEntry[]>()
  const events = [...schedule.events, ...schedule.unmatched.map(unmatchedEvent)]
  for (const event of events) {
    const byDay = new Map<string, Broadcast[]>()
    for (const row of [...event.broadcasts].sort((a, b) => a.start_utc.localeCompare(b.start_utc))) {
      const day = moscowDay(row.start_utc)
      byDay.set(day, [...(byDay.get(day) ?? []), row])
    }
    for (const [day, airings] of byDay) {
      days.set(day, [...(days.get(day) ?? []), { event, airings }])
    }
  }
  for (const entries of days.values()) {
    entries.sort((a, b) => a.airings[0].start_utc.localeCompare(b.airings[0].start_utc))
  }
  return new Map([...days].sort(([a], [b]) => a.localeCompare(b)))
}

/** The Moscow day of the first live airing, shown next to a replay. */
export function originalDay(event: ScheduleEvent): string | null {
  const live = event.broadcasts.filter((row) => row.type === 'live').map((row) => row.start_utc).sort()
  return live.length ? moscowDay(live[0]) : null
}

/** "now" only inside a confirmed interval; "finished" only once a known end has passed. */
export function airingState(row: Broadcast, now: Date): 'now' | 'finished' | null {
  if (!row.end_utc) return null
  const time = now.getTime()
  if (time >= Date.parse(row.end_utc)) return 'finished'
  if (time >= Date.parse(row.start_utc)) return 'now'
  return null
}

/** The day to open first: today if it has airings, otherwise the next day that has them. */
export function initialDay(days: string[], today: string): string | null {
  return days.find((day) => day >= today) ?? days.at(-1) ?? null
}
