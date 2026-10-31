/**
 * Група 3 «Купон постійного гостя» — сценарії 12–15 з внутрішнього звіту сайту §5.3 (див. e2e/README.md).
 * static/main.js: згорнутий блок #coupon-*, перевірка телефону (без дат — POST /api/coupon/check,
 * з датами — одна /api/quote?coupon_phone=), рядок −500 лише від сервера, приховані поля форми.
 *
 * Усі сценарії ПИШУТЬ у бек-офіс (перевірка купона має ліміт і журнал) — у live skip.
 * Тексти статусу беремо з translations.py (tr), суми — з перехопленої відповіді /api/quote;
 * формулу знижки тест НЕ повторює: сервер — єдина правда.
 */
import type { Page, Response } from '@playwright/test';
import { test, expect, skipWritesInLive } from './fixtures';
import {
  APT_A, addDays, availability, exactText, expectOptRow, expectServerQuote, fakeState, freeStay, guestName,
  pickCheckIn, pickCheckOut, pickStay, tr, uahPattern, waitQuote, type Quote, type Stay,
} from './helpers';

// e2e/fake_backoffice.py: COUPON_PHONES — має право; решта — ні
const COUPON_PHONE = '+380000000500';
const UNKNOWN_PHONE = '+380000000013';

const couponKeys = (q: object) => Object.keys(q).filter((k) => k.startsWith('coupon_'));

/** Квартира A, вільна стоянка на `nights` ночей; сторінка відкрита. */
async function openA(page: Page, request: Parameters<typeof availability>[0], nights: number): Promise<Stay> {
  const stay = freeStay(await availability(request, APT_A), nights);
  expect(stay, `fake: у A нема вільних ${nights} ночей`).not.toBeNull();
  await page.goto(`/apartment/${APT_A}`);
  return stay!;
}

async function quoteOf(pending: Promise<Response>): Promise<Quote> {
  const r = await pending;
  expect(r.status(), `статус ${r.url()}`).toBe(200);
  const q = await r.json();
  expect(typeof q.total, 'форма відповіді /api/quote').toBe('number');
  return q;
}

/** Розкрити блок купона (якщо згорнутий) і ввести телефон. */
async function typeCouponPhone(page: Page, phone: string) {
  const toggle = page.locator('#coupon-toggle');
  if ((await toggle.getAttribute('aria-expanded')) !== 'true') await toggle.click();
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  await page.locator('#coupon-phone-input').fill(phone);
}

