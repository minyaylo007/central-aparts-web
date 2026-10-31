/**
 * Налаштування смоки з оточення — ОДНЕ місце, яке читають і playwright.config.ts,
 * і тести. Докладно про кожну змінну — e2e/README.md.
 */

const backend = (process.env.SMOKE_BACKEND || 'fake').toLowerCase();
if (backend !== 'fake' && backend !== 'live') {
  throw new Error(`SMOKE_BACKEND=${backend}: дозволено лише fake або live`);
}

/** fake — сайт із підмінним бек-офісом, без мережі; live — справжній BACKOFFICE_URL. */
export const BACKEND: 'fake' | 'live' = backend;
export const IS_LIVE = BACKEND === 'live';

/** Адреса сайту під тестом. Задана ззовні (коробка) — свій сервер не піднімаємо. */
export const BASE_URL = (process.env.SMOKE_BASE_URL || 'http://127.0.0.1:8010').replace(/\/+$/, '');
export const OWN_SERVER = !process.env.SMOKE_BASE_URL;

/** Бек-офіс, до якого в live дозволено браузеру ходити по GET (фото квартир). */
export const BACKOFFICE_URL = (process.env.SMOKE_BACKOFFICE_URL || 'https://cv.central-aparts.store')
  .replace(/\/+$/, '');

/** Імена хоста для сценаріїв 1–2 (лише на коробці). Порожньо — ці сценарії skip. */
export const HOSTS = (process.env.SMOKE_HOSTS || '')
  .split(/[\s,]+/)
  .map((h) => h.trim().replace(/^https?:\/\//, '').replace(/\/.*$/, ''))
  .filter(Boolean);

/** Канонічне імʼя (SITE_URL у config.py). */
export const CANONICAL_HOST = process.env.SMOKE_CANONICAL_HOST || 'www.central-aparts.store';
