/**
 * Група 5 «Мова, мобільний, галерея» — сценарії 20–22 з внутрішнього звіту сайту §5.3 (див. e2e/README.md).
 *
 * Усі три лише читають (GET сторінок, /lang/<code> кладе мову в cookie сесії, котирування — GET),
 * тому йдуть і в live. Що залежить від кількості фото, береться з того, що сервер реально віддав
 * (слайди галереї в HTML), а не з даних підміни; квартири без потрібних фото в live — skip з причиною.
 *
 * Тексти — з translations.py (`tr`), а не своя копія; aria-label кнопок лайтбокса теж
 * (static/main.js бере їх з LIGHTBOX_TXT у public_apartment.html).
 */
import type { APIRequestContext, Locator, Page } from '@playwright/test';
import { test, expect } from './fixtures';
import { IS_LIVE } from '../smoke-env';
import {
  APT_A, APT_B, addDays, availability, calendarFor, dayCell, digits, exactText, expectServerQuote, freeStay, note, pickStay, today, tr,
  waitQuote, UK_MONTHS,
} from './helpers';

const EN_MONTHS = ['January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December'];

// static/main.js initLightbox(): підписи — з translations.py (LIGHTBOX_TXT)
const LB_CLOSE = tr('lightbox_close');
const LB_PREV = tr('lightbox_prev');
const LB_NEXT = tr('lightbox_next');

const onlyProject = (name: 'desktop' | 'mobile', why: string) =>
  test.skip(test.info().project.name !== name, `лише проєкт ${name}: ${why}`);

// ---------------------------------------------------------------------------
// Дані — з того, що сервер реально віддав
// ---------------------------------------------------------------------------

