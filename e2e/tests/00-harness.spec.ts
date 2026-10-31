/**
 * Самоперевірка каркаса: сторож справді глушить аналітику власника, а бібліотеки з CDN
 * приходять із node_modules. Упаде тут — довіряти решті смоки не можна.
 */
import { test, expect } from './fixtures';

test('каркас: GTM і пиксель Facebook заглушені, CDN віддано з node_modules',
  { tag: '@critical' }, async ({ page, traffic }) => {
  await page.goto('/');
  await page.waitForLoadState('load');

  const hit = (host: string) => traffic.stubbed.some((u) => new URL(u.split(' ')[1]).hostname === host);
  // GTM і пиксель підвантажуються після load (22.10, LCP) — чекаємо, а не дивимось миттєво
  await expect.poll(() => hit('www.googletagmanager.com'), { message: 'gtm.js мав бути перехоплений' }).toBe(true);
  await expect.poll(() => hit('connect.facebook.net'), { message: 'fbevents.js мав бути перехоплений' }).toBe(true);

  expect(traffic.cdn.some((p) => p.endsWith('/flatpickr.min.js'))).toBe(true);
  expect(await page.evaluate(() => typeof (window as any).flatpickr)).toBe('function');
});
