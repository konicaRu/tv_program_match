import { useEffect, useState } from 'react'
import {
  currentEvent, findRace, resultRows, STATE_LABELS,
  type RaceFile, type RaceRow, type Season, type Standings, type StandingsTable,
} from './lib/ibu'
import { formatDayShort, formatStamp, formatTime, moscowDay } from './lib/schedule'

type Loaded<T> = { state: 'loading' } | { state: 'failed' } | { state: 'ready'; data: T }

export async function fetchData<T>(name: string): Promise<T> {
  const response = await fetch(`${import.meta.env.BASE_URL}data/${name}`, { cache: 'no-cache' })
  if (!response.ok) throw new Error(`${name}: ${response.status}`)
  return response.json() as Promise<T>
}

export function useLoaded<T>(name: string | null): Loaded<T> {
  const [loaded, setLoaded] = useState<Loaded<T>>({ state: 'loading' })
  useEffect(() => {
    if (!name) return
    let active = true
    setLoaded({ state: 'loading' })
    fetchData<T>(name)
      .then((data) => active && setLoaded({ state: 'ready', data }))
      .catch(() => active && setLoaded({ state: 'failed' }))
    return () => { active = false }
  }, [name])
  return loaded
}

function Waiting({ loaded, what }: { loaded: Loaded<unknown>; what: string }) {
  if (loaded.state === 'loading') return <p className="empty">Загружаем {what}…</p>
  return <p className="notice" role="alert">Не удалось загрузить {what}. Проверьте подключение.</p>
}

function raceDate(iso: string): string {
  return `${formatDayShort(moscowDay(iso))}, ${formatTime(iso)}`
}

export function RacesView({ now }: { now: Date }) {
  const loaded = useLoaded<Season>('ibu/season.json')
  if (loaded.state !== 'ready') return <Waiting loaded={loaded} what="календарь IBU" />
  const season = loaded.data
  const current = currentEvent(season, now)
  return (
    <main>
      <h2 className="section-title">Гонки сезона {season.season}</h2>
      <p className="hint">Календарь, стартовые протоколы и результаты IBU. Время московское.</p>
      <ul className="events">
        {season.events.map((event) => (
          <li key={event.event_id}>
            <details className="event" open={event.event_id === current?.event_id}>
              <summary>
                <span className="event-name">{event.name}</span>
                <span className="event-meta">
                  {event.kind} · {event.country} · {formatDayShort(event.start_date).split(', ')[1]} – {formatDayShort(event.end_date).split(', ')[1]}
                </span>
              </summary>
              {event.races.length === 0 ? (
                <p className="empty">Гонки этапа ещё не опубликованы.</p>
              ) : (
                <ul className="races">
                  {event.races.map((race) => (
                    <li key={race.race_id}>
                      <a className="race-link" href={`#/race/${race.race_id}`}>
                        <span className="race-when">{raceDate(race.start_utc)}</span>
                        <span className="race-title">{race.title}</span>
                        <span className={`race-state race-state--${race.state}`}>{STATE_LABELS[race.state]}</span>
                      </a>
                    </li>
                  ))}
                </ul>
              )}
            </details>
          </li>
        ))}
      </ul>
    </main>
  )
}

export function RaceView({ raceId }: { raceId: string }) {
  const season = useLoaded<Season>('ibu/season.json')
  const entry = season.state === 'ready' ? findRace(season.data, raceId) : null
  const file = useLoaded<RaceFile>(entry?.race.has_startlist ? `ibu/races/${raceId}.json` : null)
  const [showResults, setShowResults] = useState(false)
  if (season.state !== 'ready') return <Waiting loaded={season} what="гонку" />
  if (!entry) return <p className="notice" role="alert">Гонка не найдена в календаре IBU.</p>
  const { event, race } = entry
  return (
    <main>
      <a className="back" href="#/races">← Все гонки</a>
      <h2 className="section-title">{race.title}</h2>
      <p className="hint">{event.name} · {raceDate(race.start_utc)} МСК · <a href={race.url} target="_blank" rel="noreferrer">IBU ↗</a></p>
      {!race.has_startlist ? (
        <p className="empty">Стартовый протокол ещё не опубликован.</p>
      ) : file.state !== 'ready' ? (
        file.state === 'loading' ? <p className="empty">Загружаем протокол…</p>
          : <p className="empty">Протокол ещё не загружен сборщиком. Он появится после ближайшего обновления.</p>
      ) : (
        <>
          <StartList rows={file.data.rows} />
          {file.data.is_result && (showResults ? <Results rows={file.data.rows} /> : (
            <div className="spoiler">
              <p>Результаты уже есть. Они скрыты, чтобы не испортить просмотр.</p>
              <button type="button" className="reveal" onClick={() => setShowResults(true)}>Показать результаты</button>
            </div>
          ))}
        </>
      )}
    </main>
  )
}

