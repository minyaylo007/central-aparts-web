// Знімки для звіту: головна й квартира на 390 і 1440, перший екран і вся сторінка, плюс
// hero-пошук з вибраними датами (людський вигляд дат, назва квартири в select).
//   node tools/shots.mjs <base-url> <тека>      # base-url — свій gunicorn (смока, 127.0.0.1:8010)
// Лише читання сторінок; жодних форм не відправляє.
import '../browsers-env.cjs';
import { chromium } from 'playwright';
import { mkdir } from 'node:fs/promises';
import path from 'node:path';

const [base, out] = process.argv.slice(2);
if (!base || !out) { console.error('usage: node tools/shots.mjs <base-url> <dir>'); process.exit(2); }
await mkdir(out, { recursive: true });
const APT = '/apartment/5a0e0000-0000-4000-8000-00000000000a';
const browser = await chromium.launch();
try {
  for (const [w, h, dpr] of [[390, 844, 2], [1440, 900, 1]]) {
    const context = await browser.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: dpr, locale: 'uk-UA', timezoneId: 'Europe/Kyiv',
      // .reveal без прокрутки лишається прозорим — на повному знімку секції були б порожні
      reducedMotion: 'reduce' });
    const page = await context.newPage();
    for (const [name, url] of [['home', '/'], ['apartment', APT]]) {
      await page.goto(base + url, { waitUntil: 'networkidle' });
      await page.waitForTimeout(400);
      await page.screenshot({ path: path.join(out, `${name}-${w}.png`) });
      await page.screenshot({ path: path.join(out, `${name}-${w}-full.png`), fullPage: true });
    }
    await page.goto(base + '/', { waitUntil: 'networkidle' });
    const values = await page.evaluate(() => {
      const ci = document.getElementById('hs-checkin')._flatpickr;
      const co = document.getElementById('hs-checkout')._flatpickr;
      const d = new Date(); d.setDate(d.getDate() + 30);
      const e = new Date(d); e.setDate(e.getDate() + 2);
      ci.setDate(d, true); ci.close(); co.setDate(e, true); co.close();
      const sel = document.getElementById('hs-apt');
      return { ci: ci.input.value, co: co.input.value, apt: sel.options[sel.selectedIndex].text,
        aptFits: sel.scrollWidth <= sel.clientWidth };
    });
    await page.waitForTimeout(300);
    await page.locator('#heroSearch').screenshot({ path: path.join(out, `hero-search-dates-${w}.png`) });
    console.log(`${w}: ${JSON.stringify(values)}`);
    await context.close();
  }
} finally {
  await browser.close();
}
