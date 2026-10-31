/**
 * Спільні прийоми смоки для віджета бронювання (групи 2–4): дати, Flatpickr лише справжніми
 * кліками, очікування /api/quote, тексти з translations.py, стан підмінного бек-офісу.
 * Винесено з tests/02-calendar.spec.ts без змін поведінки.
 */
import { readFileSync } from 'node:fs';
import path from 'node:path';
import type { APIRequestContext, Locator, Page, Response } from '@playwright/test';
import { test, expect } from './fixtures';

// e2e/fake_backoffice.py
export const APT_A = '5a0e0000-0000-4000-8000-00000000000a';
export const APT_B = '5a0e0000-0000-4000-8000-00000000000b';

export type Range = { from: string; to: string };
export type Stay = { ci: string; co: string };
export type Quote = {
  nights: number; base_amount: number; tourist_tax: number; total: number; tax_per_night?: number;
  [k: string]: unknown;
};

// ---------------------------------------------------------------------------
// Дати — ISO-рядки в часовому поясі браузера (timezoneId у playwright.config.ts)
// ---------------------------------------------------------------------------

const TZ = 'Europe/Kyiv';
export const today = (): string => new Intl.DateTimeFormat('en-CA', { timeZone: TZ }).format(new Date());
export function addDays(iso: string, n: number): string {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d + n)).toISOString().slice(0, 10);
}
/** 0 = неділя … 5 = пʼятниця, 6 = субота. */
export const weekday = (iso: string) => new Date(`${iso}T00:00:00Z`).getUTCDay();
/** Ніч d зайнята (як у main.js: date >= from && date < to). */
export const nightBlocked = (ranges: Range[], d: string) => ranges.some((r) => d >= r.from && d < r.to);
export const stayBlocked = (ranges: Range[], s: Stay) => ranges.some((r) => s.ci < r.to && s.co > r.from);

/**
 * Перша вільна стоянка на `nights` ночей із заїздом не раніше `from` (за замовчуванням today+1),
 * у межах вікна /rates.
 */
export function freeStay(ranges: Range[], nights: number, from: string = addDays(today(), 1)): Stay | null {
  const start = Math.max(1, Math.round((Date.parse(from) - Date.parse(today())) / 86_400_000));
  for (let i = start; i < 50; i++) {
    const s = { ci: addDays(today(), i), co: addDays(today(), i + nights) };
    if (!stayBlocked(ranges, s)) return s;
  }
  return null;
}

/** Сума, як її друкує main.js (fmt_uah: toLocaleString('uk-UA') + ' грн'): роздільник груп — будь-який пробіл. */
export function uahPattern(n: number): string {
  return `${String(n).replace(/\B(?=(\d{3})+(?!\d))/g, '\\s?')}\\s*грн`;
}
export function uah(n: number): RegExp {
  return new RegExp(`^\\s*${uahPattern(n)}\\s*$`);
}
export const digits = (s: string | null) => Number((s ?? '').replace(/\D/g, ''));

/** Запис для доповіді: анотація в HTML-звіті + рядок у виводі прогону. */
export function note(type: string, description: string) {
  test.info().annotations.push({ type, description });
  console.log(`[${test.info().project.name}] ${type}: ${description}`);
}

// ---------------------------------------------------------------------------
// Тексти інтерфейсу — з translations.py (джерело правди), а не своя копія в тесті
// ---------------------------------------------------------------------------

let _translations: string | null = null;
/**
 * t(key) з translations.py для мови `lang`. Читаємо файл і беремо рядковий літерал у блоці
 * мови (формат файла: `"ua": {` … `"en": {` …, значення в подвійних лапках).
 */
export function tr(key: string, lang: 'ua' | 'en' = 'ua'): string {
  _translations ??= readFileSync(path.resolve(__dirname, '..', '..', 'translations.py'), 'utf8');
  const start = _translations.indexOf(`"${lang}": {`);
  expect(start, `translations.py: нема блоку "${lang}"`).toBeGreaterThanOrEqual(0);
  const re = new RegExp(`^\\s*"${key}":\\s*("(?:[^"\\\\]|\\\\.)*")`, 'm');
  const m = re.exec(_translations.slice(start));
  expect(m, `translations.py: нема ключа ${lang}.${key}`).not.toBeNull();
  return JSON.parse(m![1]);
}

/** Незалежна правда для локалі Flatpickr — не з flatpickr/l10n/uk.js, який ми й перевіряємо (групи 2, 5). */
export const UK_MONTHS = ['Січень', 'Лютий', 'Березень', 'Квітень', 'Травень', 'Червень',
  'Липень', 'Серпень', 'Вересень', 'Жовтень', 'Листопад', 'Грудень'];

/** Точний збіг тексту з перекладом: екрануємо спецсимволи регулярки. */
export const exactText = (s: string) => new RegExp(`^\\s*${s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\s*$`);

// ---------------------------------------------------------------------------
// Дані — через ті самі ручки сайту, що й браузер
// ---------------------------------------------------------------------------

