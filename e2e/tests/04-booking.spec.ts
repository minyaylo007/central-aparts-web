/**
 * Група 4 «Бронювання до Монобанку» — сценарії 16–19 з внутрішнього звіту сайту §5.3 (див. e2e/README.md),
 * плюс сторінка /booking-confirmed (лише читаємо).
 *
 * СТОП на редиректі: /book → 302 → pay.monobank.ua. У браузері це сторінка-заглушка сторожа
 * ([data-smoke-stub="pay.monobank.ua"]), справжній Монобанк не відкривається і платежів нема.
 * Усе, крім 16, пише в бек-офіс (POST /book створює бронь і рахунок) — у live skip.
 *
 * Потік оплати (main.js, /book, збір, кнопка підтвердження) НЕ змінюється — рішення власника;
 * тест лише читає, що він робить.
 */
import type { Page, Request } from '@playwright/test';
import { test, expect, skipWritesInLive } from './fixtures';
import { BACKOFFICE_URL, IS_LIVE } from '../smoke-env';
import {
  APT_A, availability, digits, exactText, expectServerQuote, fakeState, freeStay, guestName, note,
  pickStay, tr, uah, waitQuote, type Quote, type Stay,
} from './helpers';

const GUEST_PHONE = '+380 00 000 00 18'; // вигаданий; у fake нікуди не йде

async function openAWithStay(page: Page, request: Parameters<typeof availability>[0], nights = 2): Promise<Stay> {
  const stay = freeStay(await availability(request, APT_A), nights);
  expect(stay, `fake: у A нема вільних ${nights} ночей`).not.toBeNull();
  await page.goto(`/apartment/${APT_A}`);
  return stay!;
}

/** Дати вибрані гостем, віджет показує серверне котирування. */
async function priced(page: Page, stay: Stay, guests = 2): Promise<Quote> {
  const pending = waitQuote(page, APT_A, stay, guests);
  await pickStay(page, stay);
  const r = await pending;
  expect(r.status()).toBe(200);
  const q = await r.json();
  await expectServerQuote(page, q);
  return q;
}

/** Усі POST /book, які браузер реально відправив. */
function bookPosts(page: Page): Request[] {
  const posts: Request[] = [];
  page.on('request', (r) => { if (r.method() === 'POST' && new URL(r.url()).pathname.startsWith('/book/')) posts.push(r); });
  return posts;
}

/** Натиснути «Перейти до оплати» і дочекатися заглушки Монобанку. Повертає POST /book. */
async function submitToMonobank(page: Page): Promise<Request> {
  const post = page.waitForRequest((r) => r.method() === 'POST' && new URL(r.url()).pathname === `/book/${APT_A}`);
  await page.locator('#btn-book').click();
  const req = await post;
  await page.waitForURL((u) => u.hostname === 'pay.monobank.ua');
  await expect(page.locator('[data-smoke-stub="pay.monobank.ua"]')).toBeAttached();
  return req;
}

async function sentBy(request: Parameters<typeof fakeState>[0], name: string) {
  return (await fakeState(request)).booking_requests.filter((b: any) => b.name === name);
}

/** Сума, яку показує віджет прямо зараз (0 — порожньо). */
const shownTotal = (page: Page) => page.locator('#pb-total').textContent().then(digits);

