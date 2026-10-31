// Збірка CSS першого екрана (22.10.2026, гілка feat/fast-first-screen). Запуск — scripts/build_css.sh.
//
//   node tools/build-css.mjs <тека зі знімками scripts/dump_pages.py>
//
// 1. static/vendor/bootstrap-lite.css — з Bootstrap 5.3.3 (e2e/node_modules, MIT) лише правила,
//    чиї класи справді є в шаблонах і static/main.js, + reboot; :root — лише змінні, на які
//    є посилання. Повний bootstrap.min.css з CDN більше не підключається.
// 2. static/critical/<група>.css — правила bootstrap-lite.css + flatpickr + styles.css, що влучають хоч в
//    один елемент першого екрана сторінок групи (GROUPS) на будь-якій із ширин VIEWPORTS
//    (з JS і без).
//    Його вміст іде інлайном у <head>; повні файли вантажаться, не блокуючи відмальовку.
//    Правило береться цілим, зі своїм @media; порядок — як у джерелі, тож каскад той самий.
//    Шапка файлу несе sha256 джерел — tests/test_critical_css.py ловить застарілий файл.
import '../browsers-env.cjs';
import { chromium } from 'playwright';
import { createHash } from 'node:crypto';
import { readFile, writeFile, readdir, mkdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, '..', '..');
const NODE_MODULES = path.resolve(HERE, '..', 'node_modules');
const STATIC = path.join(REPO, 'static');
const ORIGIN = 'http://site.test';

// ширини: телефони (390 — макет, 412 — Lighthouse mobile), планшет, ноутбук, десктоп
const VIEWPORTS = [
  [360, 740], [390, 844], [412, 823], [768, 1024], [1024, 768], [1366, 900], [1440, 900], [1920, 1080],
];
// «перший екран» з запасом: адресний рядок ховається, шрифти й картинки доїжджають
const FOLD = 1.25;

// класи, які ставить сам код (Jinja-вирази, JS-стани), а регулярка по class="" їх не бачить
// критичний CSS — окремо на кожну групу сторінок: футер коротких сторінок і віджет квартири
// не повинні їхати інлайном на головну. Ключ — аргумент critical_css() у шаблоні.
const GROUPS = {
  home: ['home', 'home-en'],
  apartment: ['apartment', 'apartment-dates', 'apartment-flash'],
  other: ['confirmed', 'error'],
};

const CLASS_SAFELIST = ['alert-danger', 'alert-success', 'show', 'collapsing', 'collapsed', 'fade'];

// ---------------------------------------------------------------- розбір CSS (текст як є)

/** Верхній рівень: [{type:'rule', sel, body} | {type:'at', prelude, body, block:bool}] */
function parseBlocks(css) {
  const out = [];
  let i = 0;
  const n = css.length;
  while (i < n) {
    // пропуск пробілів і коментарів
    if (/\s/.test(css[i])) { i++; continue; }
    if (css.startsWith('/*', i)) { const e = css.indexOf('*/', i + 2); i = e < 0 ? n : e + 2; continue; }
    // прелюдія до { або ;
    let j = i; let q = null;
    while (j < n) {
      const c = css[j];
      if (q) { if (c === '\\') j++; else if (c === q) q = null; }
      else if (c === '"' || c === "'") q = c;
      else if (css.startsWith('/*', j)) { const e = css.indexOf('*/', j + 2); j = e < 0 ? n : e + 1; }
      else if (c === '{' || c === ';') break;
      j++;
    }
    const prelude = css.slice(i, j).replace(/\/\*[\s\S]*?\*\//g, '').trim();
    if (css[j] === ';') { out.push({ type: 'at', prelude, body: null, block: false }); i = j + 1; continue; }
    // тіло до парної }
    let depth = 1; let k = j + 1; q = null;
    while (k < n && depth > 0) {
      const c = css[k];
      if (q) { if (c === '\\') k++; else if (c === q) q = null; }
      else if (c === '"' || c === "'") q = c;
      else if (css.startsWith('/*', k)) { const e = css.indexOf('*/', k + 2); k = e < 0 ? n : e + 1; }
      else if (c === '{') depth++;
      else if (c === '}') depth--;
      k++;
    }
    const body = css.slice(j + 1, k - 1);
    if (prelude.startsWith('@')) out.push({ type: 'at', prelude, body, block: true });
    else out.push({ type: 'rule', sel: prelude, body });
    i = k;
  }
  return out;
}

/** Кома верхнього рівня (не всередині :not(...) / :is(...)) */
function splitTop(s, sep) {
  const parts = []; let depth = 0; let q = null; let cur = '';
  for (let i = 0; i < s.length; i++) {
    const c = s[i];
    if (q) { cur += c; if (c === '\\') { cur += s[++i] ?? ''; } else if (c === q) q = null; continue; }
    if (c === '"' || c === "'") { q = c; cur += c; continue; }
    if (c === '(') depth++;
    if (c === ')') depth--;
    if (c === sep && depth === 0) { parts.push(cur); cur = ''; continue; }
    cur += c;
  }
  if (cur.trim()) parts.push(cur);
  return parts.map((p) => p.trim()).filter(Boolean);
}

const compact = (s) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\s+/g, ' ').replace(/\s*([{};])\s*/g, '$1').trim();