/** id квартир каталогу головної (порядок як на сторінці). */
async function catalogIds(request: APIRequestContext): Promise<string[]> {
  const html = await (await request.get('/')).text();
  const ids = [...new Set([...html.matchAll(/data-apt="([^"]+)"/g)].map((m) => m[1]))];
  expect(ids.length, 'каталог головної порожній').toBeGreaterThan(0);
  return ids;
}

/** Перша квартира каталогу, чия сторінка має щонайменше `min` фото (слайдів галереї в HTML). */
async function apartmentWithPhotos(request: APIRequestContext, min: number): Promise<string | null> {
  for (const id of await catalogIds(request)) {
    const html = await (await request.get(`/apartment/${id}`)).text();
    if ((html.match(/class="swiper-slide apt-hero-slide"/g) ?? []).length >= min) return id;
  }
  return null;
}

/** Фото галереї в порядку сервера (data-index; swiper у loop переставляє слайди в DOM). */
function galleryPhotos(page: Page): Promise<string[]> {
  return page.locator('.apt-hero-swiper .gallery-photo').evaluateAll((els) => els
    .map((e) => ({ i: Number(e.getAttribute('data-index')), src: e.getAttribute('src') ?? '' }))
    .sort((a, b) => a.i - b.i)
    .map((p) => p.src));
}

// ---------------------------------------------------------------------------

test.describe('Група 5 · Мова, мобільний, галерея', () => {
  test('20. мова: /lang/en → англійська, тримається в сесії; /lang/ua → назад; Flatpickr en/uk', async ({ page, request, jsErrors }) => {
    const [id] = await catalogIds(request);
    // перемикач: на мобільному .lang-mobile-btn, на десктопі — у навігації; видно рівно один
    const langLink = (code: 'ua' | 'en') => page.locator(`a[href="/lang/${code}"]`).filter({ visible: true });
    const aptHeading = (lang: 'ua' | 'en') =>
      page.getByRole('heading', { name: tr('apartments_section_title', lang), exact: true });

    const expectHome = async (lang: 'ua' | 'en') => {
      await page.waitForURL((u) => u.pathname === '/');
      await expect(page.locator('html')).toHaveAttribute('lang', lang === 'ua' ? 'uk' : 'en');
      await expect(aptHeading(lang)).toBeVisible();
      await expect(page.locator('.hsearch-submit')).toHaveText(exactText(tr('book_now', lang)));
      await expect(langLink(lang === 'ua' ? 'en' : 'ua'), 'перемикач пропонує іншу мову').toHaveCount(1);
      // hero-пошук: локаль Flatpickr — з мови сторінки (html lang)
      const months = await page.locator('#hs-checkin').evaluate((el) => (el as any)._flatpickr.l10n.months.longhand);
      expect(months, 'Flatpickr hero-пошуку').toEqual(lang === 'ua' ? UK_MONTHS : EN_MONTHS);
    };

    const expectApartment = async (lang: 'ua' | 'en') => {
      await page.waitForURL((u) => u.pathname === `/apartment/${id}`);
      await expect(page.locator('html')).toHaveAttribute('lang', lang === 'ua' ? 'uk' : 'en');
      // пастка ua → uk: сесія й LANG — 'ua', а локаль Flatpickr — 'uk'
      expect(await page.evaluate(() => (window as any).LANG)).toBe(lang);
      await expect(page.locator('a.btn-back')).toHaveText(exactText(tr('all_apartments', lang)));
      await expect(page.locator('label[for="picker-checkin"]')).toHaveText(exactText(tr('check_in', lang)));
      await expect(page.locator('#widget-hint')).toHaveText(exactText(tr('select_dates_first', lang)));
      expect(await page.evaluate(() => !!(window as any).flatpickr?.l10ns?.uk), 'uk.js підключено лише для ua').toBe(lang === 'ua');

      // що бачить гість у календарі
      const input = page.locator('#picker-checkin');
      const l10n = await input.evaluate((el) => {
        const fp = (el as any)._flatpickr;
        return { months: fp.l10n.months.longhand, firstDayOfWeek: fp.l10n.firstDayOfWeek };
      });
      expect(l10n, 'локаль Flatpickr').toEqual(lang === 'ua'
        ? { months: UK_MONTHS, firstDayOfWeek: 1 }
        : { months: EN_MONTHS, firstDayOfWeek: 0 });
      const cal = await calendarFor(page, input);
      const month = await cal.locator('.flatpickr-month').first().evaluate((m) => {
        const sel = m.querySelector('select.flatpickr-monthDropdown-months') as HTMLSelectElement | null;
        return (sel ? sel.selectedOptions[0]?.textContent : m.querySelector('.cur-month')?.textContent)?.trim();
      });
      expect(lang === 'ua' ? UK_MONTHS : EN_MONTHS, 'місяць у відкритому календарі').toContain(month);
    };

    // старт без cookie — українська
    await page.goto('/');
    await expectHome('ua');

    // EN → редирект назад на ту саму сторінку (referrer)
    await langLink('en').click();
    await expectHome('en');

    // сесія тримає мову: головна → квартира → головна
    await page.goto(`/apartment/${id}`);
    await expectApartment('en');
    await page.locator('a.btn-back').click();
    await expectHome('en');

    // «uk» — не мова сайту (сесія зберігає 'ua'): /lang/uk нічого не міняє
    await page.goto('/lang/uk');
    await expectHome('en');

    // UA → назад українська, і в календарі квартири теж
    await langLink('ua').click();
    await expectHome('ua');
    await page.goto(`/apartment/${id}`);
    await expectApartment('ua');

    expect(jsErrors).toEqual([]);
  });

  test('21а. мобільний: без горизонтального скролу — головна й квартира, ua і en', async ({ page, request, jsErrors }) => {
    onlyProject('mobile', 'вьюпорт 390×844, touch');
    const [id] = await catalogIds(request);
    const vw = page.viewportSize()!.width;
    for (const lang of ['ua', 'en'] as const) {
      await page.goto(`/lang/${lang}`);
      for (const path of ['/', `/apartment/${id}`]) {
        await page.goto(path);
        await page.waitForLoadState('load');
        const m = await horizontalOverflow(page);
        const where = `${lang} ${path}: ${JSON.stringify(m)}`;
        // ширший за екран вміст мобільний Chromium «вміщує», зменшуючи сторінку, — тоді innerWidth > 390
        expect(m.innerWidth, `${where} — сторінку зменшено під ширший вміст`).toBe(vw);
        expect(m.scrollWidth, where).toBeLessThanOrEqual(m.clientWidth);
      }
    }
    expect(jsErrors).toEqual([]);
  });

  test('21б. мобільний: липка панель зʼявляється при прокрутці; без дат веде до календаря', async ({ page, request, jsErrors }) => {
    onlyProject('mobile', 'липка панель є лише ≤768px');
    const [id] = await catalogIds(request);
    await page.goto(`/apartment/${id}`);
    const bar = page.locator('#stickyBookBar');
    const widget = page.locator('#booking-widget');
    await expect(bar).toHaveCSS('display', 'flex');
    expect(digits(await page.locator('#stickyPrice').textContent()), 'ціна за ніч у панелі')
      .toBe(digits(await page.locator('.widget-price b').textContent()));
    await expect(page.locator('#stickyBtn')).toHaveText(exactText(tr('book_now')));

    // віджет на екрані → панель схована (не дублює його)
    await widget.scrollIntoViewIfNeeded();
    await expect(bar).not.toHaveClass(/\bvisible\b/);
    await expect(bar).not.toBeInViewport();
    // прокрутили далі, віджет за краєм → панель на екрані
    await page.locator('footer.site-footer').scrollIntoViewIfNeeded();
    await expect(bar).toHaveClass(/\bvisible\b/);
    await expect(bar).toBeInViewport({ ratio: 1 });

    // без дат кнопка веде у віджет і сама відкриває календар заїзду (після прокрутки)
    await page.locator('#stickyBtn').click();
    const ci = page.locator('#picker-checkin');
    await expect.poll(() => ci.evaluate((el) => !!(el as any)._flatpickr?.isOpen), 'календар заїзду не відкрився').toBe(true);
    await expect(ci).toBeInViewport();
    await expect(page.locator('.flatpickr-calendar.open')).toBeInViewport();
    // поки відкритий календар, панель не заважає
    await expect(bar).toHaveClass(/\bkbd\b/);
    await expect(bar).not.toBeInViewport();

    expect(jsErrors).toEqual([]);
  });

  test('21в. мобільний: з датами липка панель — серверна сума, кнопка веде до імені гостя', async ({ page, request, jsErrors }) => {
    onlyProject('mobile', 'липка панель є лише ≤768px');
    let found: { id: string; stay: NonNullable<ReturnType<typeof freeStay>> } | null = null;
    for (const id of await catalogIds(request)) {
      const stay = freeStay(await availability(request, id), 2);
      if (stay) { found = { id, stay }; break; }
    }
    test.skip(!found, 'live: нема вільних 2 ночей у найближчі 50 днів');
    const { id, stay } = found!;

    await page.goto(`/apartment/${id}`);
    const pending = waitQuote(page, id, stay, 2);
    await pickStay(page, stay);
    const r = await pending;
    expect(r.status()).toBe(200);
    const q = await r.json();
    await expectServerQuote(page, q);

    const bar = page.locator('#stickyBookBar');
    await expect.poll(async () => digits(await page.locator('#stickyPrice').textContent()), 'сума в панелі').toBe(q.total);
    await expect(page.locator('#stickyBtn')).toHaveText(exactText(tr('sticky_pay')));

    const posts: string[] = [];
    // лише /book: POST /api/ux — UX-телеметрія самого сайту (track('sticky_cta')), вона тут очікувана
    page.on('request', (req) => {
      if (req.method() === 'POST' && new URL(req.url()).pathname.startsWith('/book')) posts.push(req.url());
    });
    await page.locator('footer.site-footer').scrollIntoViewIfNeeded();
    await expect(bar).toHaveClass(/\bvisible\b/);
    await expect(bar).toBeInViewport({ ratio: 1 });

    // імені нема → кнопка не відправляє форму, а веде до поля імені
    await page.locator('#stickyBtn').click();
    const name = page.locator('#w-name');
    await expect(name).toBeFocused();
    await expect(name).toBeInViewport();
    await expect(bar, 'поки гість друкує, панель не заважає').toHaveClass(/\bkbd\b/);
    expect(posts, 'без імені форма не мала відправлятися').toEqual([]);

    expect(jsErrors).toEqual([]);
  });

  test('21г. мобільний: галерея — swiper, листається свайпом і стрілкою, лічильник іде за фото', async ({ page, request, jsErrors }) => {
    onlyProject('mobile', 'на десктопі при ≥5 фото замість swiper колаж (22)');
    const id = await apartmentWithPhotos(request, 2);
    test.skip(!id, 'live: у каталозі нема квартири з ≥2 фото — гортати нічого');

    await page.goto(`/apartment/${id}`);
    const hero = page.locator('.apt-hero-swiper');
    await expect(hero).toBeVisible();
    await expect(page.locator('.apt-collage')).toBeHidden();
    await expect.poll(() => hero.evaluate((el) => !!(el as any).swiper), 'swiper не ініціалізувався').toBe(true);
    const n = (await galleryPhotos(page)).length;
    const counter = page.locator('#galleryCountText');
    const expectSlide = async (i: number) => {
      await expect(counter).toHaveText(`${i + 1} / ${n}`);
      await expect(hero.locator('.swiper-slide-active .gallery-photo')).toHaveAttribute('data-index', String(i));
      await expect.poll(() => hero.evaluate((el) => (el as any).swiper.animating), 'swiper не зупинився').toBe(false);
    };

    await expectSlide(0);
    await swipe(page, hero, -0.7); // пальцем справа наліво → наступне (чому 0.7 — див. swipe())
    await expectSlide(1);
    await swipe(page, hero, 0.7);
    await expectSlide(0);
    // стрілки; loop: з першого назад — на останнє
    await hero.locator('.swiper-button-prev').click();
    await expectSlide(n - 1);
    await hero.locator('.swiper-button-next').click();
    await expectSlide(0);

    expect(jsErrors).toEqual([]);
  });

  // 30.10.2026: dead-клики по фото картки на головній (4 сесії з 22) — фото тепер веде туди ж, куди кнопка
  // картки, з датами пошуку; свайп і точки пагінації лишаються на головній.
  test('21д. головна: фото картки веде на квартиру з датами пошуку; свайп і точка — ні', async ({ page, request, jsErrors }) => {
    const mobile = test.info().project.name === 'mobile';
    const navs: string[] = [];
    page.on('request', (r) => { if (r.isNavigationRequest() && r.frame() === page.mainFrame()) navs.push(r.url()); });

    const openCard = async () => {
      await page.goto('/');
      const card = page.locator('.apt-row').filter({ has: page.locator('.apt-photo-swiper') }).first();
      const sw = card.locator('.apt-photo-swiper');
      await sw.scrollIntoViewIfNeeded();
      await expect.poll(() => sw.evaluate((el) => !!(el as any).swiper), 'swiper картки не ініціалізувався').toBe(true);
      // autoplay (десктоп) сам гортає кожні 5,5 с — без нього індекс слайда міняє лише жест тесту
      await sw.evaluate((el) => (el as any).swiper.autoplay?.stop());
      const id = (await card.locator('a.apt-detail-link').getAttribute('data-apt'))!;
      return { card, sw, id, photo: sw.locator('.swiper-slide-active img.apt-row-img') };
    };
    const press = (l: Locator) => (mobile ? l.tap() : l.click());

    // 1. без дат: тап по фото → сторінка квартири, як кнопка картки
    let { card, sw, id, photo } = await openCard();
    await press(photo);
    await page.waitForURL((u) => u.pathname === `/apartment/${id}`);

    // 2. свайп і точка пагінації гортають фото, але нікуди не ведуть
    ({ card, sw, id, photo } = await openCard());
    const n = await sw.locator('.swiper-slide').count();
    if (n > 1) {
      const index = () => sw.evaluate((el) => (el as any).swiper.realIndex as number);
      const settled = () => expect.poll(() => sw.evaluate((el) => (el as any).swiper.animating), 'swiper не зупинився').toBe(false);
      navs.length = 0;
      if (mobile) {
        await swipe(page, sw, -0.7);
      } else {
        // мишею: mousedown і mouseup на тому самому фото → браузер шле click; його мусить погасити swiper
        const box = (await sw.boundingBox())!;
        const y = box.y + box.height * 0.4;
        await page.mouse.move(box.x + box.width * 0.8, y);
        await page.mouse.down();
        await page.mouse.move(box.x + box.width * 0.2, y, { steps: 10 });
        await page.mouse.up();
      }
      // без preventClicks у swiper десктопний свайп мишею тут відкривав квартиру (перевірено 30.10);
      // на телефоні після свайпу браузер click не шле зовсім
      await page.waitForTimeout(300);
      expect(navs, 'свайп відкрив сторінку').toEqual([]);
      await expect.poll(index, 'свайп не перегорнув фото').toBe(1);
      await settled();
      await press(sw.locator('.swiper-pagination-bullet').nth(2 % n));
      await expect.poll(index, 'точка не перегорнула фото').toBe(2 % n);
      await settled();
      await page.waitForTimeout(300);
      expect(navs, 'точка відкрила сторінку').toEqual([]);
      expect(new URL(page.url()).pathname).toBe('/');
    }

    // 3. доступність: імʼя посилання з translations.py, без другого таб-стопу на ту саму ціль
    const link = sw.locator('.swiper-slide-active a.apt-photo-link');
    const name = (await card.locator('.apt-row-name').textContent())!.trim();
    await expect(link).toHaveAttribute('aria-label', `${tr('apt_photo_link')} ${name}`);
    await expect(link).toHaveAttribute('tabindex', '-1');
    if (!mobile) await expect(link).toHaveCSS('cursor', 'pointer');

    // 4. з датами пошуку (fake: стоянка, зайнята в A і B, лишає на головній з переписаними посиланнями)
    if (!IS_LIVE) {
      const [a, b] = [(await availability(request, APT_A))[0], (await availability(request, APT_B))[0]];
      const stay = { ci: a.from < b.from ? a.from : b.from, co: a.to > b.to ? a.to : b.to };
      ({ card, sw, id, photo } = await openCard());
      for (const [sel, iso] of [['#hs-checkin', stay.ci], ['#hs-checkout', stay.co]] as const) {
        const input = page.locator(sel);
        await (await dayCell(input, await calendarFor(page, input), iso)).click();
      }
      await page.locator('#heroSearch .hsearch-submit').click();
      await expect(page.locator('#availSummaryNone')).toBeVisible();
      await sw.scrollIntoViewIfNeeded();
      await press(photo);
      await page.waitForURL((u) => u.pathname === `/apartment/${id}`);
      const p = new URL(page.url()).searchParams;
      expect([p.get('check_in'), p.get('check_out'), p.get('guests')]).toEqual([stay.ci, stay.co, '2']);
    }

    expect(jsErrors).toEqual([]);
  });

  // 13.10.2026: колишні 22, 22б і 22в — один сценарій (відкрили лайтбокс один раз: фокус усередині,
  // клавіші, пастка фокуса, Escape; потім те саме EN — підписи мовою сторінки).
  test('22. лайтбокс з колажу: фокус усередині, лічильник, стрілки через край, Escape; EN-підписи', async ({ page, request, jsErrors }) => {
    onlyProject('desktop', 'колаж є лише ≥992px (на мобільному — swiper, 21г)');
    const id = await apartmentWithPhotos(request, 5);
    test.skip(!id, 'live: у каталозі нема квартири з ≥5 фото — колажу нема');

    await page.goto(`/apartment/${id}`);
    const photos = await galleryPhotos(page);
    const n = photos.length;
    const collage = page.locator('.apt-collage');
    await expect(collage).toBeVisible();
    await expect(page.locator('.apt-hero-swiper')).toBeHidden();
    const opener = collage.locator('[data-lightbox-open]');
    await expect(opener).toContainText(tr('view_all_photos'));
    await expect(opener).toContainText(`(${n})`);

    const lb = page.locator('.lightbox');
    const dialog = page.getByRole('dialog');
    const counter = lb.locator('.lightbox-counter');
    const img = lb.locator('.lightbox-img');
    const closeBtn = dialog.getByRole('button', { name: LB_CLOSE });
    const expectPhoto = async (i: number) => {
      await expect(counter).toHaveText(`${i + 1} / ${n}`);
      await expect(img).toHaveAttribute('src', photos[i]);
    };
    const focusInside = () => page.evaluate(() => !!document.activeElement?.closest('.lightbox'));

    await opener.click();
    await expect(lb).toHaveClass(/\bopen\b/);
    await expect(dialog).toBeVisible();
    await expect(dialog).toHaveAttribute('aria-modal', 'true');
    await expect(dialog).toHaveAttribute('aria-label', (await page.locator('.apt-detail-name').textContent())!.trim());
    await expectPhoto(0);
    expect(await page.evaluate(() => document.body.style.overflow), 'сторінка під лайтбоксом не гортається').toBe('hidden');

    // Регресія бага 22б (знайдено 11.09 групою 5, полагоджено 11.09): static/main.js open() кликав
    // f[0].focus(), поки .lightbox ще display:none — клас .open додається лише в requestAnimationFrame.
    // Фокус лишався на кнопці під лайтбоксом, Tab ішов по сторінці ПІД модальним вікном. Тепер фокус
    // ставиться в тому ж requestAnimationFrame, одразу після .open.
    await expect(closeBtn, '22б: фокус одразу на «Закрити»').toBeFocused();
    await page.keyboard.press('Tab');
    expect(await focusInside(), '22б: перший Tab лишається в лайтбоксі').toBe(true);

    // клавіші; main.js nav(): idx = (idx + d + n) % n — через край в обидва боки
    await page.keyboard.press('ArrowRight');
    await expectPhoto(1);
    await page.keyboard.press('ArrowLeft');
    await expectPhoto(0);
    await page.keyboard.press('ArrowLeft');
    await expectPhoto(n - 1);
    await page.keyboard.press('ArrowRight');
    await expectPhoto(0);

    // кнопки
    await dialog.getByRole('button', { name: LB_NEXT }).click();
    await expectPhoto(1);
    await dialog.getByRole('button', { name: LB_PREV }).click();
    await expectPhoto(0);

    // пастка фокуса: Tab / Shift+Tab ходять по кнопках лайтбокса по колу
    await closeBtn.focus();
    await page.keyboard.press('Shift+Tab');
    await expect(dialog.getByRole('button', { name: LB_NEXT })).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(closeBtn).toBeFocused();
    for (let i = 0; i < 4; i++) {
      await page.keyboard.press('Tab');
      expect(await focusInside(), `Tab ${i + 1}: фокус вийшов з лайтбокса`).toBe(true);
    }

    // Escape закриває, фокус повертається на кнопку, що відкрила
    await page.keyboard.press('Escape');
    await expect(lb).not.toHaveClass(/\bopen\b/);
    await expect(dialog).toBeHidden();
    await expect(opener, 'фокус після Escape').toBeFocused();
    expect(await page.evaluate(() => document.body.style.overflow)).toBe('');

    // плитка колажу відкриває саме своє фото; клік по тлу закриває
    await collage.locator('.apt-collage-item img').nth(2).click();
    await expectPhoto(2);
    await lb.click({ position: { x: 10, y: 10 } });
    await expect(dialog).toBeHidden();

    // Регресія бага 22в (знайдено 11.09 групою 5, полагоджено 11.09): підписи лайтбокса («Закрити»,
    // «Попереднє фото», «Наступне фото», alt «— фото N з M») були зашиті в static/main.js українською —
    // у EN екранний диктор читав гостеві українську. Тепер — translations.py → LIGHTBOX_TXT.
    // alt = «<назва квартири> — <підпис>»: мовою сторінки має бути лише підпис. Назва — дані каталогу
    // бек-офісу (живий віддає її українською й для EN, h1 теж), тож звіряється з h1, а не з мовою.
    await page.goto('/lang/en');
    await page.goto(`/apartment/${id}`);
    await page.locator('.apt-collage [data-lightbox-open]').click();
    const labels = await page.locator('.lightbox button').evaluateAll((els) => els.map((e) => e.getAttribute('aria-label') ?? ''));
    for (const l of labels) expect(l, 'кирилиця в підписі кнопки EN-сторінки').not.toMatch(/[а-яіїєґ]{3,}/i);
    await expect(dialog.getByRole('button', { name: tr('lightbox_close', 'en') })).toBeVisible();
    await expect(dialog.getByRole('button', { name: tr('lightbox_prev', 'en') })).toBeVisible();
    await expect(dialog.getByRole('button', { name: tr('lightbox_next', 'en') })).toBeVisible();

    const alt = (await img.getAttribute('alt')) ?? '';
    const sep = alt.lastIndexOf(' — ');
    expect(sep, `alt без « — »: ${JSON.stringify(alt)}`).toBeGreaterThan(0);
    const name = (await page.locator('.apt-detail-name').textContent())!.trim();
    expect(alt.slice(0, sep), 'назва в alt — та сама, що в h1').toBe(name);
    expect(alt.slice(sep + 3), 'підпис лайтбокса в alt').toBe(
      tr('lightbox_photo_of', 'en').replace('{n}', '1').replace('{total}', String(n)));

    expect(jsErrors).toEqual([]);
  });

  // 23а–в: календар пошуку головної на телефоні (розбір 30.10 на стенді).
  // Тапи — пальцем (Pixel 5, hasTouch): Flatpickr закривається від touchstart поза календарем.
  test('23а. головна, телефон: після заїзду — підсказка «оберіть виїзд» і підсвічене поле виїзду', async ({ page, jsErrors }) => {
    onlyProject('mobile', 'на компʼютері календар виїзду відкривається сам');
    await page.goto('/');
    const ci = page.locator('#hs-checkin');
    const co = page.locator('#hs-checkout');
    const hint = page.locator('#hs-hint');
    const coField = page.locator('.hsearch-field', { has: co });
    await expect(hint).toBeHidden();

    const stay = { ci: addDays(today(), 3), co: addDays(today(), 5) };
    await calendarFor(page, ci);
    await (await heroDay(page, ci, stay.ci)).tap();
    await expect(page.locator('.flatpickr-calendar.open'), 'на телефоні виїзд сам не відкривається').toHaveCount(0);
    await expect(hint).toBeVisible();
    await expect(hint).toHaveText(exactText(tr('hero_pick_checkout')));
    await expect(coField).toHaveClass(/\bis-next\b/);

    await calendarFor(page, co);
    await (await heroDay(page, co, stay.co)).tap();
    await expect(hint).toBeHidden();
    await expect(coField).not.toHaveClass(/\bis-next\b/);
    expect(jsErrors).toEqual([]);
  });

  test('23б. головна, телефон: тап по закритому дню — пояснення в календарі, а не тиша', async ({ page, jsErrors }) => {
    onlyProject('mobile', 'розбір тапів по календарю телефона');
    await page.goto('/');
    const ci = page.locator('#hs-checkin');
    const co = page.locator('#hs-checkout');
    const cal = await calendarFor(page, ci);
    const calHint = cal.locator('.fp-hint');

    // минулий день: є на першому екрані, крім випадку «сьогодні понеділок і 1-ше число»
    const past = cal.locator('.flatpickr-day.flatpickr-disabled').first();
    if (await past.count()) {
      await past.tap();
      await expect(calHint).toBeVisible();
      await expect(calHint).toHaveText(exactText(tr('cal_day_past')));
      await expect(cal, 'календар лишається відкритим').toHaveClass(/\bopen\b/);
    } else {
      note('skip-part', 'минулих днів на першому екрані немає (1-ше число, понеділок)');
    }

    const ciDay = addDays(today(), 3);
    await (await heroDay(page, ci, ciDay)).tap();
    const coCal = await calendarFor(page, co);
    // типовий промах: у календарі виїзду тапнути сам день заїзду
    await (await heroDay(page, co, ciDay)).tap();
    const coHint = coCal.locator('.fp-hint');
    await expect(coHint).toBeVisible();
    await expect(coHint).toHaveText(exactText(tr('cal_day_before_checkin')));
    await expect(coCal).toHaveClass(/\bopen\b/);
    expect(await co.inputValue(), 'виїзд не обрано').toBe('');
    expect(jsErrors).toEqual([]);
  });

  test('23в. головна, низький телефон 360×560: відкритий календар цілком на екрані', async ({ page, jsErrors }) => {
    onlyProject('mobile', 'висота екрана телефона');
    await page.setViewportSize({ width: 360, height: 560 });
    await page.goto('/');
    // поле в середині екрана: календарю не вистачає місця ні над полем, ні під ним
    await page.evaluate(() => window.scrollTo(0, 120));
    await page.locator('#hs-checkin').tap();
    const cal = page.locator('.flatpickr-calendar.open');
    await expect(cal).toHaveCount(1);
    await expect(cal, 'низ календаря за краєм екрана').toBeInViewport({ ratio: 1 });
    await expect(page.locator('#hs-checkin')).toBeInViewport();
    expect(jsErrors).toEqual([]);
  });

  // 23г, розбір (14), 30.10: «+» гостей на стелі мовчав (головна) — тепер тьмяніє і пояснює.
  // Стеля — з data-max (сервер, app.GUESTS_MAX), а не своє число в тесті.
  test('23г. «Більше гостей» на максимумі — тьмяна кнопка і підказка, а не тиша (головна, квартира)', async ({ page, jsErrors }) => {
    onlyProject('mobile', 'тап пальцем; desktop бачить ту саму розмітку');
    const spots = [
      { url: '/', box: '.hsearch-guests', plus: '#hs-plus', minus: '#hs-minus', count: '#hs-count', hint: '#hs-guests-hint' },
      { url: `/apartment/${APT_A}`, box: '.guest-counter', plus: '#guest-plus', minus: '#guest-minus', count: '#guest-display', hint: '#guest-max-hint' },
    ];
    for (const s of spots) {
      await page.goto(s.url);
      const max = Number(await page.locator(s.box).getAttribute('data-max'));
      expect(max, `${s.url}: data-max зі стелею від сервера`).toBeGreaterThan(1);
      const plus = page.locator(s.plus);
      const hint = page.locator(s.hint);
      await expect(hint).toBeHidden();
      for (let i = 0; i < 6 && digits(await page.locator(s.count).textContent()) < max; i++) await plus.tap();
      await expect(page.locator(s.count)).toHaveText(exactText(String(max)));
      await expect(plus, `${s.url}: «+» на стелі тьмяний`).toHaveAttribute('aria-disabled', 'true');
      await expect(hint, `${s.url}: підказка лише після тапу по «+» на стелі`).toBeHidden();
      // aria-disabled Playwright вважає «not enabled» і чекає; палець гостя тап однаково доносить.
      // force не чекає ні кінця плавної прокрутки (html: scroll-behavior smooth), ні зсуву верстки під
      // навантаженням (фото над віджетом) — тап може лягти повз. Прокрутка миттєва, а «тап → підказка»
      // повторюємо: тап по «+» на стелі ідемпотентний (лічильник перевіряємо нижче)
      await expect(async () => {
        await plus.evaluate((el) => el.scrollIntoView({ block: 'center', behavior: 'instant' }));
        await plus.tap({ force: true });
        await expect(hint).toBeVisible({ timeout: 700 });
      }).toPass({ timeout: 10_000 });
      await expect(page.locator(s.count)).toHaveText(exactText(String(max)));
      await expect(hint).toHaveText(exactText(tr('guests_max_hint').replace('{n}', String(max))));
      await page.locator(s.minus).tap();
      await expect(hint, `${s.url}: нижче стелі підказка ховається`).toBeHidden();
      await expect(plus).toHaveAttribute('aria-disabled', 'false');
    }
    expect(jsErrors).toEqual([]);
  });

  // 24а–в: календар квартири з зайнятими днями (розбір 30.10, задача 16).
  // Зайнятість — з /api/availability (у fake квартира A: +10…+13), не з чисел у тесті.
  test('24а. квартира, телефон: після заїзду — підказка «оберіть виїзд» і підсвічене поле виїзду', async ({ page, jsErrors }) => {
    onlyProject('mobile', 'на компʼютері календар виїзду відкривається сам');
    await page.goto(`/apartment/${APT_A}`);
    const ci = page.locator('#picker-checkin');
    const co = page.locator('#picker-checkout');
    const hint = page.locator('#widget-next-hint');
    const coField = page.locator('.widget-date-half', { has: co });
    await expect(hint).toBeHidden();

    const stay = freeStay(await availability(page.request, APT_A), 2, addDays(today(), 2))!;
    await calendarFor(page, ci);
    await (await heroDay(page, ci, stay.ci)).tap();
    await expect(page.locator('#f-check-in')).toHaveValue(stay.ci);
    await expect(page.locator('.flatpickr-calendar.open'), 'на телефоні виїзд сам не відкривається').toHaveCount(0);
    await expect(hint).toBeVisible();
    await expect(hint).toHaveText(exactText(tr('hero_pick_checkout')));
    await expect(coField).toHaveClass(/\bis-next\b/);

    await calendarFor(page, co);
    await (await heroDay(page, co, stay.co)).tap();
    await expect(page.locator('#f-check-out')).toHaveValue(stay.co);
    await expect(hint).toBeHidden();
    await expect(coField).not.toHaveClass(/\bis-next\b/);
    expect(jsErrors).toEqual([]);
  });

  test('24б. квартира, телефон: тап по закритому дню — причина в календарі (минулий, зайнятий, до заїзду, за зайнятим)', async ({ page, jsErrors }) => {
    onlyProject('mobile', 'розбір тапів по календарю телефона');
    const ranges = await availability(page.request, APT_A);
    const busy = ranges.find((r) => r.from >= addDays(today(), 3));
    test.skip(!busy, 'у квартири нема зайнятого відрізка після +3');
    const { from } = busy!;
    const ciDay = addDays(from, -2);
    await page.goto(`/apartment/${APT_A}`);
    const ci = page.locator('#picker-checkin');
    const co = page.locator('#picker-checkout');
    async function expectReason(cal: Locator, key: string) {
      await expect(cal.locator('.fp-hint'), key).toBeVisible();
      await expect(cal.locator('.fp-hint')).toHaveText(exactText(tr(key)));
      await expect(cal, 'календар лишається відкритим').toHaveClass(/\bopen\b/);
    }

    // виїзд до заїзду: увесь календар сірий
    let cal = await calendarFor(page, co);
    await (await heroDay(page, co, addDays(today(), 5))).tap();
    await expectReason(cal, 'cal_pick_checkin_first');
    await page.keyboard.press('Escape'); // закрити (календар над полем перекриває все навколо)
    await expect(page.locator('.flatpickr-calendar.open')).toHaveCount(0);

    cal = await calendarFor(page, ci);
    const past = cal.locator('.flatpickr-day.flatpickr-disabled:not(.prevMonthDay):not(.nextMonthDay)').first();
    if (await past.count() && await past.evaluate((d) => (d as any).dateObj < new Date(new Date().setHours(0, 0, 0, 0)))) {
      await past.tap();
      await expectReason(cal, 'cal_day_past');
    } else {
      note('skip-part', 'минулих днів на першому екрані немає (1-ше число)');
    }
    await (await heroDay(page, ci, from)).tap();
    await expectReason(cal, 'cal_day_booked');
    expect(await page.locator('#f-check-in').inputValue(), 'зайнята ніч не обрана').toBe('');

    await (await heroDay(page, ci, ciDay)).tap();
    cal = await calendarFor(page, co);
    await (await heroDay(page, co, ciDay)).tap();
    await expectReason(cal, 'cal_day_before_checkin');
    await (await heroDay(page, co, addDays(from, 1))).tap();
    await expectReason(cal, 'cal_checkout_through_booked');
    expect(await page.locator('#f-check-out').inputValue(), 'виїзд не обрано').toBe('');
    // виїзд на першу зайняту ніч — дозволений (ніч перед нею вільна)
    await (await heroDay(page, co, from)).tap();
    await expect(page.locator('#f-check-out')).toHaveValue(from);
    expect(jsErrors).toEqual([]);
  });

  test('24в. квартира, низький телефон 360×560: відкритий календар цілком на екрані', async ({ page, jsErrors }) => {
    onlyProject('mobile', 'висота екрана телефона');
    await page.setViewportSize({ width: 360, height: 560 });
    await page.goto(`/apartment/${APT_A}`);
    const ci = page.locator('#picker-checkin');
    // поле на 250 px від верху: ні над ним, ні під ним календарю (~316 px) місця немає
    const at = async () => (await ci.boundingBox())!.y;
    await expect(async () => {
      await ci.evaluate((el) => window.scrollTo({ top: window.pageYOffset + el.getBoundingClientRect().top - 250, behavior: 'instant' as ScrollBehavior }));
      const y1 = await at();
      await page.waitForTimeout(150);
      expect(Math.abs((await at()) - y1), 'верстка ще зсувається').toBeLessThan(1);
      expect(Math.abs(y1 - 250)).toBeLessThan(2);
    }).toPass({ timeout: 10_000 });
    await ci.tap();
    const cal = page.locator('.flatpickr-calendar.open');
    await expect(cal).toHaveCount(1);
    await expect(cal, 'низ календаря за краєм екрана').toBeInViewport({ ratio: 1 });
    await expect(ci).toBeInViewport();
    expect(jsErrors).toEqual([]);
  });
});

/** Клітинка дня `iso` у відкритому календарі hero — і як день сусіднього місяця (той теж тапається). */
async function heroDay(page: Page, input: Locator, iso: string): Promise<Locator> {
  const label = await input.evaluate((el, d) => {
    const fp = (el as any)._flatpickr;
    const [y, m, day] = d.split('-').map(Number);
    return fp.formatDate(new Date(y, m - 1, day), fp.config.ariaDateFormat) as string;
  }, iso);
  const cal = page.locator('.flatpickr-calendar.open');
  const cell = cal.locator(`.flatpickr-day[aria-label="${label}"]`);
  for (let i = 0; i < 3 && (await cell.count()) === 0; i++) await cal.locator('.flatpickr-next-month').tap();
  await expect(cell.first(), `день ${iso} у календарі`).toBeVisible();
  return cell.first();
}

// ---------------------------------------------------------------------------

/** Ширина документа проти екрана (мобільний) + до 5 елементів, що вилазять праворуч (для діагнозу). */
function horizontalOverflow(page: Page) {
  return page.evaluate(() => {
    const de = document.documentElement;
    const cw = de.clientWidth;
    const wide = [...document.body.querySelectorAll('*')]
      .filter((e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.right > cw + 1; })
      .slice(0, 5)
      .map((e) => `${e.tagName.toLowerCase()}${e.id ? `#${e.id}` : ''}.${[...e.classList].join('.')}`);
    return { scrollWidth: de.scrollWidth, clientWidth: cw, innerWidth: window.innerWidth, wide };
  });
}

/**
 * Справжній свайп пальцем: CDP Input.dispatchTouchEvent (Chromium) → touch/pointer-події, як на телефоні.
 * `fraction` — яку частку ширини пройти (мінус — справа наліво). Бери ≥ 0.7: кожен крок — окремий запит
 * CDP, жест триває > 300 мс, і swiper вважає його «довгим» (longSwipesMs) — такий гортає лише від
 * 50% ширини (longSwipesRatio), а перші пікселі swiper зʼїдає на розпізнавання напрямку. Свайп на
 * пів екрана відпружинює назад — це поведінка swiper, не баг (перевірено 11.09).
 */
async function swipe(page: Page, target: Locator, fraction: number) {
  const box = await target.boundingBox();
  expect(box, 'галерея поза екраном').not.toBeNull();
  const y = box!.y + box!.height * 0.4;
  const x0 = box!.x + box!.width * (fraction < 0 ? 0.9 : 0.1);
  const dx = box!.width * fraction;
  const cdp = await page.context().newCDPSession(page);
  try {
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: x0, y }] });
    for (let i = 1; i <= 10; i++) {
      await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: x0 + (dx * i) / 10, y }] });
    }
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  } finally {
    await cdp.detach();
  }
}
