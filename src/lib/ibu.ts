export interface RaceEntry {
  race_id: string
  title: string
  discipline: string
  gender: 'men' | 'women' | 'mixed' | null
  km: string | null
  start_utc: string
  state: 'scheduled' | 'startlist' | 'live' | 'results' | 'cancelled'
  has_startlist: boolean
  has_results: boolean
  url: string
}

export interface SeasonEvent {
  event_id: string
  name: string
  country: string
  kind: string
  start_date: string
  end_date: string
  first_race_utc: string | null
  races: RaceEntry[]
}

export interface Season {
  season_id: string
  season: string
  events: SeasonEvent[]
}

export interface RaceRow {
  bib: string | null
  name: string
  nat: string | null
  is_team: boolean
  leg: number | null
  start_time: string | null
  start_info: string | null
  rank: string | null
  total_time: string | null
  behind: string | null
  shootings: string | null
  wc_points: string | null
  irm: string | null
}

export interface RaceFile {
  race_id: string
  title: string
  start_utc: string
  is_result: boolean
  status: string | null
  url: string
  rows: RaceRow[]
}

export interface StandingsTable {
  cup_id: string
  name: string | null
  as_of: string | null
  race_count: number | null
  total_races: number | null
  url: string
  rows: { rank: string | null; name: string; nat: string | null; score: string | null; ibu_id: string | null }[]
}

export type Standings = Record<'men' | 'women', StandingsTable>

export const STATE_LABELS: Record<RaceEntry['state'], string> = {
  scheduled: 'Запланирована',
  startlist: 'Стартовый протокол',
  live: 'Идёт',
  results: 'Есть результаты',
  cancelled: 'Отменена',
}

/** The event to open first: the one still running or next to come, else the last one. */
export function currentEvent(season: Season, now: Date): SeasonEvent | null {
  const upcoming = season.events.find((event) => event.races.some((race) => Date.parse(race.start_utc) >= now.getTime() - 6 * 3600_000))
  return upcoming ?? season.events.at(-1) ?? null
}

/** IBU races by start time, to link an airing that starts with the race. */
export function racesByStart(season: Season | null): Map<string, RaceEntry> {
  const map = new Map<string, RaceEntry>()
  for (const event of season?.events ?? []) {
    for (const race of event.races) map.set(race.start_utc, race)
  }
  return map
}

export function findRace(season: Season | null, raceId: string): { event: SeasonEvent; race: RaceEntry } | null {
  for (const event of season?.events ?? []) {
    const race = event.races.find((item) => item.race_id === raceId)
    if (race) return { event, race }
  }
  return null
}

/** Finishers first in rank order, then the rest (DNS, DNF, …) as IBU lists them. */
export function resultRows(rows: RaceRow[]): RaceRow[] {
  const ranked = rows.filter((row) => row.rank && !row.irm).sort((a, b) => Number(a.rank) - Number(b.rank))
  return [...ranked, ...rows.filter((row) => !ranked.includes(row))]
}