/** Статус «купон застосовано» з сумою знижки від сервера. */
function appliedText(discount: number): RegExp {
  const [before, after] = tr('coupon_applied').split('{n}').map((s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  return new RegExp(`^\\s*${before}${uahPattern(Math.abs(discount))}${after}\\s*$`);
}

test.describe('Група 3 · Купон постійного гостя', { tag: '@critical' }, () => {
  test('12. блок купона згорнутий за замовчуванням; клік розкриває, повторний — згортає', async ({ page, request, jsErrors }) => {
    skipWritesInLive('блок купона (сценарії групи 3)');
    const stay = await openA(page, request, 2);
    const toggle = page.locator('#coupon-toggle');
    const body = page.locator('#coupon-body');
    const input = page.locator('#coupon-phone-input');
    const fieldHeight = () => page.locator('.coupon-field--priced > .coupon-field-inner')
      .evaluate((el) => el.getBoundingClientRect().height);

    // до дат блок купона згорнутий разом із ціною (C6: поле під сумою, його поява не штовхає цифри)
    await expect(toggle).toHaveText(exactText(tr('coupon_have')));
    await expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await expect(toggle).toHaveAttribute('aria-controls', 'coupon-body');
    await expect(body).toHaveAttribute('hidden');
    await expect.poll(fieldHeight, 'до дат блок купона мав бути згорнутий').toBe(0);

    // дати → сума → зʼявляється кнопка купона, але саме поле телефону ще згорнуте
    const quote = waitQuote(page, APT_A, stay, 2, null);
    await pickStay(page, stay);
    await expectServerQuote(page, await quoteOf(quote));
    await expect.poll(fieldHeight, 'після суми кнопка купона не зʼявилась').toBeGreaterThan(0);
    await expect(toggle).toBeVisible();
    await expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await expect(body).toHaveAttribute('hidden');
    await expect(body).toBeHidden();
    await expect(input).toBeHidden();

    await toggle.click();
    await expect(toggle).toHaveAttribute('aria-expanded', 'true');
    await expect(body).not.toHaveAttribute('hidden');
    await expect(body).toHaveClass(/\bis-open\b/);
    await expect(input).toBeVisible();
    await expect(input).toBeFocused(); // main.js фокусує поле одразу (iOS показує клавіатуру)
    await expect(page.locator('#coupon-apply')).toBeEnabled();
    await expect(page.locator('#coupon-status')).toHaveAttribute('role', 'status');

    await toggle.click();
    await expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await expect(body).not.toHaveClass(/\bis-open\b/);
    // згорнутий вміст не має ловити Tab (main.js ставить inert)
    await expect(body).toHaveJSProperty('inert', true);

    expect(jsErrors).toEqual([]);
  });

  test('13. телефон без права → чесна загальна відмова, без рядка знижки і без coupon_* у котируванні', async ({ page, request, jsErrors }) => {
    skipWritesInLive('перевірка купона');
    const first = await openA(page, request, 2);
    const status = page.locator('#coupon-status');
    const notFound = exactText(tr('coupon_not_found'));

    const plain0 = waitQuote(page, APT_A, first, 2, null);
    await pickStay(page, first);
    await expectServerQuote(page, await quoteOf(plain0));

    // а) гість переніс заїзд після старого виїзду → виїзд скинуто, сума «протухла» (is-stale),
    //    блок купона на місці. Дат повних нема → «Застосувати» йде в POST /api/coupon/check
    const stay = freeStay(await availability(request, APT_A), 2, addDays(first.co, 1))!;
    await pickCheckIn(page, stay.ci);
    await expect(page.locator('#f-check-out')).toHaveValue('');
    await expect(page.locator('#booking-form')).toHaveClass(/\bis-stale\b/);
    // на десктопі календар виїзду відкрився сам — гість закриває його кліком повз (по заголовку)
    await page.locator('.apt-detail-name').click();
    await expect.poll(() => page.locator('#picker-checkout').evaluate((el) => (el as any)._flatpickr.isOpen)).toBe(false);

    await typeCouponPhone(page, UNKNOWN_PHONE);
    const check = page.waitForResponse((r) => new URL(r.url()).pathname === '/api/coupon/check');
    await page.locator('#coupon-apply').click();
    const cr = await check;
    expect(cr.request().method()).toBe('POST');
    expect(cr.request().postDataJSON()).toEqual({ phone: UNKNOWN_PHONE });
    expect(cr.status()).toBe(200);
    expect(await cr.json(), 'відмова без подробиць (сигналу для перебору нема)').toEqual({ eligible: false });
    await expect(status).toHaveText(notFound);
    await expect(status).toHaveClass(/\bis-err\b/);
    await expect(page.locator('#f-coupon-phone')).toHaveValue('');

    // б) виїзд → котирування без купона; повторне «Застосувати» → /api/quote?coupon_phone=
    const plain = waitQuote(page, APT_A, stay, 2, null);
    await pickCheckOut(page, stay.co);
    const q0 = await quoteOf(plain);
    await expectServerQuote(page, q0);

    const withPhone = waitQuote(page, APT_A, stay, 2, UNKNOWN_PHONE);
    await page.locator('#coupon-apply').click();
    const q = await quoteOf(withPhone);
    expect(couponKeys(q), 'невідомий телефон: у котируванні жодного coupon_*').toEqual([]);
    await expect(status).toHaveText(notFound);
    await expect(status).toHaveClass(/\bis-err\b/);
    await expectOptRow(page.locator('#pb-coupon-row'), false);
    await expectServerQuote(page, q);
    // у форму /book невідомий телефон не йде
    await expect(page.locator('#f-coupon-phone')).toHaveValue('');
    await expect(page.locator('#f-coupon-expected')).toHaveValue('');

    expect(jsErrors).toEqual([]);
  });

  test('14. телефон із правом, сума ≥ мінімуму → повторне котирування з coupon_phone і −500 від сервера', async ({ page, request, jsErrors }) => {
    skipWritesInLive('перевірка купона');
    const stay = await openA(page, request, 2);

    const plain = waitQuote(page, APT_A, stay, 2, null);
    await pickStay(page, stay);
    const q0 = await quoteOf(plain);
    await expectServerQuote(page, q0);
    await expectOptRow(page.locator('#pb-coupon-row'), false);

    await typeCouponPhone(page, COUPON_PHONE);
    const withCoupon = waitQuote(page, APT_A, stay, 2, COUPON_PHONE);
    await page.locator('#coupon-apply').click();
    const q = await quoteOf(withCoupon);
    expect(typeof q.coupon_discount, 'сервер додав рядок купона').toBe('number');
    const discount = q.coupon_discount as number;
    expect(discount).toBeLessThan(0);
    expect(q.coupon_applied).toBe(true);
    expect(q.total, 'знижка вже в серверному total').toBeLessThan(q0.total);

    // що бачить гість — рівно серверне: рядок −500, підсумок = total з відповіді
    await expectServerQuote(page, q);
    await expectOptRow(page.locator('#pb-coupon-row'), true);
    await expect(page.locator('#pb-coupon')).toHaveText(new RegExp(`^\\s*−\\s*${uahPattern(Math.abs(discount))}\\s*$`));
    await expect(page.locator('#coupon-status')).toHaveText(appliedText(discount));
    await expect(page.locator('#coupon-status')).toHaveClass(/\bis-ok\b/);
    await expect(page.locator('#pb-announce')).toContainText(new RegExp(uahPattern(q.total)));

    expect(jsErrors).toEqual([]);
  });

  test('14б. телефон із правом, сума < мінімуму → знижки нема, гість бачить пояснення про мінімум', async ({ page, request, jsErrors }) => {
    skipWritesInLive('перевірка купона');
    const stay = await openA(page, request, 1); // одна ніч — дешевше за мінімум купона
    const status = page.locator('#coupon-status');

    const plain = waitQuote(page, APT_A, stay, 2, null);
    await pickStay(page, stay);
    await expectServerQuote(page, await quoteOf(plain));

    await typeCouponPhone(page, COUPON_PHONE);
    const withCoupon = waitQuote(page, APT_A, stay, 2, COUPON_PHONE);
    await page.locator('#coupon-apply').click();
    const q = await quoteOf(withCoupon);
    expect(q.coupon_applied, 'сервер: купон не застосовано').toBe(false);
    expect(typeof q.coupon_min_total).toBe('number');
    expect(q.coupon_discount, 'знижки нема').toBeUndefined();
    const minTotal = q.coupon_min_total as number;
    expect(q.base_amount + q.tourist_tax, 'стоянка дешевша за мінімум').toBeLessThan(minTotal);

    await expectServerQuote(page, q); // підсумок — повна серверна сума
    await expectOptRow(page.locator('#pb-coupon-row'), false);
    // пояснення: мінімум від сервера і скільки бракує (обидва числа — з відповіді)
    const [a, b, c2] = tr('coupon_min_total').split(/\{n\}|\{gap\}/).map((s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
    const nPattern = String(minTotal).replace(/\B(?=(\d{3})+(?!\d))/g, '\\s?');
    await expect(status).toHaveText(new RegExp(
      `^\\s*${a}${nPattern}${b}${uahPattern(minTotal - (q.base_amount + q.tourist_tax))}${c2}\\s*$`));
    await expect(status).toHaveClass(/\bis-err\b/);
    // телефон лишається (додасть ніч — знижка зʼявиться), але «очікую знижку» не ставиться
    await expect(page.locator('#f-coupon-phone')).toHaveValue(COUPON_PHONE);
    await expect(page.locator('#f-coupon-expected')).toHaveValue('');

    expect(jsErrors).toEqual([]);
  });

  test('15. після купона приховані поля заповнені й доїхали в /book (coupon_phone, coupon_expected)', async ({ page, request, jsErrors }) => {
    skipWritesInLive('перевірка купона і POST /book');
    const stay = await openA(page, request, 2);
    const name = guestName('15');

    await pickStay(page, stay);
    await typeCouponPhone(page, COUPON_PHONE);
    const withCoupon = waitQuote(page, APT_A, stay, 2, COUPON_PHONE);
    await page.locator('#coupon-apply').click();
    const q = await quoteOf(withCoupon);
    await expectServerQuote(page, q);
    await expectOptRow(page.locator('#pb-coupon-row'), true);

    await expect(page.locator('#f-coupon-phone')).toHaveValue(COUPON_PHONE);
    await expect(page.locator('#f-coupon-expected')).toHaveValue('1');
    // телефон броні main.js підставляє з купона, якщо гість його ще не ввів
    await expect(page.locator('#w-phone')).toHaveValue(COUPON_PHONE);

    await page.locator('#w-name').fill(name);
    await page.locator('#btn-book').click();
    await page.waitForURL((u) => u.hostname === 'pay.monobank.ua');
    await expect(page.locator('[data-smoke-stub="pay.monobank.ua"]')).toBeAttached();

    const sent = (await fakeState(request)).booking_requests.filter((b: any) => b.name === name);
    expect(sent, `форма /book з імʼям «${name}»`).toHaveLength(1);
    expect(sent[0]).toMatchObject({
      apartment_id: APT_A, check_in: stay.ci, check_out: stay.co, guests: 2,
      phone: COUPON_PHONE, coupon_phone: COUPON_PHONE, coupon_expected: '1',
    });
    // оплата — на суму зі знижкою: те саме котирування, що бачив гість
    const ref = new URL(page.url()).searchParams.get('ref')!;
    expect((await fakeState(request)).bookings[ref]?.total).toBe(q.total);

    expect(jsErrors).toEqual([]);
  });
});