test.describe('Група 4 · Бронювання до Монобанку', { tag: '@critical' }, () => {
  test('16. /?test=1 показує тестову квартиру 1 грн, звичайна / — ні', async ({ page, request, jsErrors }) => {
    const idsOn = async (url: string) => {
      await page.goto(url);
      return {
        cards: await page.locator('a[data-apt]').evaluateAll((els) => els.map((e) => e.getAttribute('data-apt')!)),
        options: await page.locator('#hs-apt option').evaluateAll((els) => els.map((e) => (e as HTMLOptionElement).value)),
        prices: await page.locator('.apt-row .price-value').allTextContents(),
      };
    };
    const plain = await idsOn('/');
    const withTest = await idsOn('/?test=1');

    // публічний каталог — без квартир за 1 грн (app.py _public_apartments)
    expect(plain.cards.length).toBeGreaterThan(0);
    for (const p of plain.prices) expect(digits(p), `/: ціна «${p}»`).toBeGreaterThan(1);

    // ?test=1 — ті самі квартири + тестові наприкінці, і в картках, і в селекторі hero-пошуку
    const extra = withTest.cards.filter((id) => !plain.cards.includes(id));
    // live: тестова квартира живе лише в бек-офісі (Config.TEST_APARTMENT_IDS); її там може не бути
    // (22.10.2026 — 404) — заводити не наше, тож без неї перевіряти нічого. У fake вона є завжди.
    test.skip(IS_LIVE && extra.length === 0,
      'live: тестової квартири з TEST_APARTMENT_IDS нема в бек-офісі (GET /apartments/<id> → 404) — /?test=1 нічого не додає');
    expect(extra.length, '/?test=1 не додав тестової квартири').toBeGreaterThan(0);
    expect(withTest.cards.slice(0, plain.cards.length)).toEqual(plain.cards);
    for (const id of extra) {
      expect(withTest.options, `#hs-apt: ${id}`).toContain(id);
      expect(plain.options, `#hs-apt на / містить тестову ${id}`).not.toContain(id);
      const row = page.locator('.apt-row').filter({ has: page.locator(`a[data-apt="${id}"]`) });
      expect(digits(await row.locator('.price-value').textContent()), `${id}: ціна тестової`).toBe(1);
    }
    if (!IS_LIVE) {
      const { ids } = await fakeState(request);
      expect(extra).toEqual([ids.test_apt]);
      expect(plain.cards).not.toContain(ids.test_apt);
    }
    // посилання на тестову квартиру відкривається (читаємо сторінку, нічого не бронюємо)
    const r = await request.get(`/apartment/${extra[0]}`);
    expect(r.status()).toBe(200);

    expect(jsErrors).toEqual([]);
  });

  test('17. без обовʼязкових полів форма не йде; серверна гілка — флеш «заповніть усі поля»', async ({ page, request, jsErrors }) => {
    skipWritesInLive('POST /book');
    const stay = await openAWithStay(page, request);
    await priced(page, stay);
    const posts = bookPosts(page);
    const name = page.locator('#w-name');
    const phone = page.locator('#w-phone');
    const btn = page.locator('#btn-book');

    // а) браузер сам не відправляє: required на імені й телефоні
    await expect(name).toHaveAttribute('required');
    await expect(phone).toHaveAttribute('required');
    await btn.click();
    await expect(name).toBeFocused(); // перевірка форми браузера спинилась на першому порожньому
    expect(await name.evaluate((el) => (el as HTMLInputElement).validity.valueMissing)).toBe(true);

    await name.fill(guestName('17'));
    await btn.click();
    await expect(phone).toBeFocused();
    expect(await phone.evaluate((el) => (el as HTMLInputElement).validity.valueMissing)).toBe(true);

    expect(posts, 'форма пішла на /book з порожніми полями').toEqual([]);
    expect(new URL(page.url()).pathname).toBe(`/apartment/${APT_A}`);
    // submit-обробник не спрацював → кнопка не зависла в «Створюємо платіж…»
    await expect(btn).toBeEnabled();
    await expect(btn).not.toHaveClass(/\bis-loading\b/);

    // б) серверна гілка: POST без телефону з контексту сторінки (та сама cookie сесії)
    const serverName = guestName('17-server');
    const resp = await page.request.post(`/book/${APT_A}`, {
      form: { check_in: stay.ci, check_out: stay.co, guests: '2', name: serverName, phone: '' },
      maxRedirects: 0,
    });
    expect(resp.status()).toBe(302);
    const back = new URL(resp.headers()['location'], page.url());
    expect(back.pathname).toBe(`/apartment/${APT_A}`);
    await page.goto(back.href);
    await expect(page.locator('.alert-danger')).toHaveText(exactText(tr('fill_all_fields')));
    expect(new URL(page.url()).pathname, 'гість лишився на сторінці квартири').toBe(`/apartment/${APT_A}`);
    expect(await sentBy(request, serverName), 'бек-офіс не мав отримати неповну форму').toEqual([]);

    expect(jsErrors).toEqual([]);
  });

  test('18. повна форма → 302 на Монобанк (заглушка); у /book правильні дати, гості, телефон', async ({ page, request, traffic, jsErrors }) => {
    skipWritesInLive('POST /book');
    const stay = await openAWithStay(page, request);
    await priced(page, stay);
    // 3 гостей: нове котирування з guests=3
    const q3p = waitQuote(page, APT_A, stay, 3);
    await page.locator('#guest-plus').click();
    const q3 = await (await q3p).json();
    await expectServerQuote(page, q3);

    const name = guestName('18');
    await page.locator('#w-name').fill(name);
    await page.locator('#w-phone').fill(GUEST_PHONE);
    await expect(page.locator('#f-guests')).toHaveValue('3');

    await submitToMonobank(page);
    // що відповів сервер на POST /book (сторож записав, перш ніж пустити браузер на заглушку)
    expect(traffic.offsite).toHaveLength(1);
    expect(traffic.offsite[0]).toMatchObject({ from: `POST /book/${APT_A}`, status: 302 });
    const pay = new URL(traffic.offsite[0].location);
    expect(pay.hostname).toBe('pay.monobank.ua');
    expect(page.url()).toBe(pay.href); // браузер пішов саме туди, куди сказав сервер

    const sent = await sentBy(request, name);
    expect(sent, `форма /book з імʼям «${name}»`).toHaveLength(1);
    expect(sent[0]).toMatchObject({
      apartment_id: APT_A, check_in: stay.ci, check_out: stay.co, guests: 3,
      phone: GUEST_PHONE, coupon_phone: '', coupon_expected: '',
    });
    const ref = pay.searchParams.get('ref')!;
    expect((await fakeState(request)).bookings[ref], `бронь ${ref}`).toMatchObject({
      apartment_id: APT_A, check_in: stay.ci, check_out: stay.co, total: q3.total,
    });

    expect(jsErrors).toEqual([]);
  });

  // 13.10.2026: колишні 18в і 18г — один сценарій (три submit-и одного гостя): ключ із браузера,
  // новий ключ на повторний submit, запасний шлях без crypto.randomUUID.
  test('18в. Idempotency-Key: UUID з браузера доїхав до бек-офісу, на кожен submit — новий; без randomUUID — getRandomValues', async ({ page, request, jsErrors }) => {
    skipWritesInLive('POST /book');
    const UUID4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
    const keyOf = (r: Request) => new URLSearchParams(r.postData() ?? '').get('idempotency_key') ?? '';
    const stay = await openAWithStay(page, request);
    await priced(page, stay);
    const name = guestName('18v');
    await page.locator('#w-name').fill(name);
    await page.locator('#w-phone').fill(GUEST_PHONE);
    const first = keyOf(await submitToMonobank(page));
    expect(first).toMatch(UUID4);

    // друга спроба на тій самій сторінці, поле вже заповнене старим ключем (як після bfcache):
    // main.js мусить покласти НОВИЙ, а не відправити старий
    await openAWithStay(page, request);
    await priced(page, stay);
    await page.locator('#w-name').fill(name);
    await page.locator('#w-phone').fill(GUEST_PHONE);
    await page.locator('#f-idempotency-key').evaluate((el, k) => { (el as HTMLInputElement).value = k; }, first);
    const second = keyOf(await submitToMonobank(page));
    expect(second).toMatch(UUID4);
    expect(second).not.toBe(first);

    // третя спроба — браузер без crypto.randomUUID (колишній 18г): запасний шлях getRandomValues,
    // той самий вигляд UUID4
    await page.addInitScript(() => { try { delete (Crypto.prototype as any).randomUUID; } catch (e) {} });
    await openAWithStay(page, request);
    await priced(page, stay);
    expect(await page.evaluate(() => typeof (crypto as any).randomUUID)).toBe('undefined');
    await page.locator('#w-name').fill(name);
    await page.locator('#w-phone').fill(GUEST_PHONE);
    const third = keyOf(await submitToMonobank(page));
    expect(third).toMatch(UUID4);
    expect(third).not.toBe(second);

    const sent = await sentBy(request, name);
    expect(sent.map((b: any) => b.idempotency_key)).toEqual([first, second, third]);
    expect(jsErrors).toEqual([]);
  });

  test('18б. зайняті дати на /book → флеш dates_unavailable, зайняті дати НЕ повертаються у форму', async ({ page, request, jsErrors }) => {
    skipWritesInLive('POST /book');
    const [busy] = await availability(request, APT_A);
    const stay = await openAWithStay(page, request);
    await priced(page, stay);
    const name = guestName('18b');
    await page.locator('#w-name').fill(name);
    await page.locator('#w-phone').fill(GUEST_PHONE);

    // Гонка «поки гість заповнював форму, дати забронював інший»: календар зайняте не дає
    // вибрати, тож підміняємо значення прихованих полів — сервер мусить відмовити сам.
    await page.evaluate(({ ci, co }) => {
      (document.getElementById('f-check-in') as HTMLInputElement).value = ci;
      (document.getElementById('f-check-out') as HTMLInputElement).value = co;
    }, { ci: busy.from, co: busy.to });

    const post = page.waitForRequest((r) => r.method() === 'POST' && new URL(r.url()).pathname === `/book/${APT_A}`);
    await page.locator('#btn-book').click();
    const res = await (await post).response();
    expect(res?.status()).toBe(302);
    await page.waitForURL((u) => u.pathname === `/apartment/${APT_A}` && u.searchParams.get('book') === '1');

    await expect(page.locator('.alert-danger')).toHaveText(exactText(tr('dates_unavailable')));
    const params = new URL(page.url()).searchParams;
    expect(params.has('check_in') || params.has('check_out'), `зайняті дати повернулись в адресу: ${page.url()}`).toBe(false);
    expect(params.get('guests')).toBe('2');
    await expect(page.locator('#f-check-in')).toHaveValue('');
    await expect(page.locator('#f-check-out')).toHaveValue('');
    await expect(page.locator('#btn-book')).toBeDisabled();

    const sent = await sentBy(request, name);
    expect(sent).toHaveLength(1);
    expect(sent[0]).toMatchObject({ check_in: busy.from, check_out: busy.to });

    expect(jsErrors).toEqual([]);
  });

  test('19. «назад» з Монобанку: кнопка не зависла, сума не протухла', async ({ page, request, jsErrors }) => {
    skipWritesInLive('POST /book');
    // Хто показав сторінку після «назад»: bfcache (persisted=true) чи нове завантаження.
    // Init-скрипт на bfcache не перезапускається — масив переживає і отримує `true`.
    await page.addInitScript(() => {
      (window as any).__smokePageshow = [];
      addEventListener('pageshow', (e) => (window as any).__smokePageshow.push((e as PageTransitionEvent).persisted));
    });
    // Chromium сам каже, чому сторінку НЕ взято з bfcache (лише читаємо подію CDP)
    const notRestored: string[] = [];
    const cdp = await page.context().newCDPSession(page);
    await cdp.send('Page.enable');
    cdp.on('Page.backForwardCacheNotUsed', (e: any) =>
      notRestored.push(...(e.notRestoredExplanations ?? []).map((x: any) => `${x.type}:${x.reason}`)));

    const stay = await openAWithStay(page, request);
    const q = await priced(page, stay);
    const name = guestName('19');
    await page.locator('#w-name').fill(name);
    await page.locator('#w-phone').fill(GUEST_PHONE);
    await submitToMonobank(page);

    await page.goBack();
    await page.waitForURL((u) => u.pathname === `/apartment/${APT_A}`);
    await expect.poll(() => page.evaluate(() => ((window as any).__smokePageshow ?? []).length)).toBeGreaterThan(0);
    const shows: boolean[] = await page.evaluate(() => (window as any).__smokePageshow);
    const fromBfcache = shows[shows.length - 1] === true;
    // 11.09: під смокою bfcache НЕ спрацьовує (pageshow persisted=false). Playwright завжди
    // запускає Chromium з --disable-back-forward-cache, але й без нього (ignoreDefaultArgs, перевірено
    // зондом) headless shell відмовляє: BrowsingInstanceNotSwapped, BackForwardCacheDisabledForDelegate.
    // Тож гілку bfcache тут не виконати — тест перевіряє обидві чесно, під bfcache не підганяє.
    note('bfcache', fromBfcache
      ? `спрацював: pageshow ${JSON.stringify(shows)}`
      : `НЕ спрацював — сторінку завантажено наново (pageshow ${JSON.stringify(shows)}); ` +
        `Chromium: ${notRestored.length ? [...new Set(notRestored)].join(', ') : 'причину не повідомив'}`);

    const btn = page.locator('#btn-book');
    // обіцянка обробника pageshow (main.js): зняти «Створюємо платіж…» і стан submitting
    await expect(btn).not.toHaveClass(/\bis-loading\b/);
    await expect(page.locator('#btn-book-txt')).toHaveText(exactText(tr('proceed_to_payment')));

    if (fromBfcache) {
      // дати ті самі → на екрані те саме серверне котирування, кнопка знову робоча
      await expect(page.locator('#f-check-in')).toHaveValue(stay.ci);
      await expect(page.locator('#f-check-out')).toHaveValue(stay.co);
      await expect(page.locator('#pb-total')).toHaveText(uah(q.total));
      await expect(btn).toBeEnabled();
      await submitToMonobank(page); // повторне натискання не глушить захист від подвійного кліку
      expect(await sentBy(request, name)).toHaveLength(2);
    } else {
      // Нове завантаження: у скрипта нема дат (checkin/checkout = null), тож суми бути не може,
      // а кнопка чесно неактивна, доки гість не вибере дати. Жодної суми «з минулого».
      await expect(page.locator('#f-check-in')).toHaveValue('');
      expect(await shownTotal(page), 'після перезавантаження на екрані лишилась сума').toBe(0);
      await expect(page.locator('#booking-form')).not.toHaveClass(/\bis-priced\b/);
      await expect(btn).toBeDisabled();
      // і віджет знову робочий: дати → свіже котирування → кнопка активна
      await priced(page, stay);
    }

    expect(jsErrors).toEqual([]);
  });

  // Регресія находки 11.09. Раніше flatpickr.min.css підключався асинхронно (media="print" onload):
  // гість відкривав календар ДО того, як CSS застосувався → календар без стилів на всю ширину
  // (desktop 2730 px) → flatpickr 4.6.13 positionCalendar іде в гілку «centerMost» →
  // getDocumentStyleSheet() читає cssRules першої таблиці (Google Fonts, чужа, без crossorigin)
  // ДО свого try → SecurityError. 22.10 аркуш знову не блокує відмальовку, але вже свій
  // (/static/vendor/flatpickr-4.6.13.min.css), а першим аркушем документа стоїть інлайн-<style>;
  // main.js (whenFullCss) створює календарі лише після події fullcss — повні аркуші увімкнено, тож
  // календаря без стилів не існує. Тримаємо відповідь аркуша і перевіряємо саме це.
  // Лише читає (GET сторінки й CSS), тому йде й у live: там APT_A підміни нема (404) —
  // беремо першу квартиру каталогу головної, як група 2.
  test('календар не створюється до flatpickr.min.css і відкривається зі стилями, без JS-помилки', async ({ page, request, jsErrors }) => {
    let aptId = APT_A;
    if (IS_LIVE) {
      const html = await (await request.get('/')).text();
      aptId = /data-apt="([^"]+)"/.exec(html)?.[1] ?? '';
      expect(aptId, 'live: каталог головної порожній').not.toBe('');
    }
    let release!: () => void;
    const gate = new Promise<void>((r) => { release = r; });
    let held = false;
    await page.route((u) => u.pathname === '/static/vendor/flatpickr-4.6.13.min.css',
      async (route) => { held = true; await gate; await route.fallback(); });
    await page.goto(`/apartment/${aptId}`, { waitUntil: 'commit' });
    await expect.poll(() => held, 'flatpickr.min.css мав бути запитаний').toBe(true);
    await page.waitForTimeout(1_000); // «нетерплячий гість»: сторінка вже секунду без таблиці
    expect(await page.evaluate(() => !!(document.querySelector('#picker-checkin') as any)?._flatpickr),
      'flatpickr ініціалізувався раніше за свою таблицю стилів').toBe(false);
    release();
    await page.waitForLoadState('load');
    const input = page.locator('#picker-checkin');
    await input.click();
    await expect.poll(() => input.evaluate((el) => !!(el as any)._flatpickr?.isOpen)).toBe(true);
    const cal = await page.locator('.flatpickr-calendar.open').evaluate((el) =>
      ({ position: getComputedStyle(el).position, width: el.getBoundingClientRect().width, vw: innerWidth }));
    expect(cal.position, 'календар без стилів flatpickr').toBe('absolute');
    expect(cal.width, 'календар ширший за екран').toBeLessThanOrEqual(cal.vw);
    expect(jsErrors).toEqual([]);
  });

  test('сторінка /booking-confirmed: деталі броні й живе посилання «продовжити» (лише читаємо)', async ({ page, request, jsErrors }) => {
    skipWritesInLive('POST /book');
    const stay = freeStay(await availability(request, APT_A), 2)!;
    // бронь — прямим POST (не через віджет: його перевіряє 18); ref — з адреси оплати
    const resp = await page.request.post(`/book/${APT_A}`, {
      form: { check_in: stay.ci, check_out: stay.co, guests: '2', name: guestName('confirmed'), phone: GUEST_PHONE },
      maxRedirects: 0,
    });
    expect(resp.status()).toBe(302);
    const ref = new URL(resp.headers()['location']).searchParams.get('ref')!;
    const booking = (await fakeState(request)).bookings[ref];

    const r = await page.goto(`/booking-confirmed?ref=${encodeURIComponent(ref)}`);
    expect(r?.status()).toBe(200);
    await expect(page.getByRole('heading', { level: 1 })).toHaveText(exactText(tr('booking_confirmed')));
    const details = page.locator('.confirmed-details');
    await expect(details).toContainText(`#${ref}`);
    // редизайн 21.10: гостю — «22 жовтня 2026», машинна дата — у <time datetime>
    await expect(details.locator(`time[datetime="${stay.ci}"]`)).toHaveCount(1);
    await expect(details.locator(`time[datetime="${stay.co}"]`)).toHaveCount(1);
    await expect(details).toContainText(String(booking.total));

    const cont = page.getByRole('link', { name: tr('continue_to_docs') });
    await expect(cont).toBeVisible();
    await expect(cont).toHaveAttribute('href', `${BACKOFFICE_URL}/pay/${ref}`);
    await expect(page.getByRole('link', { name: tr('back_home') })).toHaveAttribute('href', '/');

    expect(jsErrors).toEqual([]);
  });
});
