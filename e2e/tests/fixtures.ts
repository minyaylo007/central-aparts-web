/**
 * Спільні крючки смоки. Кожен spec імпортує `test` і `expect` ЗВІДСИ, не з @playwright/test:
 * тоді на кожен тест автоматично стоїть сторож сторонніх запитів.
 *
 * Сторож (context.route на ВСЕ):
 *   - свій сайт (BASE_URL)                  → пропускаємо;
 *   - бек-офіс, лише в live і лише GET/HEAD → пропускаємо (фото квартир);
 *     будь-який інший метод на бек-офіс     → обрив + порушення (браузер не пише в бойовий);
 *   - cdn.jsdelivr.net/npm/<pkg>@<ver>/…    → віддаємо з e2e/node_modules тієї ж версії
 *     (bootstrap, swiper, flatpickr, bootstrap-icons) — Flatpickr справжній, мережі нема;
 *   - усе інше (GTM, Google Analytics, пиксель Facebook, шрифти Google, Монобанк…)
 *     → порожня заглушка за типом ресурсу. НІЧОГО не йде в бойову аналітику власника.
 * Сітка: сторонній запит, що завершився мимо route, — порушення; виняток — навігація iframe,
 * скасована браузером до route (ERR_ABORTED, у мережу не виходила) → traffic.cancelled.
 */
import { test as base, expect, type BrowserContext, type Request, type Route } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { BACKOFFICE_URL, BASE_URL, IS_LIVE } from '../smoke-env';

export { expect };

export type Traffic = {
  /** Сторонні запити, замінені заглушкою (метод + URL). */
  stubbed: string[];
  /** Шляхи CDN, відданих з node_modules. */
  cdn: string[];
  /** Порушення, що валять тест: запис у бек-офіс, промах CDN, запит мимо сторожа. */
  violations: string[];
  /** Редиректи свого сайту на чужий домен (POST /book → Монобанк): що відповів сервер. */
  offsite: { from: string; status: number; location: string }[];
  /** Навігації чужих iframe, які браузер скасував до route (у мережу не виходили), — не порушення. */
  cancelled: string[];
};

export type JsError = { kind: 'pageerror' | 'console'; text: string; url: string };

const NODE_MODULES = path.resolve(__dirname, '..', 'node_modules');
const CONTENT_TYPES: Record<string, string> = {
  '.css': 'text/css; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
  '.json': 'application/json',
  '.map': 'application/json',
  '.woff2': 'font/woff2',
  '.woff': 'font/woff',
};
const GIF_1X1 = Buffer.from('R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7', 'base64');

async function serveCdnFromNodeModules(route: Route, url: URL, log: Traffic) {
  const m = /^\/npm\/([^@/]+)@([^/]+)\/(.+)$/.exec(url.pathname);
  try {
    if (!m) throw new Error('незнайомий шлях CDN');
    const [, name, wanted, file] = m;
    const pkgDir = path.join(NODE_MODULES, name);
    const { version } = JSON.parse(await readFile(path.join(pkgDir, 'package.json'), 'utf8'));
    if (version !== wanted && !version.startsWith(`${wanted}.`)) {
      throw new Error(`у node_modules ${name}@${version}, шаблон просить @${wanted} — онови e2e/package.json`);
    }
    const filePath = path.join(pkgDir, file);
    if (!filePath.startsWith(pkgDir + path.sep)) throw new Error('шлях поза пакетом');
    const body = await readFile(filePath);
    log.cdn.push(url.pathname);
    return route.fulfill({
      status: 200,
      body,
      contentType: CONTENT_TYPES[path.extname(file)] ?? 'application/octet-stream',
      headers: { 'access-control-allow-origin': '*' },
    });
  } catch (e) {
    log.violations.push(`CDN ${url.href}: ${(e as Error).message}`);
    return route.fulfill({ status: 404, body: '' });
  }
}

