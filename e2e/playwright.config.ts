// Свій каталог браузерів сайту поза CI (див. browsers-env.cjs). Ставить PLAYWRIGHT_BROWSERS_PATH
// у process.env до старту воркерів — вони його успадковують і запускають chromium звідти.
import './browsers-env.cjs';
import { defineConfig, devices } from '@playwright/test';
import { existsSync } from 'node:fs';
import { homedir, tmpdir } from 'node:os';
import path from 'node:path';
import { BACKEND, BACKOFFICE_URL, BASE_URL, OWN_SERVER } from './smoke-env';

/**
 * Смока публічного сайту. Два режими (SMOKE_BACKEND): fake — за замовчуванням і в CI,
 * live — справжній бек-офіс, лише читаючі сценарії. Див. e2e/README.md.
 *
 * Сервер під тестом — gunicorn на 127.0.0.1:8010 (реєстр портів машини: порт смоки й
 * заміру навантаження), і ТІЛЬКИ там. Чужий процес на 8010 — не «перевикористовуємо»
 * (reuseExistingServer: false), а падаємо: це не наш сервер. 8004 — сервіс сайту, туди смока
 * свій сервер не піднімає (прогін проти нього — SMOKE_BASE_URL, див. e2e/README.md).
 */

const REPO_ROOT = path.resolve(__dirname, '..');
const VENV_PYTHON = path.join(homedir(), 'venvs', 'central-aparts-site', 'bin', 'python');
const PYTHON = process.env.SMOKE_PYTHON || (existsSync(VENV_PYTHON) ? VENV_PYTHON : 'python3');

const gunicorn = [
  PYTHON, '-m', 'gunicorn',
  '--chdir', REPO_ROOT,
  '--pythonpath', path.join(REPO_ROOT, 'e2e'),
  // -w 1: стан підміни (остання бронь, котирування) живе в памʼяті одного процесу
  '-w', '1', '--threads', '4', '--timeout', '60',
  '-b', '127.0.0.1:8010',
  // gunicorn 26 інакше створює $XDG_RUNTIME_DIR/gunicorn.ctl — один шлях на користувача,
  // його ділять gunicorn інших проєктів; паралельні запуски заважали б одне одному
  '--no-control-socket',
  BACKEND === 'fake' ? 'fake_wsgi:application' : 'wsgi:application',
].join(' ');

export default defineConfig({
  testDir: './tests',
  timeout: 30_000,
  expect: { timeout: 7_000 },
  forbidOnly: !!process.env.CI,
  retries: 0, // смока, що перезапускається, ховає флак — хай буде видно
  workers: process.env.CI ? 2 : 4,
  reporter: process.env.CI
    ? [['github'], ['list'], ['html', { open: 'never' }]]
    : [['list'], ['html', { open: 'never' }]],

  use: {
    baseURL: BASE_URL,
    locale: 'uk-UA',
    timezoneId: 'Europe/Kyiv',
    serviceWorkers: 'block',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },

  projects: [
    {
      name: 'desktop',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1366, height: 900 } },
    },
    {
      name: 'mobile',
      // 13.10.2026 (аудит «ускорение II»): мобільний вьюпорт лише для спеки 05 — 21а–г живуть
      // тільки тут, а 20 і 22 на ньому мають свої гілки (перемикач .lang-mobile-btn, skip колажу).
      // Решта спек на 390×844 лише повторювала desktop: бронь, купон, форма від вьюпорта не залежать.
      testMatch: /05-lang-mobile-gallery\.spec\.ts$/,
      // @http — перевірки рівня HTTP (заголовки, редиректи): від вьюпорта не залежать
      grepInvert: /@http/,
      use: { ...devices['Pixel 5'], viewport: { width: 390, height: 844 } },
    },
  ],

  webServer: OWN_SERVER
    ? {
        command: gunicorn,
        url: `${BASE_URL}/health`,
        reuseExistingServer: false,
        timeout: 30_000,
        stdout: 'pipe',
        stderr: 'pipe',
        gracefulShutdown: { signal: 'SIGTERM', timeout: 5_000 },
        env: {
          ...(process.env as Record<string, string>),
          ENV: 'smoke',
          SECRET_KEY: 'smoke-not-a-secret',
          BACKOFFICE_URL,
          // Ключ смоці не потрібен: читаючі ручки бек-офісу відкриті задумом. Затираємо
          // явно, щоб ключ з оточення оболонки не поїхав у жоден запит.
          PUBLIC_API_KEY: '',
          // Своя статистика (analytics/) увімкнена в усій смоці: analytics.js і rrweb ходять
          // у кожному сценарії, і сторож JS-помилок ловить їхні збої. База — тимчасова, своя на запуск.
          ANALYTICS_DB: path.join(tmpdir(), `site-smoke-analytics-${process.pid}.db`),
          STATS_KEY: '',
        },
      }
    : undefined,
});
