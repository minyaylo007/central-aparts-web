/**
 * Група 6 «Кілька апартаментів однією оплатою» — спека 108 бек-офісу (06.10.2026).
 *
 * Гість на сторінці A додає B на ті самі дати: смета по кімнатах і підсумок — рівно числа з
 * /api/quote-group (у fake збір 37, не 48, і рядки — з бек-офісу), бронь іде на /book-group,
 * сервер відповідає 302 на Монобанк (заглушка сторожа), /booking-confirmed перелічує обидві.
 * Гілка «зайняли між сметою і оплатою» — імʼя з позначкою [гонка] (fake_backoffice.py):
 * гостю — назва квартири, не id і не код; зайнята знята з вибору.
 * Усе пише в бек-офіс — у live skip.
 */
import type { Page, Request, Response } from '@playwright/test';
import { test, expect, skipWritesInLive } from './fixtures';
import {
  APT_A, APT_B, availability, digits, exactText, fakeState, freeStay, guestName, pickStay, tr, uah,
  waitQuote, expectServerQuote, type Stay,
} from './helpers';

const GUEST_PHONE = '+380 00 000 01 08'; // вигаданий; у fake нікуди не йде
const NAME_B = 'Апартаменти B (смока)';
const UUID4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

/** Дати, вільні і в A, і в B. */
async function freeForBoth(request: Parameters<typeof availability>[0], nights = 2): Promise<Stay> {
  const busy = [...await availability(request, APT_A), ...await availability(request, APT_B)];
  const stay = freeStay(busy, nights);
  expect(stay, 'fake: нема спільних вільних ночей у A і B').not.toBeNull();
  return stay!;
}

function waitGroupQuote(page: Page, stay: Stay, guests: string): Promise<Response> {
  return page.waitForResponse((r) => {
    const u = new URL(r.url());
    return u.pathname === '/api/quote-group' && u.searchParams.get('apartment_ids') === `${APT_A},${APT_B}` &&
      u.searchParams.get('check_in') === stay.ci && u.searchParams.get('check_out') === stay.co &&
      u.searchParams.get('guests') === guests;
  });
}

/** Сторінка A з датами, одиночне котирування на місці, блок групи відкритий, B відмічено. */
async function groupAB(page: Page, stay: Stay) {
  await page.goto(`/apartment/${APT_A}`);
  const single = waitQuote(page, APT_A, stay, 2);
  await pickStay(page, stay);
  await expectServerQuote(page, await (await single).json());

  const field = page.locator('#group-field');
  await expect(field).toBeVisible();
  await page.locator('#group-toggle').click();
  const roomB = field.locator(`.group-room[data-apt-id="${APT_B}"]`);
  await expect(roomB).toBeVisible();
  await expect(field.locator(`.group-room[data-apt-id="${APT_A}"]`)).toHaveCount(0); // себе не пропонує
  const pending = waitGroupQuote(page, stay, '2,2');
  await roomB.locator('.group-room-cb').check();
  const r = await pending;
  expect(r.status()).toBe(200);
  return { roomB, q: await r.json() };
}

/** Віджет показує рівно групову смету `q`: рядок на кімнату, збір і підсумок — числа сервера. */
async function expectGroupQuote(page: Page, q: any) {
  await expect(page.locator('#group-breakdown')).toBeVisible();
  await expect(page.locator('#group-breakdown')).not.toHaveAttribute('aria-busy', 'true');
  const rows = page.locator('#group-rows .group-row');
  await expect(rows).toHaveCount(q.rooms.length);
  for (const [i, room] of q.rooms.entries()) {
    await expect(rows.nth(i).locator('span').first()).toContainText(room.name);
    await expect(rows.nth(i).locator('span').first()).toContainText(`${room.guests} ${tr('group_guests_short')}`);
    await expect(rows.nth(i).locator(':scope > span').last()).toHaveText(uah(room.base_amount));
  }
  await expect(page.locator('#group-tax')).toHaveText(uah(q.tourist_tax));
  await expect(page.locator('#group-total')).toHaveText(uah(q.total));
  await expect(page.locator('#group-total-label')).toHaveText(
    exactText(tr('group_total_few').replace('{n}', String(q.rooms.length))));
  await expect(page.locator('#btn-book')).toBeEnabled();
}

function groupPost(page: Page): Promise<Request> {
  return page.waitForRequest((r) => r.method() === 'POST' && new URL(r.url()).pathname === '/book-group');
}