function stubThirdParty(route: Route, url: URL) {
  switch (route.request().resourceType()) {
    case 'script':
      return route.fulfill({ status: 200, contentType: 'application/javascript', body: '' });
    case 'stylesheet':
      return route.fulfill({ status: 200, contentType: 'text/css', body: '' });
    case 'image':
      return route.fulfill({ status: 200, contentType: 'image/gif', body: GIF_1X1 });
    case 'document':
      // напр. редирект /book → Монобанк: сторінка-заглушка, справжній Монобанк не відкривається
      return route.fulfill({
        status: 200,
        contentType: 'text/html; charset=utf-8',
        body: `<!doctype html><title>smoke stub</title><p data-smoke-stub="${url.hostname}">stub</p>`,
      });
    default: // xhr/fetch/beacon/ping (пиксель, collect), шрифти
      return route.fulfill({ status: 204, body: '' });
  }
}

/** Навігація iframe (не головного кадру). Запит service worker'а кадру не має — тоді false. */
function isChildFrameNavigation(req: Request): boolean {
  try {
    return req.isNavigationRequest() && req.frame().parentFrame() !== null;
  } catch {
    return false;
  }
}

export async function guardThirdParty(context: BrowserContext, log: Traffic) {
  const site = new URL(BASE_URL).origin;
  const backoffice = new URL(BACKOFFICE_URL).origin;
  const isForeign = (url: URL) =>
    /^https?:$/.test(url.protocol) && url.origin !== site && !(IS_LIVE && url.origin === backoffice);

  // Сітка під сторожем: будь-який сторонній запит, що завершився, але НЕ пройшов через route
  // нижче, — порушення. Так ловиться, напр., ціль редиректу (route її не бачить, див. нижче).
  const routed = new WeakSet<Request>();
  const checkRouted = (req: Request) => {
    if (!isForeign(new URL(req.url())) || routed.has(req)) return;
    // Навігацію дочірнього кадру браузер скасовує, коли головний кадр іде геть: iframe карти на
    // головній (loading="lazy") стартує при прокрутці, а hero-пошук тієї ж миті веде на квартиру.
    // Запит тримало перехоплення (route бачить КОЖЕН запит, доки обробник не вирішить), до
    // обробника він не дійшов — ERR_ABORTED за ~2 мс, відповіді нема, у мережу не виходив. Це не
    // порушення (CI 11.09, run 34599422539; локально 4 з 40), але видно в traffic.cancelled.
    // Усе інше мимо route — отримана відповідь або інша помилка — як і раніше порушення.
    if (req.failure()?.errorText === 'net::ERR_ABORTED' && isChildFrameNavigation(req)) {
      log.cancelled.push(`${req.method()} ${req.url()}`);
      return;
    }
    log.violations.push(`запит пройшов мимо сторожа в мережу: ${req.method()} ${req.url()}`);
  };
  context.on('requestfinished', checkRouted);
  context.on('requestfailed', checkRouted);

  await context.route(() => true, async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    if (!/^https?:$/.test(url.protocol)) return route.fallback();
    if (url.origin === site) {
      // Playwright: «handler will only be called for the first url if the response is a redirect» —
      // ціль редиректу route НЕ бачить (навіть коли 302 віддали через route.fulfill), і
      // POST /book → 302 → pay.monobank.ua ішов у СПРАВЖНЮ мережу (11.09: браузер завантажив
      // сторінку Монобанку за вигаданим рахунком). Тому POST-навігацію свого сайту забираємо самі
      // без редиректів. Редирект на чужий домен браузер не отримує: замість 302 — сторінка, що
      // робить location.replace(ціль). Це нова навігація, її route бачить → заглушка; page.url() —
      // справжня ціль, історія як після 302 (/book не лишається). Що сказав сервер — traffic.offsite.
      if (req.isNavigationRequest() && req.method() !== 'GET') {
        const resp = await route.fetch({ maxRedirects: 0 });
        const location = resp.headers()['location'];
        const target = resp.status() >= 300 && resp.status() < 400 && location ? new URL(location, url) : null;
        if (target && isForeign(target)) {
          log.offsite.push({ from: `${req.method()} ${url.pathname}`, status: resp.status(), location: target.href });
          const js = JSON.stringify(target.href).replace(/</g, '\\u003c');
          return route.fulfill({
            status: 200,
            contentType: 'text/html; charset=utf-8',
            body: `<!doctype html><title>smoke redirect</title><script>location.replace(${js})</script>`,
          });
        }
        return route.fulfill({ response: resp });
      }
      return route.fallback();
    }
    routed.add(req);
    if (IS_LIVE && url.origin === backoffice) {
      if (req.method() === 'GET' || req.method() === 'HEAD') return route.fallback();
      log.violations.push(`браузер пише в бек-офіс напряму: ${req.method()} ${url.href}`);
      return route.abort();
    }
    if (url.hostname === 'cdn.jsdelivr.net') return serveCdnFromNodeModules(route, url, log);
    log.stubbed.push(`${req.method()} ${url.href}`);
    return stubThirdParty(route, url);
  });
}