function serialize(blocks) {
  return blocks.map((b) => {
    if (b.type === 'rule') return `${compact(b.sel)}{${compact(b.body)}}`;
    if (!b.block) return `${b.prelude};`;
    if (b.children) return `${b.prelude}{${serialize(b.children)}}`;
    return `${b.prelude}{${compact(b.body)}}`;
  }).join('\n');
}

const GROUPING_AT = /^@(media|supports|layer|container)\b/;

/** Рекурсивно: правила всередині @media / @supports — дерево з children */
function tree(css) {
  return parseBlocks(css).map((b) => (b.type === 'at' && b.block && GROUPING_AT.test(b.prelude))
    ? { ...b, children: tree(b.body) } : b);
}

function filterTree(blocks, keepRule, keepAt) {
  const out = [];
  for (const b of blocks) {
    if (b.children) {
      const children = filterTree(b.children, keepRule, keepAt);
      if (children.length) out.push({ ...b, children });
    } else if (b.type === 'rule') {
      const sels = keepRule(b);
      if (sels) out.push(sels === true ? b : { ...b, sel: sels.join(',') });
    } else if (keepAt(b)) out.push(b);
  }
  return out;
}

function allRules(blocks, acc = []) {
  for (const b of blocks) {
    if (b.children) allRules(b.children, acc);
    else if (b.type === 'rule') acc.push(b);
  }
  return acc;
}

// ---------------------------------------------------------------- 1. bootstrap-lite

