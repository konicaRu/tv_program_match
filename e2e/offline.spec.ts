import { expect, test } from '@playwright/test'

test('keeps the last downloaded schedule without a network', async ({ page, context }) => {
  await page.goto('./')
  await page.evaluate(() => navigator.serviceWorker.ready)
  // Second load goes through the service worker, which stores the schedule.
  await page.reload()
  await expect(page.locator('.card').first()).toBeVisible()
  const cards = await page.locator('.card').count()

  await context.setOffline(true)
  await page.reload()
  await expect(page.locator('.card')).toHaveCount(cards)
  await expect(page.getByRole('alert')).toHaveCount(0)
})
