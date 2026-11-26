import { expect, test, type Page } from '@playwright/test'
import { readFileSync } from 'node:fs'

const fixture = (name: string) => readFileSync(`e2e/fixtures/${name}`, 'utf-8')

async function open(page: Page, status = fixture('status.json'), schedule = fixture('schedule.json')) {
  await page.clock.setFixedTime(new Date('2026-11-25T12:00:00Z'))
  await page.route('**/data/schedule.json', (route) => route.fulfill({ body: schedule, contentType: 'application/json' }))
  await page.route('**/data/status.json', (route) => route.fulfill({ body: status, contentType: 'application/json' }))
  await page.goto('./')
}

test('opens the next day with airings, in Moscow time', async ({ page }) => {
  await open(page)
  await expect(page.getByRole('button', { name: /28 нояб/ })).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByRole('heading', { level: 2 })).toHaveText('Суббота, 28 ноября')
  const sprint = page.getByRole('button', { name: /Спринт · Мужчины/ })
  await expect(sprint).toContainText('07:45')
  await expect(page.getByText('Обновлено 25 нояб., 17:42')).toBeVisible()
})

test('finds the Guberniev show and its replay in two taps', async ({ page }) => {
  await open(page)
  await page.getByRole('button', { name: /29 нояб/ }).click()
  const show = page.getByRole('button', { name: /Биатлон с Дмитрием Губерниевым/ })
  await show.click()
  await expect(show).toHaveAttribute('aria-expanded', 'true')
  const airings = page.locator(`#${await show.getAttribute('aria-controls')} li`)
  await expect(airings).toHaveCount(3)
  await expect(airings.nth(0)).toContainText('09:45')
  await expect(airings.nth(2)).toContainText('20:40')
  await expect(airings.nth(2)).toContainText('Матч Страна')
  await expect(airings.nth(2)).toContainText('Повтор записи')
})

test('dates a night replay by the race day', async ({ page }) => {
  await open(page)
  await page.getByRole('button', { name: /Спринт · Мужчины/ }).click()
  await expect(page.getByText('Повтор записи · гонка сб, 28 нояб.').first()).toBeVisible()
})

test('has no horizontal scroll', async ({ page }) => {
  await open(page)
  await page.getByRole('button', { name: /Спринт · Мужчины/ }).click()
  const [scroll, width] = await page.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth])
  expect(scroll).toBeLessThanOrEqual(width)
})

test('warns when the schedule is stale', async ({ page }) => {
  const stale = {
    ...JSON.parse(fixture('status.json')), stale: true, errors: { 'https://t.me/s/okkowintersport': 'timed out' },
    sources: {
      match_guide: { last_success: '2026-11-25T14:42:54Z', stale: false, errors: {} },
      okko_telegram: { last_success: '2026-11-24T04:15:00Z', stale: true, errors: { 'https://t.me/s/okkowintersport': 'timed out' } },
    },
  }
  await open(page, JSON.stringify(stale))
  await expect(page.getByRole('status')).toHaveText('Часть расписания могла устареть: расписание Okko (канал «Зимний спорт»): данные от 24 нояб., 07:15.')
  await expect(page.getByRole('button', { name: /Спринт · Мужчины/ })).toBeVisible()
})

test('explains when the schedule cannot be loaded', async ({ page }) => {
  await page.route('**/data/schedule.json', (route) => route.fulfill({ status: 503 }))
  await page.goto('./')
  await expect(page.getByRole('alert')).toContainText('Не удалось загрузить расписание')
})

test('marks Okko airings and links to the Okko schedule post', async ({ page }) => {
  const schedule = JSON.parse(fixture('schedule.json'))
  schedule.events.push({
    event_id: 'kubok-mira-sprint-men-2026-11-29', kind: 'race', title: 'Кубок мира. Спринт',
    competition: 'Кубок мира', discipline: 'Спринт', gender: 'men', category: 'international',
    broadcasts: [{
      broadcast_id: 'okko-sprint-men-1795867200', platform: 'OKKO', channel: 'Okko',
      title: 'Биатлон. Кубок мира. Спринт. Мужчины', type: 'unknown', start_utc: '2026-11-29T12:00:00Z',
      end_utc: null, url: 'https://t.me/okkowintersport/2494', source_url: 'https://t.me/okkowintersport/2494',
      verified_at: '2026-11-25T17:00:00Z',
    }],
  })
  await open(page, fixture('status.json'), JSON.stringify(schedule))
  await page.getByRole('button', { name: /29 нояб/ }).click()
  const card = page.getByRole('button', { name: /Спринт · Мужчины.*Кубок мира/ })
  await expect(card).toContainText('15:00')
  await expect(card.locator('.badge--okko')).toHaveText('Okko')
  await card.click()
  await expect(page.getByRole('link', { name: 'Расписание Okko в Telegram' })).toHaveAttribute('href', 'https://t.me/okkowintersport/2494')
})