async function usedClasses() {
  const files = (await readdir(path.join(REPO, 'templates'))).filter((f) => f.endsWith('.html'))
    .map((f) => path.join(REPO, 'templates', f));
  files.push(path.join(STATIC, 'main.js'), path.join(REPO, 'app.py'));
  const used = new Set(CLASS_SAFELIST);
  for (const f of files) {
    const txt = await readFile(f, 'utf8');
    for (const m of txt.matchAll(/class(?:Name)?\s*=\s*["']([^"']+)["']/g)) {
      for (const c of m[1].replace(/\{[{%][\s\S]*?[%}]\}/g, ' ').split(/\s+/)) if (c) used.add(c);
    }
    for (const m of txt.matchAll(/classList\.(?:add|remove|toggle|contains)\(([^)]*)\)/g)) {
      for (const c of m[1].matchAll(/["']([\w-]+)["']/g)) used.add(c[1]);
    }
  }
  return used;
}

function declarations(body) {
  return splitTop(body, ';').map((d) => {
    const i = d.indexOf(':');
    return { prop: d.slice(0, i).trim(), value: d.slice(i + 1).trim() };
  });
}

async function buildBootstrapLite() {
  const src = await readFile(path.join(NODE_MODULES, 'bootstrap/dist/css/bootstrap.css'), 'utf8');
  const { version } = JSON.parse(await readFile(path.join(NODE_MODULES, 'bootstrap/package.json'), 'utf8'));
  const used = await usedClasses();
  const classesOf = (sel) => [...sel.replace(/url\([^)]*\)/g, '').matchAll(/\.(-?[_a-zA-Z][\w-]*)/g)].map((m) => m[1]);

  const keepRule = (b) => {
    const sels = splitTop(b.sel, ',').filter((s) => !/data-bs-theme=dark|\.was-validated/.test(s) &&
      classesOf(s).every((c) => used.has(c)));
    return sels.length ? sels : null;
  };
  // @keyframes — лише ті, на які є посилання; @font-face у Bootstrap нема
  let lite = filterTree(tree(src), keepRule, () => true);
  const text0 = serialize(lite);
  lite = filterTree(lite, () => true, (b) => {
    if (!/^@(-webkit-)?keyframes\s/.test(b.prelude)) return true;
    const name = b.prelude.split(/\s+/)[1];
    return new RegExp(`animation(-name)?:[^;}]*\\b${name}\\b`).test(text0);
  });

  // :root — лише змінні, на які посилаються лишені правила, styles.css, шаблони або main.js
  const ours = [await readFile(path.join(STATIC, 'styles.css'), 'utf8'), await readFile(path.join(STATIC, 'main.js'), 'utf8')];
  for (const f of await readdir(path.join(REPO, 'templates'))) ours.push(await readFile(path.join(REPO, 'templates', f), 'utf8'));
  const rootRules = allRules(lite).filter((r) => /^:root\b/.test(r.sel.trim()));
  const rest = serialize(filterTree(lite, (b) => !rootRules.includes(b), () => true));
  let haystack = rest + ours.join('\n');
  const decls = rootRules.flatMap((r) => declarations(r.body));
  const wanted = new Set();
  for (let changed = true; changed;) {
    changed = false;
    for (const d of decls) {
      if (!d.prop.startsWith('--') || wanted.has(d.prop)) continue;
      if (haystack.includes(`var(${d.prop})`) || haystack.includes(`var(${d.prop},`)) {
        wanted.add(d.prop); haystack += d.value; changed = true;
      }
    }
  }
  for (const r of rootRules) {
    r.body = declarations(r.body).filter((d) => !d.prop.startsWith('--') || wanted.has(d.prop))
      .map((d) => `${d.prop}:${d.value}`).join(';');
  }
  lite = filterTree(lite, (b) => (b.body.trim() ? true : null), () => true);

  const head = `/*! bootstrap-lite: вибірка з Bootstrap v${version} (https://getbootstrap.com/), ` +
    `MIT, (c) The Bootstrap Authors. Згенеровано e2e/tools/build-css.mjs — руками не правити. */\n`;
  const css = head + serialize(lite) + '\n';
  await writeFile(path.join(STATIC, 'vendor', 'bootstrap-lite.css'), css);
  return { css, kept: allRules(lite).length, total: allRules(tree(src)).length, bytes: css.length, srcBytes: src.length };
}

// ---------------------------------------------------------------- 2. critical

const CONTENT_TYPES = { '.css': 'text/css', '.js': 'application/javascript', '.svg': 'image/svg+xml', '.webp': 'image/webp', '.json': 'application/json', '.woff2': 'font/woff2' };

async function serve(route, pagesDir) {
  const url = new URL(route.request().url());
  try {
    if (url.origin === ORIGIN && url.pathname.startsWith('/__page/')) {
      return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: await readFile(path.join(pagesDir, `${url.pathname.slice(8)}.html`)) });
    }
    if (url.origin === ORIGIN && url.pathname.startsWith('/static/')) {
      const file = path.join(STATIC, decodeURIComponent(url.pathname.slice(8)));
      if (!file.startsWith(STATIC + path.sep)) throw new Error('поза static');
      return route.fulfill({ status: 200, contentType: CONTENT_TYPES[path.extname(file)] ?? 'application/octet-stream', body: await readFile(file) });
    }
    if (url.hostname === 'cdn.jsdelivr.net') {
      const m = /^\/npm\/([^@/]+)@[^/]+\/(.+)$/.exec(url.pathname);
      const file = path.join(NODE_MODULES, m[1], m[2]);
      return route.fulfill({ status: 200, contentType: CONTENT_TYPES[path.extname(file)] ?? 'application/octet-stream', body: await readFile(file), headers: { 'access-control-allow-origin': '*' } });
    }
    if (url.origin === ORIGIN && url.pathname.startsWith('/api/')) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: url.pathname.startsWith('/api/rates') ? '{}' : '[]' });
    }
  } catch { /* нижче — порожня відповідь */ }
  const type = route.request().resourceType();
  return route.fulfill({ status: type === 'document' ? 404 : 200, body: '', contentType: type === 'stylesheet' ? 'text/css' : 'application/javascript' });
}