test.describe('Група 6 · Кілька апартаментів однією оплатою', { tag: '@critical' }, () => {
  test('30. A + B: смета по кімнатах від сервера → /book-group → Монобанк → підтвердження з обома', async ({ page, request, traffic, jsErrors }) => {
    skipWritesInLive('POST /book-group');
    const stay = await freeForBoth(request);
    const { roomB, q: q22 } = await groupAB(page, stay);
    await expectGroupQuote(page, q22);
    // fake навмисно рахує збір 37 за гостя-ніч: 48 з main.js тут дало б іншу суму
    expect(q22.tourist_tax).toBe(37 * 2 * 2 * q22.nights);

    // 3 гостей у B — нова смета, віджет показує її числа
    const p23 = waitGroupQuote(page, stay, '2,3');
    await roomB.locator('.group-room-guests').selectOption('3');
    const q23 = await (await p23).json();
    await expectGroupQuote(page, q23);
    expect(q23.total).not.toBe(q22.total);
    // липка ціна (мобільний) — теж підсумок групи
    await expect(page.locator('#stickyPrice')).toHaveText(exactText(Number(q23.total).toLocaleString('uk-UA')));

    const name = guestName('30');
    await page.locator('#w-name').fill(name);
    await page.locator('#w-phone').fill(GUEST_PHONE);
    const post = groupPost(page);
    await page.locator('#btn-book').click();
    const req = await post;
    await page.waitForURL((u) => u.hostname === 'pay.monobank.ua');
    await expect(page.locator('[data-smoke-stub="pay.monobank.ua"]')).toBeAttached();

    const form = new URLSearchParams(req.postData() ?? '');
    expect(form.getAll('room')).toEqual([APT_A, APT_B]);
    expect(form.getAll('room_guests')).toEqual(['2', '3']);
    expect(form.get('idempotency_key')).toMatch(UUID4);

    expect(traffic.offsite).toHaveLength(1);
    expect(traffic.offsite[0]).toMatchObject({ from: 'POST /book-group', status: 302 });
    const pay = new URL(traffic.offsite[0].location);
    expect(page.url()).toBe(pay.href);

    const sent = (await fakeState(request)).group_requests.filter((g: any) => g.name === name);
    expect(sent, `/book-group з імʼям «${name}»`).toHaveLength(1);
    expect(sent[0]).toMatchObject({
      items: [{ apartment_id: APT_A, guests: 2 }, { apartment_id: APT_B, guests: 3 }],
      check_in: stay.ci, check_out: stay.co, phone: GUEST_PHONE, idempotency_key: form.get('idempotency_key'),
    });

    // підтвердження: обидві кімнати і підсумок групи — той самий, що в сметі
    const ref = pay.searchParams.get('ref')!;
    const r = await page.goto(`/booking-confirmed?ref=${encodeURIComponent(ref)}`);
    expect(r?.status()).toBe(200);
    const items = page.locator('#group-confirmed-list li');
    await expect(items).toHaveCount(2);
    await expect(items.nth(0)).toContainText(`Апартаменти A (смока) · 2 ${tr('group_guests_short')}`);
    await expect(items.nth(1)).toContainText(`${NAME_B} · 3 ${tr('group_guests_short')}`);
    await expect(page.locator('.confirmed-details')).toContainText(String(q23.total));

    expect(jsErrors).toEqual([]);
  });

  test('31. dates_unavailable між сметою й оплатою: гостю — назва квартири, B знята з вибору', async ({ page, request, traffic, jsErrors }) => {
    skipWritesInLive('POST /book-group');
    const stay = await freeForBoth(request);
    const { q } = await groupAB(page, stay);
    await expectGroupQuote(page, q);

    const name = guestName('31 [гонка]');
    await page.locator('#w-name').fill(name);
    await page.locator('#w-phone').fill(GUEST_PHONE);
    const post = groupPost(page);
    await page.locator('#btn-book').click();
    await post;
    await page.waitForURL((u) => u.pathname === `/apartment/${APT_A}`);
    expect(traffic.offsite, 'до Монобанку не мали дійти').toEqual([]);

    const alert = page.locator('.alert-danger');
    await expect(alert).toHaveText(exactText(tr('group_room_unavailable').replace('{name}', NAME_B)));
    await expect(alert).not.toContainText(APT_B);
    const back = new URL(page.url());
    expect(back.searchParams.get('check_in')).toBe(stay.ci);
    expect(back.searchParams.get('check_out')).toBe(stay.co);
    expect(back.searchParams.has('group'), 'зайнята B лишилась у виборі').toBe(false);
    await expect(page.locator(`.group-room[data-apt-id="${APT_B}"] .group-room-cb`)).not.toBeChecked();

    const sent = (await fakeState(request)).group_requests.filter((g: any) => g.name === name);
    expect(sent).toHaveLength(1);
    expect(digits(sent[0].phone)).toBe(digits(GUEST_PHONE));

    expect(jsErrors).toEqual([]);
  });
});
