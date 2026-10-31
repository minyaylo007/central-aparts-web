/**
 * Своя статистика (analytics/, static/analytics.js): маяк /t іде, сторінка не ламається.
 * Смока вмикає ANALYTICS_DB у playwright.config.ts, тож скрипт і rrweb працюють у кожному сценарії;
 * тут — лише що пакет реально відправився і сервер відповів 204.
 */
import { test, expect } from './fixtures';
import { APT_A, availability, freeStay } from './helpers';

test.describe('Група 7 · Статистика', () => {
  test('маяк /t іде з головної, запис rrweb вантажиться, JS-помилок нема', { tag: '@critical' }, async ({ page, jsErrors }) => {
    const beacon = page.waitForResponse((r) => new URL(r.url()).pathname === '/t' && r.request().method() === 'POST');
    const rec = page.waitForRequest((r) => r.url().includes('/static/vendor/rrweb-record-'));
    await page.goto('/');
    await expect(page.locator('script[src*="analytics.js"]')).toHaveCount(1);
    await page.mouse.wheel(0, 900);
    await page.locator('body').click({ position: { x: 5, y: 5 } });
    await rec;
    // пакет іде сам за 4 с; чекаємо його, а не ховаємо сторінку
    const r = await beacon;
    expect(r.status(), 'відповідь /t').toBe(204);
    expect(r.request().postData() ?? '', 'у пакеті є перегляд сторінки').toContain('"k":"pv"');
    expect(jsErrors, 'JS-помилки сторінки').toEqual([]);
  });

  // 30.10: async-маяк вантажився після main.js — котировка з дат у URL ішла повз обгортку fetch,
  // і крок «price» губився. Тепер маяк синхронний і раніше за main.js.
  test('прихід на квартиру з датами в URL дає крок price', { tag: '@critical' }, async ({ page, request, jsErrors }) => {
    const s = freeStay(await availability(request, APT_A), 2);
    test.skip(!s, 'нема двох вільних ночей у фейку');
    // повільна мережа: маяк приходить на 1,5 с пізніше — main.js однаково мусить чекати на нього
    await page.route(/\/static\/analytics\.js/, async (r) => { await new Promise((ok) => setTimeout(ok, 1500)); await r.continue(); });
    const price = page.waitForRequest((r) => new URL(r.url()).pathname === '/t' && r.method() === 'POST'
      && (r.postData() ?? '').includes('"st":"price"'));
    await page.goto(`/apartment/${APT_A}?check_in=${s!.ci}&check_out=${s!.co}`);
    await price;
    expect(jsErrors, 'JS-помилки сторінки').toEqual([]);
  });

  // 30.10: посилання, після якого сторінка пішла (pagehide) пізніше 1 с, — не мертвий клік.
  // Окрема сторінка через route: на головній слайдер сам рухає DOM і сховав би dd будь-де.
  test('повільне посилання — не мертвий клік, кнопка без реакції — мертвий', async ({ page }) => {
    const html = (body: string) => `<!doctype html><html><body style="margin:0;height:900px">${body}`
      + '<script src="/static/analytics.js"></script></body></html>';
    await page.route(/\/__dead$/, (r) => r.fulfill({ contentType: 'text/html', body: html(
      '<button id="mute" style="margin:40px">нічого</button><a id="slow" href="/__slow" style="margin:40px">далі</a>') }));
    await page.route(/\/__slow$/, async (r) => {
      // 6 с: старий код слав би dd звичайним пакетом за 4 с, ще до відходу (маяк у pagehide смока не бачить)
      await new Promise((ok) => setTimeout(ok, 6000));
      await r.fulfill({ contentType: 'text/html', body: html('<p>прийшли</p>') });
    });
    const sent: string[] = [];
    page.on('request', (r) => { if (new URL(r.url()).pathname === '/t') sent.push(r.postData() ?? ''); });
    await page.goto('/__dead');
    // уміє сказати «так»: кнопка без реакції за 1 с — dd, пакет за ≤ 4 с
    const dd = page.waitForRequest((r) => new URL(r.url()).pathname === '/t' && (r.postData() ?? '').includes('"k":"dd"'));
    await page.locator('#mute').click();
    await dd;
    const before = sent.join('').split('"k":"dd"').length;
    await page.locator('#slow').click();
    await page.waitForURL(/\/__slow$/);
    await page.waitForTimeout(500);
    expect(sent.join('').split('"k":"dd"').length, 'посилання з навігацією через 6 с — без dd').toBe(before);
  });

  // 30.10, правило dead 2 (static/analytics.js, ACT): dd — лише по тому, що обіцяє дію (кнопка, день
  // календаря, cursor:pointer); тап по декору без реакції — dx, у мертві не йде.
  test('мертвий клік лише по цілі, що обіцяє дію; декор — dx', async ({ page }) => {
    await page.route(/\/__decor$/, (r) => r.fulfill({ contentType: 'text/html', body: '<!doctype html><html>'
      + '<body style="margin:0;height:900px"><p id="decor" style="margin:40px">просто текст</p>'
      + '<span class="flatpickr-day" id="day" style="display:inline-block;margin:40px">5</span>'
      + '<span id="hand" style="cursor:pointer;display:inline-block;margin:40px">рука</span>'
      + '<script src="/static/analytics.js"></script></body></html>' }));
    const events: any[] = [];
    page.on('request', (r) => {
      if (new URL(r.url()).pathname !== '/t') return;
      try { events.push(...(JSON.parse(r.postData() ?? '{}').e ?? [])); } catch { /* шматок запису rrweb */ }
    });
    const rec = page.waitForRequest((r) => r.url().includes('/static/vendor/rrweb-record-'));
    await page.goto('/__decor');
    await rec;                          // rrweb на старті сам чіпає DOM — клікаємо після нього
    await page.waitForTimeout(1000);
    for (const id of ['#decor', '#day', '#hand']) {
      await page.locator(id).click();
      await page.waitForTimeout(1200);   // окремі вікна сторожа на кожен клік
    }
    const got = () => events.filter((e) => e.k === 'dd' || e.k === 'dx').map((e) => `${e.k} ${e.s} ${e.dv}`).sort();
    await expect.poll(got, { timeout: 8000 }).toEqual(['dd span#day 2', 'dd span#hand 2', 'dx p#decor 2']);
  });

  test.describe('палець', () => {
    test.use({ hasTouch: true, isMobile: true, viewport: { width: 390, height: 844 } });
    // 30.10, гіпотеза (б) з розбору (14): Flatpickr відкривається поверх поля ще до click, тож click
    // приходить спільному предку — BODY, а DOM уже змінився, і сторож писав dd «html>body».
    // Ціль тепер — від pointerdown.
    test('тап по полю дати hero — клік по полю, а не по body, і не мертвий', async ({ page, jsErrors }) => {
      const events: any[] = [];
      page.on('request', (r) => {
        if (new URL(r.url()).pathname !== '/t') return;
        try { events.push(...(JSON.parse(r.postData() ?? '{}').e ?? [])); } catch { /* шматок запису rrweb */ }
      });
      await page.goto('/');
      await page.locator('#hs-checkin').tap();
      await expect(page.locator('.flatpickr-calendar.open')).toHaveCount(1);
      await expect.poll(() => events.filter((e) => e.k === 'c').length, { timeout: 8000 }).toBeGreaterThan(0);
      await page.waitForTimeout(1500);   // сторож мертвого кліку — 1 с
      await page.evaluate(() => window.dispatchEvent(new Event('pagehide')));
      await expect.poll(() => events.filter((e) => e.k === 'sc').length, { timeout: 4000 }).toBeGreaterThan(0);
      expect(events.filter((e) => e.k === 'c').map((e) => e.s)).toEqual(['input#hs-checkin']);
      expect(events.filter((e) => e.k === 'dd' || e.k === 'dx'), 'тап по полю, що відкрило календар, — не мертвий').toEqual([]);
      expect(jsErrors).toEqual([]);
    });
  });
});
