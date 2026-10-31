/**
 * Де лежать браузери Playwright сайту — ОДНЕ місце, яке читають playwright.config.ts і
 * npm-скрипти (`node -r ./browsers-env.cjs …`).
 *
 * Спільний ~/.cache/ms-playwright ділять кілька проєктів, і установник одного зносить «чужі»
 * версії іншого (11.09 установка іншого проєкту знесла наш chromium). Тому в сайту свій
 * каталог — поза git і спільний для всіх worktree сайту: SITE_PLAYWRIGHT_BROWSERS_PATH або
 * ~/.cache/ms-playwright-central-aparts-site (на машині це може бути симлінк на інше місце).
 *
 * Лише поза CI і лише якщо PLAYWRIGHT_BROWSERS_PATH не задано ззовні (так його й переозначають).
 */
const { homedir } = require('node:os');
const path = require('node:path');

const SITE_BROWSERS_PATH = process.env.SITE_PLAYWRIGHT_BROWSERS_PATH
  || path.join(homedir(), '.cache', 'ms-playwright-central-aparts-site');

if (!process.env.CI && !process.env.PLAYWRIGHT_BROWSERS_PATH) {
  process.env.PLAYWRIGHT_BROWSERS_PATH = SITE_BROWSERS_PATH;
}

module.exports = { SITE_BROWSERS_PATH };