/** Для кожного селектора: чи влучає він у елемент першого екрана (тут і зараз). */
function matchInPage({ selectors, fold }) {
  const vh = window.innerHeight;
  const above = new Set();
  // Видимий елемент — за своєю рамкою. Невидимий (display:none тощо) — лише корінь
  // прихованого піддерева, і за рамкою батька: його правило «сховати» мусить бути в першому
  // кадрі, інакше модалка блимне. Нащадки прихованого не потрібні — їх і так не видно.
  const visible = (e) => !e || e.checkVisibility({ visibilityProperty: false, opacityProperty: false });
  for (const el of document.querySelectorAll('body, body *, html')) {
    if (visible(el)) {
      if (el.getBoundingClientRect().top < vh * fold) above.add(el);
    } else if (visible(el.parentElement) && (!el.parentElement || el.parentElement.getBoundingClientRect().top < vh * fold)) {
      above.add(el);
    }
  }
  // псевдоелементи й динамічні стани прибираємо: правило про :hover кнопки першого екрана — теж його
  const DYN = /::?(before|after|placeholder|selection|marker|first-letter|first-line|backdrop|file-selector-button|-webkit-[\w-]+|-moz-[\w-]+|hover|focus|focus-visible|focus-within|active|visited|link|any-link|target|checked|disabled|enabled|invalid|valid|required|optional|placeholder-shown|autofill|read-only|read-write|indeterminate|default|empty)(?![\w-])(\([^)]*\))?/g;
  return selectors.map((s) => {
    let plain = s.replace(DYN, '').replace(/\(\s*\)/g, '').trim();
    if (!plain || /[>+~]$/.test(plain)) plain = '*';
    plain = plain.replace(/([>+~])\s*$/, '$1 *');
    try {
      for (const el of document.querySelectorAll(plain)) if (above.has(el)) return true;
      return false;
    } catch { return true; } // не розібрали — краще зайве правило, ніж стрибок
  });
}

