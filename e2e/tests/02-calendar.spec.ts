/**
 * Група 2 «Календар і котирування» — сценарії 6–11 з внутрішнього звіту сайту §5.3 (див. e2e/README.md),
 * плюс бейджі «вільна/зайнята» hero-пошуку на головній. Серце static/main.js:
 * два Flatpickr, /api/availability, /api/quote, /api/rates.
 *
 * Дати й суми тут НЕ хардкодяться і формула котирування НЕ повторюється: зайнятість і ціни
 * беремо з тих самих ручок, що й браузер, суму — з перехопленої відповіді /api/quote.
 * Тому сценарії 6–11 ідуть і в live (там вони лише читають: GET availability/quote/rates,
 * без купонного телефону). Конкретні дані підміни (збір 37, пт/сб +300) — лише у fake.
 *
 * ВІДОМА НАХОДКА, НЕ ЧИНИТИ (рішення власника): static/main.js рахує миттєву оцінку до
 * відповіді сервера зі збором 48 (TOURIST_TAX, TOURIST_TAX_PER_GUEST_NIGHT). Сценарій 10
 * перевіряє ПІДСУМОК після відповіді, а що гість бачить до неї — лише записує в
 * анотації (`guest_sees_before_server`, `quote_ms`), тест від цього не падає.
 */
import type { APIRequestContext, Page, Response } from '@playwright/test';
import { test, expect } from './fixtures';
import { IS_LIVE } from '../smoke-env';
import {
  APT_A, APT_B, addDays, availability, calendarFor, dayCell, digits, expectServerQuote, freeStay,
  nightBlocked, note, pickCheckIn, pickCheckOut, pickStay, today, uah, UK_MONTHS, waitQuote, weekday,
  type Quote, type Range, type Stay,
} from './helpers';

// e2e/fake_backoffice.py
const FAKE_TAX_PER_GUEST_NIGHT = 37;
const FAKE_WEEKEND_SURCHARGE = 300;
/** Збір, яким static/main.js рахує оцінку до відповіді сервера (відома находка). */
const JS_TAX = 48;

// Незалежна правда для локалі (UK_MONTHS — у ./helpers.ts)
const UK_WEEKDAYS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Нд'];
const EN_MONTHS = /January|February|March|April|May|June|July|August|September|October|November|December/;

// ---------------------------------------------------------------------------
// Дані — через ті самі ручки сайту, що й браузер
// ---------------------------------------------------------------------------

