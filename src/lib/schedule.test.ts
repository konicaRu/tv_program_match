import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import {
  airingState, buildDays, formatDayLong, formatTime, staleSources, headline, initialDay, moscowDay, originalDay,
  type Broadcast, type Schedule,
} from './schedule'

function airing(start_utc: string, type: Broadcast['type'] = 'live', end_utc: string | null = null): Broadcast {
  return {
    broadcast_id: `match-test-${start_utc}`, platform: 'MATCH', channel: 'Матч ТВ', title: 'Биатлон',
    type, start_utc, end_utc, url: null, source_url: 'https://matchtv.ru/tvguide', verified_at: start_utc,
  }
}

const sprint = {
  event_id: 'kubok-mira-sprint-men-2026-12-05', kind: 'race' as const, title: 'Кубок мира. Спринт',
  competition: 'Кубок мира', discipline: 'Спринт', gender: 'men' as const, category: 'international',
  broadcasts: [
    airing('2026-12-05T21:30:00Z', 'replay'),
    airing('2026-12-05T11:00:00Z', 'live'),
  ],
}

describe('Moscow time', () => {
  it('shows times and days in Moscow whatever the device zone is', () => {
    expect(formatTime('2026-11-28T04:45:00Z')).toBe('07:45')
    expect(moscowDay('2026-11-28T21:30:00Z')).toBe('2026-11-29')
    expect(formatDayLong('2026-11-28')).toBe('Суббота, 28 ноября')
  })
})

describe('buildDays', () => {
  it('lists an event on each Moscow day it is aired, in airing order', () => {
    const days = buildDays({ schedule_version: 1, generated_at: '', events: [sprint], unmatched: [] })
    expect([...days.keys()]).toEqual(['2026-12-05', '2026-12-06'])
    expect(days.get('2026-12-05')![0].airings.map((row) => row.type)).toEqual(['live'])
    expect(days.get('2026-12-06')![0].airings.map((row) => row.type)).toEqual(['replay'])
  })

  it('keeps unrecognised airings in the feed', () => {
    const lone = { ...airing('2026-12-05T08:00:00Z', 'unknown'), title: 'Биатлон. Обзор недели' }
    const days = buildDays({ schedule_version: 1, generated_at: '', events: [sprint], unmatched: [lone] })
    expect(days.get('2026-12-05')!.map((entry) => headline(entry.event).title)).toEqual(['Биатлон. Обзор недели', 'Спринт · Мужчины'])
  })

  it('reads the real collected schedule', () => {
    const schedule: Schedule = JSON.parse(readFileSync('public/data/schedule.json', 'utf-8'))
    const days = buildDays(schedule)
    const total = [...days.values()].flat().reduce((sum, entry) => sum + entry.airings.length, 0)
    expect(total).toBe(schedule.events.reduce((sum, event) => sum + event.broadcasts.length, 0) + schedule.unmatched.length)
  })
})

describe('staleSources', () => {
  it('names only the sources that failed, with the age of their data', () => {
    const status = {
      checked_at: '', last_success: null, stale: true, errors: {}, unpublished: [],
      sources: {
        match_guide: { last_success: '2026-11-26T04:15:00Z', stale: false, errors: {} },
        match_live: { last_success: null, stale: true, errors: { x: 'down' } },
        okko_telegram: { last_success: '2026-11-25T16:15:00Z', stale: true, errors: { y: 'down' } },
      },
    }
    expect(staleSources(status)).toEqual([
      'раздел биатлона «Матча»: данных пока нет',
      'расписание Okko (канал «Зимний спорт»): данные от 25 нояб., 19:15',
    ])
  })

  it('still warns for an old status without per-source details', () => {
    expect(staleSources({ checked_at: '', last_success: null, stale: true, errors: {}, unpublished: [] }))
      .toEqual(['часть источников не загрузилась'])
  })
})

describe('labels and states', () => {
  it('names races by discipline and gender, shows by title', () => {
    expect(headline(sprint)).toEqual({ title: 'Спринт · Мужчины', subtitle: 'Кубок мира' })
    expect(headline({ ...sprint, kind: 'show', category: 'show', title: 'Биатлон с Дмитрием Губерниевым', discipline: null }))
      .toEqual({ title: 'Биатлон с Дмитрием Губерниевым', subtitle: 'Программа' })
  })

  it('dates a replay by its original live airing', () => {
    expect(originalDay(sprint)).toBe('2026-12-05')
  })

  it('claims "now" only inside a known interval', () => {
    const row = airing('2026-12-05T11:00:00Z', 'live', '2026-12-05T12:30:00Z')
    expect(airingState(row, new Date('2026-12-05T10:59:00Z'))).toBeNull()
    expect(airingState(row, new Date('2026-12-05T11:30:00Z'))).toBe('now')
    expect(airingState(row, new Date('2026-12-05T12:30:00Z'))).toBe('finished')
    expect(airingState(airing('2026-12-05T11:00:00Z'), new Date('2026-12-06T00:00:00Z'))).toBeNull()
  })

  it('opens today, or the next day with airings', () => {
    expect(initialDay(['2026-11-28', '2026-11-29'], '2026-11-25')).toBe('2026-11-28')
    expect(initialDay(['2026-11-28', '2026-11-29'], '2026-11-29')).toBe('2026-11-29')
    expect(initialDay(['2026-11-28'], '2026-12-01')).toBe('2026-11-28')
  })
})