async function buildCritical(pagesDir, liteCss, flatpickrCss) {
  const stylesCss = await readFile(path.join(STATIC, 'styles.css'), 'utf8');
  // порядок — як у <head>: bootstrap-lite, flatpickr, styles. Flatpickr тут заради правила, що
  // ховає календар (його вставляє JS у кінець body): без нього до приходу повного аркуша
  // календар стояв би внизу сторінки голим списком днів.
  const sources = [liteCss, flatpickrCss, stylesCss];
  const trees = sources.map(tree);
  const rules = trees.flatMap((t) => allRules(t));
  const selectorsOf = new Map(rules.map((r) => [r, splitTop(r.sel, ',')]));
  const flat = [...new Set(rules.flatMap((r) => selectorsOf.get(r)))];
  const hits = Object.fromEntries(Object.keys(GROUPS).map((g) => [g, new Set()]));

  const pages = Object.keys(JSON.parse(await readFile(path.join(pagesDir, 'pages.json'), 'utf8')));
  const groupOf = (name) => Object.keys(GROUPS).find((g) => GROUPS[g].includes(name));
  for (const g of Object.keys(GROUPS)) {
    if (!GROUPS[g].some((n) => pages.includes(n))) throw new Error(`група ${g}: нема жодної сторінки у знімку`);
  }
  const browser = await chromium.launch();
  try {
    for (const js of [false, true]) {
      for (const [width, height] of VIEWPORTS) {
        const context = await browser.newContext({ viewport: { width, height }, javaScriptEnabled: js, deviceScaleFactor: 1, locale: 'uk-UA', timezoneId: 'Europe/Kyiv' });
        await context.route(() => true, (route) => serve(route, pagesDir));
        const page = await context.newPage();
        for (const name of pages) {
          const hit = hits[groupOf(name)];
          if (!hit) throw new Error(`${name}: сторінка без групи в GROUPS`);
          await page.goto(`${ORIGIN}/__page/${name}`, { waitUntil: 'load' });
          if (js) await page.waitForTimeout(150); // onload-перемикачі CSS, Swiper, Flatpickr
          const todo = flat.filter((s) => !hit.has(s));
          const res = await page.evaluate(matchInPage, { selectors: todo, fold: FOLD });
          todo.forEach((s, i) => { if (res[i]) hit.add(s); });
        }
        await context.close();
      }
    }
  } finally {
    await browser.close();
  }

  const hash = createHash('sha256').update(sources.join('\0')).digest('hex').slice(0, 16);
  const out = [];
  for (const [group, hit] of Object.entries(hits)) {
    const keepRule = (b) => (selectorsOf.get(b) ?? splitTop(b.sel, ',')).some((s) => hit.has(s)) || null;
    const keepAt = (b) => /^@font-face/.test(b.prelude); // запасні шрифти з size-adjust — інакше стрибок
    let crit = trees.map((t) => filterTree(t, keepRule, keepAt));
    // @keyframes — ті, на які посилаються відібрані правила
    const text0 = crit.map(serialize).join('\n');
    const frames = trees.map((t) => t.filter((b) => b.type === 'at' && /^@(-webkit-)?keyframes\s/.test(b.prelude)
      && new RegExp(`animation(-name)?:[^;}]*\\b${b.prelude.split(/\s+/)[1]}\\b`).test(text0)));
    crit = crit.map((c, i) => c.concat(frames[i]));
    const css = `/* critical/${group}: перший екран (${GROUPS[group].join(', ')}) з bootstrap-lite.css + flatpickr + styles.css ` +
      `(sources ${hash}). Згенеровано scripts/build_css.sh — руками не правити. */\n${crit.map(serialize).join('\n')}\n`;
    await writeFile(path.join(STATIC, 'critical', `${group}.css`), css);
    out.push({ group, bytes: Buffer.byteLength(css), rules: allRules(crit.flat()).length });
  }
  return { out, total: rules.length, hash };
}

/** Flatpickr CSS — свій файл замість CDN: той самий origin, без окремого з'єднання. */
async function copyFlatpickrCss() {
  const { version } = JSON.parse(await readFile(path.join(NODE_MODULES, 'flatpickr/package.json'), 'utf8'));
  const src = await readFile(path.join(NODE_MODULES, 'flatpickr/dist/flatpickr.min.css'), 'utf8');
  const out = path.join(STATIC, 'vendor', `flatpickr-${version}.min.css`);
  const css = `/*! flatpickr v${version}, MIT, (c) Gregory Petrosyan. Копія з npm — руками не правити. */\n${src.trim()}\n`;
  await writeFile(out, css);
  return { file: path.relative(REPO, out), css };
}

const pagesDir = process.argv[2];
if (!pagesDir) { console.error('usage: node tools/build-css.mjs <pages-dir>'); process.exit(2); }
const flatpickr = await copyFlatpickrCss();
console.log(`${flatpickr.file}: скопійовано`);
const lite = await buildBootstrapLite();
console.log(`bootstrap-lite.css: ${lite.kept}/${lite.total} правил, ${lite.bytes} Б (повний bootstrap.css ${lite.srcBytes} Б)`);
await mkdir(path.join(STATIC, 'critical'), { recursive: true });
const crit = await buildCritical(pagesDir, lite.css, flatpickr.css);
for (const c of crit.out) console.log(`critical/${c.group}.css: ${c.rules}/${crit.total} правил, ${c.bytes} Б, sources ${crit.hash}`);