/**
 * Відомі баги застосунку, що вискакують у ЧУЖИХ сценаріях випадково (залежно від розкладки).
 * jsErrors їх не рахує, а пише в анотацію `known_bug`. Кожен пункт мусить мати свій test.fixme,
 * який відтворює баг детерміновано, — фільтр не ховає находку, а лише прибирає флак.
 * Полагодили баг — прибрати пункт звідси й зняти fixme.
 * Зараз порожньо: єдиний пункт (flatpickr 4.6.13 + асинхронний flatpickr.min.css → SecurityError
 * на cssRules чужої таблиці) полагоджено 11.09, його fixme у 04-booking.spec.ts став звичайним тестом.
 * Формат пункту: `{ match: /текст винятку/, why: 'що це і де його test.fixme' }`.
 */
const KNOWN_APP_BUGS: { match: RegExp; why: string }[] = [];

/** Сценарій пише в бек-офіс (купон, /book) — у live його не запускаємо. Кликати в тілі тесту. */
export function skipWritesInLive(what: string) {
  base.skip(IS_LIVE, `live: ${what} пише в бойовий бек-офіс — сценарій лише для SMOKE_BACKEND=fake`);
}

export const test = base.extend<{ traffic: Traffic; jsErrors: JsError[] }>({
  traffic: [
    async ({ context }, use) => {
      const log: Traffic = { stubbed: [], cdn: [], violations: [], offsite: [], cancelled: [] };
      await guardThirdParty(context, log);
      await use(log);
      if (log.cancelled.length) {
        base.info().annotations.push({ type: 'cancelled_iframe', description: log.cancelled.join('; ') });
      }
      expect(log.violations, 'сторож сторонніх запитів').toEqual([]);
    },
    { auto: true },
  ],

  /**
   * JS-помилки сторінки: неперехоплені винятки + console.error зі СВОГО походження.
   * Помилки від заглушених сторонніх ресурсів не рахуються (їх і так нема в мережі).
   */
  jsErrors: async ({ page }, use) => {
    const site = new URL(BASE_URL).origin;
    const errors: JsError[] = [];
    page.on('pageerror', (e) => {
      const known = KNOWN_APP_BUGS.find((k) => k.match.test(e.message));
      if (!known) return errors.push({ kind: 'pageerror', text: e.message, url: page.url() });
      // відомий баг застосунку: чужі сценарії від нього не падають, але його видно у звіті
      try { base.info().annotations.push({ type: 'known_bug', description: `${known.why} — ${e.message}` }); } catch { /* тест уже скінчився */ }
    });
    page.on('console', (msg) => {
      if (msg.type() !== 'error') return;
      const src = msg.location().url || '';
      if (src && !src.startsWith(site)) return;
      errors.push({ kind: 'console', text: msg.text(), url: src });
    });
    await use(errors);
  },
});