export async function availability(request: APIRequestContext, id: string): Promise<Range[]> {
  const r = await request.get(`/api/availability/${id}`);
  expect(r.status(), `/api/availability/${id}`).toBe(200);
  return r.json();
}

/** Стан підмінного бек-офісу (лише fake): що сайт реально передав у /book, /coupon, /quote. */
export async function fakeState(request: APIRequestContext) {
  const r = await request.get('/__fake__/state');
  expect(r.status(), '/__fake__/state (є лише у fake)').toBe(200);
  return r.json();
}

/** Імʼя гостя, унікальне для тесту й проєкту: паралельні тести не плутають свої броні. */
export function guestName(tag: string): string {
  return `Смока ${tag} ${test.info().project.name} ${test.info().testId.slice(0, 8)}-${test.info().repeatEachIndex}`;
}

// ---------------------------------------------------------------------------
// Flatpickr — лише через справжні кліки гостя; інстанс читаємо, але не керуємо ним
// ---------------------------------------------------------------------------

/** Календар поля `input` відкритий (відкриває кліком, якщо ще ні). */
export async function calendarFor(page: Page, input: Locator): Promise<Locator> {
  const isOpen = () => input.evaluate((el) => !!(el as any)._flatpickr?.isOpen);
  if (!(await isOpen())) await input.click();
  await expect.poll(isOpen, 'календар не відкрився').toBe(true);
  const cal = page.locator('.flatpickr-calendar.open');
  await expect(cal).toHaveCount(1);
  return cal;
}

/** Клітинка дня `iso` у відкритому календарі; гортає вперед, поки день не зʼявиться. */
export async function dayCell(input: Locator, cal: Locator, iso: string): Promise<Locator> {
  const label = await input.evaluate((el, d) => {
    const fp = (el as any)._flatpickr;
    const [y, m, day] = d.split('-').map(Number);
    return fp.formatDate(new Date(y, m - 1, day), fp.config.ariaDateFormat) as string;
  }, iso);
  const cell = cal.locator(`.flatpickr-day:not(.prevMonthDay):not(.nextMonthDay)[aria-label="${label}"]`);
  for (let i = 0; i < 4 && (await cell.count()) === 0; i++) {
    await cal.locator('.flatpickr-next-month').click();
  }
  await expect(cell, `день ${iso} у календарі`).toHaveCount(1);
  return cell;
}

export async function pickCheckIn(page: Page, iso: string) {
  const input = page.locator('#picker-checkin');
  await (await dayCell(input, await calendarFor(page, input), iso)).click();
  await expect(page.locator('#f-check-in')).toHaveValue(iso);
}

export async function pickCheckOut(page: Page, iso: string) {
  const input = page.locator('#picker-checkout');
  await (await dayCell(input, await calendarFor(page, input), iso)).click();
  await expect(page.locator('#f-check-out')).toHaveValue(iso);
}

export async function pickStay(page: Page, s: Stay) {
  await pickCheckIn(page, s.ci);
  await pickCheckOut(page, s.co);
}

/**
 * Відповідь /api/quote саме на цю стоянку й цю кількість гостей. `couponPhone`: не задано —
 * будь-яка; рядок — лише запит із цим coupon_phone; null — лише запит БЕЗ coupon_phone.
 */
export function waitQuote(page: Page, id: string, s: Stay, guests: number, couponPhone?: string | null): Promise<Response> {
  return page.waitForResponse((r) => {
    const u = new URL(r.url());
    if (u.pathname !== `/api/quote/${id}` || u.searchParams.get('check_in') !== s.ci ||
      u.searchParams.get('check_out') !== s.co || u.searchParams.get('guests') !== String(guests)) return false;
    if (couponPhone === undefined) return true;
    return couponPhone === null ? !u.searchParams.has('coupon_phone') : u.searchParams.get('coupon_phone') === couponPhone;
  });
}

/** Віджет показує рівно серверне котирування `q` (не свою оцінку). */
export async function expectServerQuote(page: Page, q: Quote) {
  await expect(page.locator('#price-breakdown')).not.toHaveAttribute('aria-busy', 'true');
  await expect(page.locator('#pb-total')).toHaveText(uah(q.total));
  await expect(page.locator('#pb-nights-price')).toHaveText(uah(q.base_amount));
  await expect(page.locator('#pb-tax')).toHaveText(uah(q.tourist_tax));
  await expect(page.locator('#btn-book')).toBeEnabled();
}

/** Опційний рядок підсумку (#pb-coupon-row тощо) видно гостеві: без hidden і з .is-on. */
export async function expectOptRow(row: Locator, shown: boolean) {
  if (shown) {
    await expect(row).not.toHaveAttribute('hidden');
    await expect(row).toHaveClass(/\bis-on\b/);
  } else {
    // або ще ні разу не показаний (hidden), або згорнутий (без .is-on)
    await expect.poll(() => row.evaluate((el) => (el as HTMLElement).hidden || !el.classList.contains('is-on')),
      `${row}: рядок мав бути прихований`).toBe(true);
  }
}