function startTime(row: RaceRow): string {
  return row.start_time ? formatTime(row.start_time) : row.start_info ?? ''
}

function StartList({ rows }: { rows: RaceRow[] }) {
  const ordered = [...rows].sort((a, b) => Number(a.bib ?? 9999) - Number(b.bib ?? 9999))
  const teams = rows.some((row) => row.is_team)
  return (
    <section aria-labelledby="startlist-title">
      <h3 id="startlist-title" className="table-title">Стартовый протокол · {ordered.length}</h3>
      <table className="table">
        <thead><tr><th>№</th><th>{teams ? 'Команда' : 'Спортсмен'}</th><th>Страна</th><th>Старт</th></tr></thead>
        <tbody>
          {ordered.map((row) => (
            <tr key={`${row.bib}-${row.name}`}>
              <td className="num">{row.bib}</td><td>{row.name}</td><td>{row.nat}</td><td className="num">{startTime(row)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}

function Results({ rows }: { rows: RaceRow[] }) {
  return (
    <section aria-labelledby="results-title">
      <h3 id="results-title" className="table-title">Результаты</h3>
      <table className="table">
        <thead><tr><th>Место</th><th>Спортсмен</th><th>Страна</th><th>Время</th><th>Промахи</th></tr></thead>
        <tbody>
          {resultRows(rows).map((row) => (
            <tr key={`${row.bib}-${row.name}`}>
              <td className="num">{row.irm ?? row.rank}</td>
              <td>{row.name}{row.behind && row.rank !== '1' ? <span className="cell-note"> +{row.behind}</span> : null}</td>
              <td>{row.nat}</td><td className="num">{row.total_time}</td><td className="num">{row.shootings}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}

export function StandingsView({ revealed, onReveal }: { revealed: boolean; onReveal: () => void }) {
  const loaded = useLoaded<Standings>(revealed ? 'ibu/standings.json' : null)
  const [gender, setGender] = useState<'men' | 'women'>('men')
  return (
    <main>
      <h2 className="section-title">Общий зачёт Кубка мира</h2>
      {!revealed ? (
        <div className="spoiler">
          <p>Зачёт показывает итоги уже прошедших гонок. Откройте его, когда будете готовы.</p>
          <button type="button" className="reveal" onClick={onReveal}>Показать зачёт</button>
        </div>
      ) : loaded.state !== 'ready' ? <Waiting loaded={loaded} what="зачёт" /> : (
        <>
          <div className="tabs" role="tablist" aria-label="Зачёт">
            {(['men', 'women'] as const).map((key) => (
              <button key={key} type="button" role="tab" aria-selected={gender === key} className="tab" onClick={() => setGender(key)}>
                {key === 'men' ? 'Мужчины' : 'Женщины'}
              </button>
            ))}
          </div>
          <StandingsTableView table={loaded.data[gender]} />
        </>
      )}
    </main>
  )
}

function StandingsTableView({ table }: { table: StandingsTable }) {
  if (table.rows.length === 0) return <p className="empty">Зачёт появится после первой гонки.</p>
  return (
    <section>
      <p className="hint">
        После {table.race_count} из {table.total_races} гонок
        {table.as_of ? ` · обновлено ${formatStamp(table.as_of)}` : ''} · <a href={table.url} target="_blank" rel="noreferrer">IBU ↗</a>
      </p>
      <table className="table">
        <thead><tr><th>Место</th><th>Спортсмен</th><th>Страна</th><th>Очки</th></tr></thead>
        <tbody>
          {table.rows.map((row) => (
            <tr key={row.ibu_id ?? row.name}>
              <td className="num">{row.rank}</td><td>{row.name}</td><td>{row.nat}</td><td className="num">{row.score}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}
