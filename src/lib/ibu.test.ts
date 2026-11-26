import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { currentEvent, findRace, racesByStart, resultRows, type RaceFile, type Season } from './ibu'

const season: Season = JSON.parse(readFileSync('e2e/fixtures/ibu/season.json', 'utf-8'))

describe('IBU season', () => {
  it('opens the stage that is running or comes next', () => {
    expect(currentEvent(season, new Date('2026-11-26T12:00:00Z'))?.name).toBe('Kontiolahti')
    expect(currentEvent(season, new Date('2026-12-01T12:00:00Z'))?.name).toBe('Hochfilzen')
  })

  it('finds a race by its start time and by its id', () => {
    expect(racesByStart(season).get('2026-11-27T15:15:00Z')?.race_id).toBe('BT2627SWRLCP01SWIN')
    expect(findRace(season, 'BT2627SWRLCP02SMPU')?.event.name).toBe('Hochfilzen')
    expect(findRace(season, 'nope')).toBeNull()
  })
})

describe('resultRows', () => {
  it('lists finishers by rank, then non-finishers', () => {
    const race: RaceFile = JSON.parse(readFileSync('e2e/fixtures/ibu/races/BT2627SWRLCP01SMIN.json', 'utf-8'))
    const rows = resultRows(race.rows)
    const finishers = rows.filter((row) => !row.irm)
    expect(finishers.map((row) => Number(row.rank))).toEqual([...finishers.map((row) => Number(row.rank))].sort((a, b) => a - b))
    expect(rows.slice(finishers.length).every((row) => row.irm)).toBe(true)
  })
})