/** fake — квартира A; live — усі квартири каталогу головної (порядок як на сторінці). */
async function apartments(request: APIRequestContext): Promise<string[]> {
  if (!IS_LIVE) return [APT_A];
  const html = await (await request.get('/')).text();
  const ids = [...new Set([...html.matchAll(/data-apt="([^"]+)"/g)].map((m) => m[1]))];
  expect(ids.length, 'live: каталог головної порожній').toBeGreaterThan(0);
  return ids;
}

/** Квартира з вільною стоянкою на `nights` ночей — перша придатна з каталогу. */
async function apartmentWithFreeStay(request: APIRequestContext, nights: number) {
  for (const id of await apartments(request)) {
    const stay = freeStay(await availability(request, id), nights);
    if (stay) return { id, stay };
  }
  return null;
}

// Flatpickr (calendarFor, dayCell, pick*), waitQuote, expectServerQuote — у ./helpers.ts

async function quoteJson(pending: Promise<Response>): Promise<Quote> {
  const r = await pending;
  expect(r.status(), `статус ${r.url()}`).toBe(200);
  // купонний телефон у котирування тут не потрапляє ніколи (у live — тим паче)
  expect(new URL(r.url()).searchParams.has('coupon_phone')).toBe(false);
  const q = await r.json();
  expect(typeof q.total, 'форма відповіді /api/quote').toBe('number');
  return q;
}

async function quoteMs(page: Page, id: string): Promise<number | null> {
  return page.evaluate((apt) => {
    const e = performance.getEntriesByType('resource').filter((x) => x.name.includes(`/api/quote/${apt}`)).pop();
    return e ? Math.round(e.duration) : null;
  }, id);
}

// ---------------------------------------------------------------------------

test.describe('Група 2 · Календар і котирування', { tag: '@critical' }, () => {
  test('6. Flatpickr піднявся; локаль українська (сесія ua → Flatpickr uk)', async ({ page, request, jsErrors }) => {
    const [id] = await apartments(request);
    await page.goto(`/apartment/${id}`);

    // пастка: сесія зберігає 'ua', а ключ локалі Flatpickr — 'uk'
    expect(await page.evaluate(() => (window as any).LANG)).toBe('ua');
    for (const sel of ['#picker-checkin', '#picker-checkout']) {
      const input = page.locator(sel);
      await expect(input, `${sel}: Flatpickr не ініціалізувався`).toHaveClass(/flatpickr-input/);
      const l10n = await input.evaluate((el) => {
        const fp = (el as any)._flatpickr;
        return { months: fp.l10n.months.longhand, firstDayOfWeek: fp.l10n.firstDayOfWeek };
      });
      expect(l10n, `${sel}: локаль Flatpickr`).toEqual({ months: UK_MONTHS, firstDayOfWeek: 1 });
    }

    // що бачить гість: місяць і дні тижня українською, тиждень з понеділка
    const cal = await calendarFor(page, page.locator('#picker-checkin'));
    const month = await cal.locator('.flatpickr-month').first().evaluate((m) => {
      const sel = m.querySelector('select.flatpickr-monthDropdown-months') as HTMLSelectElement | null;
      return (sel ? sel.selectedOptions[0]?.textContent : m.querySelector('.cur-month')?.textContent)?.trim();
    });
    expect(month).toBe(UK_MONTHS[Number(today().slice(5, 7)) - 1]);
    const weekdays = await cal.locator('.flatpickr-weekdaycontainer').first().locator('.flatpickr-weekday').allInnerTexts();
    expect(weekdays.map((w) => w.trim())).toEqual(UK_WEEKDAYS);
    const labels = await cal.locator('.flatpickr-day').evaluateAll((els) => els.map((e) => e.getAttribute('aria-label') ?? '').join(' '));
    expect(labels).not.toMatch(EN_MONTHS);

    expect(jsErrors).toEqual([]);
  });

  test('7. зайняті дати задизейблені й не вибираються (дані /api/availability)', async ({ page, request, jsErrors }) => {
    // квартира з зайнятим відрізком, до якого є вільна ніч перед ним
    let target: { id: string; ranges: Range[]; range: Range } | null = null;
    for (const id of await apartments(request)) {
      const ranges = await availability(request, id);
      const range = ranges.find((r) => r.from >= addDays(today(), 2) && r.from <= addDays(today(), 50) &&
        !nightBlocked(ranges, addDays(r.from, -1)));
      if (range) { target = { id, ranges, range }; break; }
    }
    test.skip(!target, 'live: у каталозі нема зайнятих ночей у найближчі 50 днів — нема що перевіряти');
    const { id, ranges, range } = target!;
    if (!IS_LIVE) expect(ranges, 'fake: у A два зайняті відрізки').toHaveLength(2);

    await page.goto(`/apartment/${id}`);
    // сторінка й календар тримають ту саму зайнятість, що віддає /api/availability
    expect(await page.evaluate(() => (window as any).BLOCKED_DATES)).toEqual(ranges);

    const inCI = page.locator('#picker-checkin');
    const fCI = page.locator('#f-check-in');
    let cal = await calendarFor(page, inCI);
    const before = addDays(range.from, -1);
    await expect(await dayCell(inCI, cal, before), `${before} вільна`).not.toHaveClass(/flatpickr-disabled/);
    for (let d = range.from; d < range.to; d = addDays(d, 1)) {
      const cell = await dayCell(inCI, cal, d);
      await expect(cell, `${d} зайнята → disabled`).toHaveClass(/flatpickr-disabled/);
      if (d === range.from) {
        await cell.click({ force: true }); // force: клітинка disabled, клік усе одно справжній
        await expect(fCI, 'зайнята дата вибралась як заїзд').toHaveValue('');
        expect(await inCI.evaluate((el) => (el as any)._flatpickr.isOpen), 'календар мав лишитись відкритим').toBe(true);
      }
    }
    if (!nightBlocked(ranges, range.to)) {
      // день виїзду попереднього гостя вільний для заїзду
      await expect(await dayCell(inCI, cal, range.to), `${range.to} (виїзд) вільна`).not.toHaveClass(/flatpickr-disabled/);
    }

    // виїзд: не можна перестрибнути зайняту ніч; виїхати в день її початку — можна
    await pickCheckIn(page, before);
    const inCO = page.locator('#picker-checkout');
    cal = await calendarFor(page, inCO);
    const across = addDays(range.from, 1);
    const acrossCell = await dayCell(inCO, cal, across);
    await expect(acrossCell, `виїзд ${across} перетинає зайняту ніч`).toHaveClass(/flatpickr-disabled/);
    await acrossCell.click({ force: true });
    await expect(page.locator('#f-check-out')).toHaveValue('');
    await expect(await dayCell(inCO, cal, range.from), `виїзд ${range.from}`).not.toHaveClass(/flatpickr-disabled/);
    const quote = waitQuote(page, id, { ci: before, co: range.from }, 2);
    await pickCheckOut(page, range.from);
    await expectServerQuote(page, await quoteJson(quote));

    expect(jsErrors).toEqual([]);
  });

  test('8. вибір дат → /api/quote; у віджеті сума від сервера', async ({ page, request, jsErrors }) => {
    const found = await apartmentWithFreeStay(request, 2);
    test.skip(!found, 'live: нема вільних 2 ночей у найближчі 50 днів');
    const { id, stay } = found!;

    await page.goto(`/apartment/${id}`);
    await expect(page.locator('#btn-book')).toBeDisabled();
    const quote = waitQuote(page, id, stay, 2);
    await pickStay(page, stay);
    const q = await quoteJson(quote);
    expect(q.nights).toBe(2);
    await expectServerQuote(page, q);
    // екранний диктор озвучує ту саму серверну суму
    await expect(page.locator('#pb-announce')).toHaveText(new RegExp(uah(q.total).source.replace(/^\^\\s\*/, '')));

    note('quote_ms', `${await quoteMs(page, id)} мс — /api/quote ${stay.ci}→${stay.co}, 2 гостей`);
    expect(jsErrors).toEqual([]);
  });

  test('9. лічильник гостей тримає межі 1..4; зміна гостей перезапитує котирування', async ({ page, request, jsErrors }) => {
    const found = await apartmentWithFreeStay(request, 2);
    test.skip(!found, 'live: нема вільних 2 ночей у найближчі 50 днів');
    const { id, stay } = found!;

    await page.goto(`/apartment/${id}`);
    const minus = page.locator('#guest-minus');
    const plus = page.locator('#guest-plus');
    const shown = page.locator('#guest-display');
    const field = page.locator('#f-guests'); // це значення їде у форму /book
    await expect(shown).toHaveText('2');

    await minus.click();
    await expect(shown).toHaveText('1');
    await expect(field).toHaveValue('1');
    await expect(minus).toBeDisabled();
    await minus.dispatchEvent('click'); // навіть обійшовши disabled — нижче 1 не йде
    await expect(shown).toHaveText('1');

    for (const n of ['2', '3', '4']) {
      await plus.click();
      await expect(shown).toHaveText(n);
    }
    await expect(field).toHaveValue('4');
    await expect(plus).toBeDisabled();
    await plus.dispatchEvent('click');
    await expect(shown).toHaveText('4');
    await expect(field).toHaveValue('4');

    // дати при 4 гостях → котирування з guests=4
    const q4 = waitQuote(page, id, stay, 4);
    await pickStay(page, stay);
    await expectServerQuote(page, await quoteJson(q4));

    // та сама стоянка, менше гостей → новий запит із новим guests, на екрані — його сума
    const q2 = waitQuote(page, id, stay, 2);
    await minus.click();
    await minus.click();
    await expect(field).toHaveValue('2');
    const quote2 = await quoteJson(q2);
    await expectServerQuote(page, quote2);

    expect(jsErrors).toEqual([]);
  });

  test('10. туристичний збір у підсумку = серверний tourist_tax', async ({ page, request, jsErrors }) => {
    const found = await apartmentWithFreeStay(request, 3);
    test.skip(!found, 'live: нема вільних 3 ночей у найближчі 50 днів');
    const { id, stay } = found!;
    const guests = 3;

    await page.goto(`/apartment/${id}`);
    await page.locator('#guest-plus').click();
    await expect(page.locator('#guest-display')).toHaveText(String(guests));

    // Притримуємо відповідь сервера, щоб без гонки прочитати, що гість бачить ДО неї.
    let release!: () => void;
    const gate = new Promise<void>((r) => { release = r; });
    await page.route((u) => u.pathname === `/api/quote/${id}`, async (route) => { await gate; await route.fallback(); });

    const quote = waitQuote(page, id, stay, guests);
    await pickStay(page, stay);
    const beforeTax = digits(await page.locator('#pb-tax').textContent());
    const beforeTotal = digits(await page.locator('#pb-total').textContent());
    release();
    const q = await quoteJson(quote);
    await expectServerQuote(page, q);
    await page.unrouteAll({ behavior: 'wait' });

    // збір — рівно серверний; якби JS рахував сам (48), тут було б інше число
    const shownTax = digits(await page.locator('#pb-tax').textContent());
    expect(shownTax).toBe(q.tourist_tax);
    expect(digits(await page.locator('#pb-total').textContent())).toBe(q.total);
    if (typeof q.tax_per_night === 'number') {
      expect(q.tourist_tax, 'сервер: tourist_tax = nights × guests × tax_per_night').toBe(q.nights * guests * q.tax_per_night);
      if (q.tax_per_night !== JS_TAX) expect(shownTax).not.toBe(q.nights * guests * JS_TAX);
    }
    if (!IS_LIVE) expect(q.tax_per_night, 'fake: збір підміни').toBe(FAKE_TAX_PER_GUEST_NIGHT);

    // Відома находка — лише фіксуємо, тест від неї не падає
    note('guest_sees_before_server',
      `збір ${beforeTax} грн (= ${q.nights}×${guests}×${JS_TAX}: ${beforeTax === q.nights * guests * JS_TAX ? 'так' : 'ні'}), ` +
      `разом ${beforeTotal} грн → після відповіді збір ${q.tourist_tax}, разом ${q.total} (tax_per_night=${q.tax_per_night})`);

    // зміна гостей на тій самій стоянці: миттєвий перерахунок бере tax_per_night сервера, не 48
    const q2p = waitQuote(page, id, stay, guests - 1);
    await page.locator('#guest-minus').click();
    if (typeof q.tax_per_night === 'number') {
      await expect(page.locator('#pb-tax')).toHaveText(uah(q.nights * (guests - 1) * q.tax_per_night));
    }
    const q2 = await quoteJson(q2p);
    await expectServerQuote(page, q2);
    note('quote_ms', `${await quoteMs(page, id)} мс — стільки гість бачить оцінку до серверної суми (останній запит)`);

    expect(jsErrors).toEqual([]);
  });

  test('11. ціни за ніч у календарі — з /api/rates', async ({ page, request, jsErrors }) => {
    const [id] = await apartments(request);
    const ratesResp = page.waitForResponse((r) => new URL(r.url()).pathname === `/api/rates/${id}`);
    await page.goto(`/apartment/${id}`);
    const r = await ratesResp;
    expect(r.status()).toBe(200);
    const rates: Record<string, number> = (await r.json()).rates;
    expect(Object.keys(rates).length, '/api/rates порожній').toBeGreaterThan(0);

    const input = page.locator('#picker-checkin');
    const cal = await calendarFor(page, input);
    await expect(cal, 'JS не позначив календар цінами').toHaveClass(/fp-has-prices/);

    // що надруковано в клітинках: поточні місяці + ще один вперед
    const seen = new Map<string, { price: string | null; disabled: boolean }>();
    const collect = async () => {
      const cells = await cal.locator('.flatpickr-day:not(.prevMonthDay):not(.nextMonthDay)').evaluateAll((els) =>
        els.map((el) => {
          const d = (el as any).dateObj as Date;
          const iso = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
          return { iso, price: el.querySelector('.fp-day-price')?.textContent ?? null, disabled: el.classList.contains('flatpickr-disabled') };
        }));
      for (const c of cells) seen.set(c.iso, { price: c.price, disabled: c.disabled });
    };
    await collect();
    await cal.locator('.flatpickr-next-month').click();
    await collect();

    let priced = 0;
    for (const [iso, c] of seen) {
      const rate = rates[iso];
      if (c.disabled || typeof rate !== 'number' || rate <= 0) {
        expect(c.price, `${iso}: ціни бути не мало`).toBeNull();
        continue;
      }
      expect(c.price, `${iso}: ціна з /api/rates`).not.toBeNull();
      expect(digits(c.price), `${iso}: ціна в клітинці`).toBe(rate);
      priced++;
    }
    expect(priced, 'жодної ціни в календарі').toBeGreaterThan(0);

    if (!IS_LIVE) {
      // fake: пт/сб дорожчі за будні рівно на WEEKEND_SURCHARGE
      const byKind = { weekend: new Set<number>(), weekday: new Set<number>() };
      for (const [iso, c] of seen) {
        if (c.price === null) continue;
        byKind[[5, 6].includes(weekday(iso)) ? 'weekend' : 'weekday'].add(digits(c.price));
      }
      expect([...byKind.weekday]).toEqual([2200]);
      expect([...byKind.weekend]).toEqual([2200 + FAKE_WEEKEND_SURCHARGE]);
    }

    expect(jsErrors).toEqual([]);
  });

  test('головна: hero-пошук за датами — бейджі вільна/зайнята за /api/availability', async ({ page, request, jsErrors }) => {
    test.skip(IS_LIVE, 'live: потрібен перетин зайнятостей A і B — є лише в даних підміни');
    const [a, b] = [(await availability(request, APT_A))[0], (await availability(request, APT_B))[0]];
    const row = (id: string) => page.locator('.apt-row').filter({ has: page.locator(`a[data-apt="${id}"]`) });
    const badge = (id: string) => row(id).locator('[data-apt-avail]');
    const form = page.locator('#heroSearch');
    const pickHero = async (sel: string, iso: string) => {
      const input = page.locator(sel);
      await (await dayCell(input, await calendarFor(page, input), iso)).click();
      // 22.10: у полі — «пт, 23 жовт.», як у віджеті квартири; ISO живе в календарі (і йде в URL нижче)
      await expect.poll(() => input.evaluate((el) => {
        const d = (el as any)._flatpickr.selectedDates[0];
        return d && [d.getFullYear(), d.getMonth() + 1, d.getDate()].map((n) => String(n).padStart(2, '0')).join('-');
      })).toBe(iso);
      const [y, m, d] = iso.split('-').map(Number);
      const human = await page.evaluate(([y, m, d]) => new Date(y, m - 1, d)
        .toLocaleDateString('uk-UA', { weekday: 'short', day: 'numeric', month: 'short' }), [y, m, d]);
      expect(human).toMatch(/^\p{L}+, \d{1,2} \p{L}+\.?$/u);
      await expect(input).toHaveValue(human);
    };

    // стоянка, що зачіпає зайняте в ОБОХ → обидва бейджі «зайнята», чесна примітка, лишаємось на головній
    const both: Stay = { ci: a.from < b.from ? a.from : b.from, co: a.to > b.to ? a.to : b.to };
    await page.goto('/');
    await pickHero('#hs-checkin', both.ci);
    await pickHero('#hs-checkout', both.co);
    await form.locator('.hsearch-submit').click();
    for (const id of [APT_A, APT_B]) {
      await expect(badge(id)).toBeVisible();
      await expect(badge(id)).toHaveClass(/is-busy/);
      await expect(badge(id).locator('.apt-avail-text')).toHaveText((await badge(id).getAttribute('data-busy'))!);
      // toBeDisabled() на <option> не спрацьовує — дивимось властивість і позначку «— зайнято»
      const option = page.locator(`#hs-apt option[value="${id}"]`);
      await expect(option).toHaveJSProperty('disabled', true);
      await expect(option).toHaveText(new RegExp(`— ${await page.locator('#hs-apt').getAttribute('data-busy-word')}$`));
    }
    await expect(page.locator('#availSummaryNone')).toBeVisible();
    expect(new URL(page.url()).pathname).toBe('/');

    // зайнята лише A → пошук веде на вільну B з датами в адресі
    const onlyA: Stay = { ci: a.from, co: addDays(a.from, 1) };
    await page.goto('/');
    await pickHero('#hs-checkin', onlyA.ci);
    await pickHero('#hs-checkout', onlyA.co);
    await form.locator('.hsearch-submit').click();
    await page.waitForURL((u) => u.pathname === `/apartment/${APT_B}`);
    const params = new URL(page.url()).searchParams;
    expect([params.get('check_in'), params.get('check_out'), params.get('guests')]).toEqual([onlyA.ci, onlyA.co, '2']);

    expect(jsErrors).toEqual([]);
  });
});
