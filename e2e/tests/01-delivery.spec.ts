/**
 * Група 1 «Доставка» — сценарії 1–5 з внутрішнього звіту сайту §5.3 (див. e2e/README.md), плюс /health.
 * Те, що ламається першим при переїзді з CloudFront на nginx/Caddy.
 *
 * 1–2 міряються лише на коробці (SMOKE_HOSTS); на localhost — skip.
 * 3 навмисно не привʼязаний до конкретного проксі: сьогодні кеш /static ставить Flask
 * (app.py static_cache_headers), на коробці — nginx `expires 7d` або Caddy.
 */
import type { APIResponse } from '@playwright/test';
import { test, expect } from './fixtures';
import { CANONICAL_HOST, HOSTS } from '../smoke-env';
import { tr } from './helpers';

const BOX_ONLY = 'меряється тільки на коробці: задай SMOKE_HOSTS (імена через кому)';

/** Усі значення заголовка (nginx `expires` + `add_header` дають ДВА Cache-Control). */
function headerAll(r: APIResponse, name: string): string {
  return r.headersArray().filter((h) => h.name.toLowerCase() === name).map((h) => h.value).join(', ');
}

function hostOf(url: string): string {
  return new URL(url).hostname;
}

test.describe('Група 1 · Доставка', () => {
  test('1. три імені хоста віддають сайт; канонічне імʼя одне', { tag: '@http' }, async ({ request }) => {
    test.skip(HOSTS.length === 0, BOX_ONLY);
    for (const host of HOSTS) {
      const r = await request.get(`https://${host}/`);
      expect(r.status(), `${host}: головна`).toBe(200);
      // редирект (якщо є) веде лише на те саме або канонічне імʼя
      expect([host, CANONICAL_HOST], `${host}: куди привів редирект`).toContain(hostOf(r.url()));
      const html = await r.text();
      expect(html, `${host}: каталог, не сторінка помилки`).toContain('data-apt=');
      expect(html, `${host}: сторінка помилки`).not.toContain('data-testid="error-page"');
      // канонічне посилання — завжди на SITE_URL, з якого б імені не прийшли
      const canonical = /<link rel="canonical" href="([^"]+)"/.exec(html)?.[1];
      expect(canonical, `${host}: <link rel=canonical>`).toBeTruthy();
      expect(hostOf(canonical!), `${host}: канонічне імʼя`).toBe(CANONICAL_HOST);
    }
  });

  test('2. HTTP → HTTPS і валідний сертифікат на кожному імені', { tag: '@http' }, async ({ request }) => {
    test.skip(HOSTS.length === 0, BOX_ONLY);
    for (const host of HOSTS) {
      const plain = await request.get(`http://${host}/`, { maxRedirects: 0 });
      expect([301, 302, 307, 308], `${host}: http:// має перенаправляти`).toContain(plain.status());
      const location = plain.headers()['location'] ?? '';
      expect(location, `${host}: Location`).toMatch(/^https:\/\//);
      expect([host, CANONICAL_HOST], `${host}: куди веде http→https`).toContain(hostOf(location));

      // ignoreHTTPSErrors не ввімкнено: невалідний/чужий сертифікат = виняток тут
      let secure: APIResponse;
      try {
        secure = await request.get(`https://${host}/health`, { maxRedirects: 0 });
      } catch (e) {
        throw new Error(`${host}: TLS не пройшов перевірку — ${(e as Error).message}`);
      }
      expect(secure.status(), `${host}: https /health`).toBe(200);
    }
  });

  for (const [file, type, marker] of [
    ['/static/styles.css', /^text\/css/, ':root'],
    ['/static/main.js', /javascript/, 'BLOCKED_DATES'],
  ] as const) {
    test(`3. ${file}: 200, свій тип і заголовок кешу`, { tag: '@http' }, async ({ request }) => {
      const r = await request.get(file);
      expect(r.status()).toBe(200);
      expect(r.headers()['content-type'] ?? '').toMatch(type);
      // найчастіша поломка переїзду — alias не збігся і на місці CSS/JS HTML-сторінка
      const body = await r.text();
      expect(body.trimStart().startsWith('<'), 'замість файлу віддано HTML').toBe(false);
      expect(body).toContain(marker);

      const cacheControl = headerAll(r, 'cache-control');
      expect(cacheControl, 'Cache-Control відсутній').not.toBe('');
      expect(cacheControl).not.toMatch(/no-store|no-cache|private/);
      const maxAge = Number(/(?:^|[,\s])max-age=(\d+)/.exec(cacheControl)?.[1] ?? 0);
      expect(maxAge, `max-age у "${cacheControl}"`).toBeGreaterThan(0);
    });
  }

  test('/health: 200, ok, коміт і Cache-Control: no-store', { tag: ['@http', '@critical'] }, async ({ request }) => {
    const r = await request.get('/health');
    expect(r.status()).toBe(200);
    expect(headerAll(r, 'cache-control')).toContain('no-store');
    const body = await r.json();
    expect(body.ok).toBe(true);
    // коробка/локально — завжди з комітом (штамп або git); порожньо буває лише на Lambda
    expect(body.git_sha).toMatch(/^[0-9a-f]{40}$/);
    test.info().annotations.push({ type: 'git_sha', description: `${body.git_short} ${body.git_subject}` });
  });

  test('4. головна: каталог, а не сторінка помилки; CSS застосовано; JS без помилок',
    { tag: '@critical' }, async ({ page, jsErrors }) => {
    const resp = await page.goto('/');
    expect(resp?.status()).toBe(200);
    await page.waitForLoadState('load');

    await expect(page.getByTestId('error-page')).toHaveCount(0);
    const cards = page.locator('a[data-apt]');
    await expect(cards.first()).toBeVisible();
    expect(await cards.count()).toBeGreaterThan(0);
    for (const href of await cards.evaluateAll((els) => els.map((e) => e.getAttribute('href')))) {
      expect(href).toMatch(/^\/apartment\/[^/?]+/);
    }

    // «біла сторінка без CSS»: токен теми з styles.css :root мусить існувати
    const clay = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--clay').trim());
    expect(clay, 'styles.css не застосувався (--clay порожній)').not.toBe('');

    expect(jsErrors).toEqual([]);
  });

  for (const id of ['00000000-0000-4000-8000-000000000000', 'smoke-no-such-apartment']) {
    test(`5. неіснуюча квартира ${id} → сторінка 404, не трейс`, async ({ page, jsErrors }) => {
      const resp = await page.goto(`/apartment/${id}`);
      expect(resp?.status()).toBe(404);

      const errorPage = page.getByTestId('error-page');
      await expect(errorPage).toBeVisible();
      await expect(errorPage).toHaveAttribute('data-error-code', '404');
      // редизайн 21.10: три шляхи назад; «На головну» — перший і веде на /
      await expect(errorPage.getByRole('link', { name: tr('back_home'), exact: true })).toHaveAttribute('href', '/');

      const text = await page.locator('body').innerText();
      expect(text).not.toMatch(/Traceback|Internal Server Error|werkzeug|jinja2/i);
      // консольне «Failed to load resource: 404» від самої сторінки — очікуване; рахуємо лише винятки
      expect(jsErrors.filter((e) => e.kind === 'pageerror')).toEqual([]);
    });
  }

  test('5б. невідомий шлях → та сама сторінка 404', async ({ page }) => {
    const resp = await page.goto('/smoke-no-such-page');
    expect(resp?.status()).toBe(404);
    await expect(page.getByTestId('error-page')).toHaveAttribute('data-error-code', '404');
  });
});
