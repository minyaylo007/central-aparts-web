/**
 * Події Meta Pixel: ViewContent на квартирі, InitiateCheckout після ціни, Search з пошуку на головній —
 * кожна з content_ids = id квартири (той самий id, що в фіді каталогу). Справжній fbevents.js у
 * смоці не вантажиться (чужа мережа — заглушка); замість черги fbq ставимо свій запис у
 * sessionStorage ДО сторінки — сніпет у <head> бачить готовий fbq і нічого не перезаписує,
 * а запис переживає перехід головна → квартира.
 */
import type { Page } from '@playwright/test';
import { test, expect } from './fixtures';
import { APT_A, availability, calendarFor, dayCell, freeStay } from './helpers';

type Call = [string, string, Record<string, unknown>?, Record<string, unknown>?];

async function recordPixel(page: Page) {
  await page.addInitScript(() => {
    (window as any).fbq = function (...args: unknown[]) {
      try {
        const log = JSON.parse(sessionStorage.getItem('smoke_fbq') || '[]');
        log.push(args);
        sessionStorage.setItem('smoke_fbq', JSON.stringify(log));
      } catch (e) { /* немає сховища — подію не запишемо, тест це побачить */ }
    };
  });
}

const tracked = async (page: Page, ev: string) =>
  ((JSON.parse(await page.evaluate(() => sessionStorage.getItem('smoke_fbq') || '[]')) as Call[])
    .filter((c) => c[0] === 'track' && c[1] === ev)).map((c) => c[2] ?? {});

test.describe('Група 8 · Meta Pixel', () => {
  test('квартира: ViewContent і InitiateCheckout з content_ids', { tag: '@critical' }, async ({ page, request, jsErrors }) => {
    const s = freeStay(await availability(request, APT_A), 2);
    test.skip(!s, 'нема двох вільних ночей у фейку');
    await recordPixel(page);
    await page.goto(`/apartment/${APT_A}?check_in=${s!.ci}&check_out=${s!.co}`);
    await expect.poll(() => tracked(page, 'InitiateCheckout')).toHaveLength(1);
    const [view] = await tracked(page, 'ViewContent');
    expect(view).toMatchObject({ content_ids: [APT_A], content_type: 'product', currency: 'UAH' });
    const [ic] = await tracked(page, 'InitiateCheckout');
    expect(ic).toMatchObject({ content_ids: [APT_A], content_type: 'product', num_items: 1, currency: 'UAH' });
    expect(typeof ic.value).toBe('number');
    expect(jsErrors).toEqual([]);
  });

  test('головна: пошук з датами шле Search з вільними квартирами', async ({ page, request, jsErrors }) => {
    const s = freeStay(await availability(request, APT_A), 1);
    test.skip(!s, 'нема вільної ночі у фейку');
    await recordPixel(page);
    await page.goto('/');
    for (const [sel, iso] of [['#hs-checkin', s!.ci], ['#hs-checkout', s!.co]]) {
      const input = page.locator(sel);
      await (await dayCell(input, await calendarFor(page, input), iso)).click();
    }
    await page.locator('#heroSearch .hsearch-submit').click();
    await page.waitForURL(/\/apartment\//);
    const searches = await tracked(page, 'Search');
    expect(searches).toHaveLength(1);
    expect(searches[0]).toMatchObject({ content_type: 'product', checkin_date: s!.ci, checkout_date: s!.co });
    expect(searches[0].content_ids as string[]).toContain(APT_A);
    expect(jsErrors).toEqual([]);
  });
});
