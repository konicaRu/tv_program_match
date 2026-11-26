import { expect, test, type Page } from '@playwright/test'
import { readFileSync } from 'node:fs'

const fixture = (name: string) => readFileSync(`e2e/fixtures/${name}`, 'utf-8')

async function open(page: Page, path: string, schedule = fixture('schedule.json')) {
  await page.clock.setFixedTime(new Date('2026-11-26T12:00:00Z'))
  await page.route('**/data/schedule.json', (route) => route.fulfill({ body: schedule, contentType: 'application/json' }))
  await page.route('**/data/status.json', (route) => route.fulfill({ body: fixture('status.json'), contentType: 'application/json' }))
  await page.route('**/data/ibu/**', (route) => {
    const name = new URL(route.request().url()).pathname.split('/data/ibu/')[1]
    try {
      route.fulfill({ body: fixture(`ibu/${name}`), contentType: 'application/json' })
    } catch {
      route.fulfill({ status: 404 })
    }
  })
  await page.goto(`./${path}`)
}

test('races tab opens the current stage and a start list without results', async ({ page }) => {
  await open(page, '')
  await page.getByRole('link', { name: 'Гонки' }).click()
  await expect(page.getByRole('heading', { name: 'Гонки сезона 2026/2027' })).toBeVisible()
  const race = page.getByRole('link', { name: /Индивидуальная гонка · Женщины · 15 км/ })
  await expect(race).toContainText('пт, 27 нояб., 18:15')
  await race.click()
  await expect(page.getByRole('heading', { name: 'Стартовый протокол · 103' })).toBeVisible()
  await expect(page.locator('tbody tr').first()).toContainText('TODOROVA Milena')
  await expect(page.locator('tbody tr').first()).toContainText('18:15')
  await expect(page.getByRole('button', { name: 'Показать результаты' })).toHaveCount(0)
  await page.goBack()
  await expect(page.getByRole('heading', { name: 'Гонки сезона 2026/2027' })).toBeVisible()
})

test('results stay hidden until asked for', async ({ page }) => {
  await open(page, '#/race/BT2627SWRLCP01SMIN')
  await expect(page.getByRole('heading', { name: /Стартовый протокол/ })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Результаты' })).toHaveCount(0)
  await page.getByRole('button', { name: 'Показать результаты' }).click()
  const results = page.locator('section', { has: page.getByRole('heading', { name: 'Результаты' }) })
  await expect(results.locator('tbody tr').first().locator('td').first()).toHaveText('1')
})

test('standings open only on request, by gender', async ({ page }) => {
  await open(page, '#/standings')
  await expect(page.locator('table')).toHaveCount(0)
  await page.getByRole('button', { name: 'Показать зачёт' }).click()
  await expect(page.getByRole('tab', { name: 'Мужчины' })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByText(/После 1 из 21 гонок/)).toBeVisible()
  await expect(page.locator('tbody tr')).toHaveCount(40)
  await page.getByRole('tab', { name: 'Женщины' }).click()
  await expect(page.getByText('Зачёт появится после первой гонки.')).toBeVisible()
})

test('an Okko airing links to the IBU start list of its race', async ({ page }) => {
  const schedule = JSON.parse(fixture('schedule.json'))
  schedule.events.push({
    event_id: 'kubok-mira-individual-women-2026-11-27', kind: 'race', title: 'Кубок мира. Индивидуальная гонка',
    competition: 'Кубок мира', discipline: 'Индивидуальная гонка', gender: 'women', category: 'international',
    broadcasts: [{
      broadcast_id: 'okko-individual-women-1795965300', platform: 'OKKO', channel: 'Okko',
      title: 'Биатлон. Кубок мира. Индивидуальная гонка. Женщины', type: 'unknown', start_utc: '2026-11-27T15:15:00Z',
      end_utc: null, url: 'https://t.me/okkowintersport/2494', source_url: 'https://t.me/okkowintersport/2494',
      verified_at: '2026-11-26T09:00:00Z',
    }],
  })
  await open(page, '', JSON.stringify(schedule))
  await page.getByRole('button', { name: /27 нояб/ }).click()
  await page.getByRole('button', { name: /Индивидуальная гонка · Женщины/ }).click()
  await page.getByRole('link', { name: 'Стартовый протокол IBU →' }).click()
  await expect(page).toHaveURL(/#\/race\/BT2627SWRLCP01SWIN$/)
  await expect(page.getByRole('heading', { name: 'Стартовый протокол · 103' })).toBeVisible()
})

test('ibu pages have no horizontal scroll', async ({ page }) => {
  for (const path of ['#/races', '#/race/BT2627SWRLCP01SWIN', '#/standings']) {
    await open(page, path)
    await page.waitForLoadState('networkidle')
    const [scroll, width] = await page.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth])
    expect(scroll, path).toBeLessThanOrEqual(width)
  }
})
