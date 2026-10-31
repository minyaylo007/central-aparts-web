/* Своя статистика → POST /t (analytics/). Без cookies, IP і значень полів. */
(function () {
'use strict';
try {
var W = window, D = document, N = navigator, L = location, DE = D.documentElement, now = Date.now, M = Math,
  on = function (t, n, f, c) { t.addEventListener(n, f, c); };
if (W.top !== W || /^\/stats/.test(L.pathname)) return;
var fo = W.fetch, ss = null;
try { ss = W.sessionStorage; } catch (e) {}
var rid = function () {
  var a = new Uint8Array(12), s = '', i;
  try { crypto.getRandomValues(a); } catch (e) { for (i = 0; i < 12; i++) a[i] = M.random() * 256; }
  for (i = 0; i < 12; i++) s += (a[i] % 36).toString(36);
  return s;
};
var sid = null;
try { var o = JSON.parse(ss.getItem('cs_a')); if (o && now() - o.t < 18e5) sid = o.i; } catch (e) {}
sid = sid || rid();
var touch = function () { try { ss.setItem('cs_a', JSON.stringify({ i: sid, t: now() })); } catch (e) {} };
touch();
var pv = rid(), path = L.pathname, q = [], tm = 0, steps = {};
var post = function (u, b, ka) { try { fo.call(W, u, { method: 'POST', body: b, keepalive: ka }).catch(function () {}); } catch (e) {} };
var send = function (b) {
  try { if (N.sendBeacon && N.sendBeacon('/t', b)) return; } catch (e) {}
  post('/t', b, true);
};
var env = function (x) { x.v = 1; x.s = sid; x.p = pv; return JSON.stringify(x); };
var flush = function () {
  clearTimeout(tm); tm = 0;
  while (q.length) send(env({ e: q.splice(0, 50) }));
  touch();
};
var push = function (e) {
  e.u = path; q.push(e);
  if (q.length >= 40) flush(); else if (!tm) tm = setTimeout(flush, 4000);
};
var step = function (s) { if (!steps[s]) { steps[s] = 1; push({ k: 'f', st: s }); } };

// перегляд: домен реферера й utm_*/ref
var qs = {}, ref = '';
try { new URLSearchParams(L.search).forEach(function (v, k) { if (/^utm_|^ref$/.test(k)) qs[k] = v.slice(0, 60); }); } catch (e) {}
try { var rh = new URL(D.referrer).hostname; if (rh !== L.hostname) ref = rh; } catch (e) {}
push({ k: 'pv', w: W.innerWidth, h: W.innerHeight, l: (N.language || '').slice(0, 2), sl: DE.lang, r: ref, q: qs, wd: N.webdriver ? 1 : 0 });
flush();

// кліки; rage: 3 за 1 с поруч; dead (dd): 1 с без реакції після тапу по тому, що обіцяє дію, —
// ACT або cursor:pointer (docs/PATTERNS.md §8a). Тап по декору без реакції — dx, у dead не йде.
var INTER = 'a,button,[role=button],input,select,textarea,label,summary,[data-bs-toggle]',
  ACT = INTER + ',[onclick],[tabindex]:not([tabindex="-1"]),.flatpickr-day,.flatpickr-prev-month,.flatpickr-next-month',
  FIELD = /^(INPUT|SELECT|TEXTAREA)$/;
var sel = function (el) {
  var p = [], n = 0, s, c;
  while (el && el.nodeType === 1 && n++ < 3) {
    s = el.tagName.toLowerCase();
    if (el.id) { p.unshift(s + '#' + el.id); break; }
    c = typeof el.className === 'string' ? el.className.trim().split(/\s+/)[0] : '';
    p.unshift(c ? s + '.' + c : s); el = el.parentElement;
  }
  return p.join('>');
};
var txt = function (el) {
  if (!el || FIELD.test(el.tagName) || el.closest('#booking-form input,#coupon-body,.confirmed-details')) return '';
  return (el.getAttribute('aria-label') || el.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 40);
};
// сторож реакції: свій MutationObserver на кожен тап; прокрутка й /api/* — теж реакція (poke)
var live = [], burst = [], pend = null, pd = null;
var arm = function () {
  if (!W.MutationObserver) return null;
  var w = { m: 0 };
  w.o = new MutationObserver(function () { w.m = 1; });
  w.o.observe(D.body, { childList: true, subtree: true, attributes: true, characterData: true });
  live.push(w); return w;
};
var disarm = function (w) { if (w) { w.o.disconnect(); live = live.filter(function (x) { return x !== w; }); } };
var poke = function () { for (var i = 0; i < live.length; i++) live[i].m = 1; };
// реакція буває вже на pointerdown (Flatpickr відкривається поверх поля) — тоді click іде
// спільному предку, BODY, а DOM до click уже змінився. Ціль і сторож — від pointerdown (§8a).
on(D, 'pointerdown', function (ev) {
  try { if (pd) disarm(pd.w); pd = { el: ev.target, t: now(), w: arm() }; } catch (x) {}
}, true);
// посилання: сервер може думати > 1 с — чекаємо ще 9 с; pagehide (пішли) скасовує
var watchDead = function (e, nav, w) {
  if (!w) return;
  var h0 = L.href;
  setTimeout(function () {
    disarm(w);
    if (w.m || L.href !== h0 || D.visibilityState !== 'visible') return;
    if (!nav) return push(e);
    pend = e; setTimeout(function () { if (pend === e) { pend = null; push(e); } }, 9000);
  }, 1000);
};
on(D, 'click', function (ev) {
  try {
    var t = ev.target; if (!t || t.nodeType !== 1) return;
    var w = null;
    if (pd && now() - pd.t < 1500) {
      if (pd.el && pd.el.nodeType === 1 && pd.el !== t && t.contains(pd.el)) t = pd.el;
      w = pd.w;
    } else if (pd) disarm(pd.w);
    pd = null;
    var it = t.closest(INTER), el = it || t, pw = M.max(DE.scrollWidth, DE.clientWidth) || 1,
      x = ev.pageX / pw, y = ev.pageY / pw, ms = now();
    if (!(x >= 0 && x <= 1 && y >= 0 && y <= 60)) return disarm(w);
    var c = { x: +x.toFixed(4), y: +y.toFixed(4), s: sel(el), r: it ? it.getAttribute('role') || it.tagName.toLowerCase() : '', t: txt(it) },
      cp = function (k) { var o = { k: k }; for (var i in c) o[i] = c[i]; return o; };
    push(cp('c'));
    burst = burst.filter(function (b) { return ms - b.t < 1000 && M.abs(b.x - ev.clientX) < 30 && M.abs(b.y - ev.clientY) < 30; });
    burst.push({ t: ms, x: ev.clientX, y: ev.clientY });
    if (burst.length === 3) { var r = cp('rg'); r.n = 3; push(r); }
    if (el.id === 'group-toggle') step('group_open');
    var href = it && it.tagName === 'A' ? it.getAttribute('href') || '' : '';
    if ((it && (FIELD.test(it.tagName) || it.tagName === 'LABEL' || it.target === '_blank')) || /^(tel|mailto):/.test(href)) return disarm(w);
    var d = cp(t.closest(ACT) || W.getComputedStyle(t).cursor === 'pointer' ? 'dd' : 'dx');
    d.dv = 2;
    watchDead(d, href && href[0] !== '#', w || arm());
  } catch (x) {}
}, true);

// прокрутка, % висоти
var maxs = 0, us = 0;
var sc = function () {
  var m = M.min(100, M.round((W.scrollY + W.innerHeight) * 100 / M.max(DE.scrollHeight, 1)));
  if (m > maxs) maxs = m;
};
on(W, 'scroll', function () { us = 1; poke(); sc(); }, { passive: true });

// поля: час і заповнене чи ні, значення — ніколи
var fel = null, f0 = 0, touched = {};
var fm = function (el) { var f = el.form || el.closest('form'); return f ? f.id || 'form' : 'noform'; };
on(D, 'focusin', function (ev) {
  var t = ev.target;
  if (!t || !t.matches || !t.matches('input,select,textarea') || t.type === 'hidden') return;
  fel = t; f0 = now();
  if (fm(t) === 'booking-form' && /^w-(name|phone)$/.test(t.id)) step('form');
}, true);
on(D, 'focusout', function (ev) {
  var t = ev.target; if (t !== fel) return; fel = null;
  var id = (t.id || t.getAttribute('name') || '').slice(0, 40); if (!/^[A-Za-z]/.test(id)) return;
  var full = /^(checkbox|radio)$/.test(t.type) ? t.checked : !!t.value;
  touched[fm(t)] = id;
  push({ k: 'fd', f: id, fm: fm(t), ms: now() - f0, fl: full ? 1 : 0 });
}, true);
on(D, 'submit', function (ev) {
  var f = ev.target; delete touched[f.id || 'form'];
  if (f.id === 'booking-form') step(/\/book-group/.test(f.action) ? 'group_submit' : 'submit');
  var hd = f.querySelectorAll('#hs-checkin,#hs-checkout'); // головна: лише «обидві дати є»
  if (hd.length > 1 && hd[0].value && hd[1].value) step('home_dates');
  flush();
}, true);

// /api/*: код і час; кроки воронки. Вкладка ховалась — bg:1 без ms (див. clean.py)
var nhid = 0;
if (fo) W.fetch = function (u) {
  var p = fo.apply(this, arguments);
  try {
    var a = new URL(typeof u === 'string' ? u : (u && u.url) || '', L.href), t0 = now(), ap = a.pathname,
      h0 = D.visibilityState === 'visible' ? nhid : -1;
    var done = function (st) {
      var e = { k: 'ap', a: ap, st: st };
      if (h0 === nhid && D.visibilityState === 'visible') e.ms = now() - t0; else e.bg = 1;
      push(e);
    };
    if (a.origin === L.origin && !ap.indexOf('/api/')) {
      poke();
      var grp = ap === '/api/quote-group', qt = /^\/apartment\//.test(path) && (grp || !ap.indexOf('/api/quote/')),
        cp = ap === '/api/coupon/check';
      if (qt && !grp) step('dates');
      if (cp) step('coupon_try');
      p.then(function (r) {
        done(r.status);
        if (r.ok && qt) step(grp ? 'group_quote' : 'price');
        if (r.ok && cp) r.clone().json().then(function (j) { if (j && j.eligible === true) step('coupon_ok'); }, function () {});
      }, function () { done(0); });
    }
  } catch (x) {}
  return p;
};

// помилки JS, LCP/INP/CLS
var nerr = 0, lcp = 0, cls = 0, inp = 0, vsent = 0, fh = D.visibilityState === 'hidden' ? 0 : 1 / 0;
var je = function (m, src, ln) { if (nerr++ < 10) push({ k: 'je', m: String(m).slice(0, 200), src: src, ln: ln }); };
on(W, 'error', function (ev) { if (ev.message) je(ev.message, ev.filename || '', ev.lineno || 0); });
on(W, 'unhandledrejection', function (ev) { var r = ev.reason; je((r && r.message) || r, 'promise', 0); });
var po = function (o, cb) {
  try { o.buffered = true; new PerformanceObserver(function (l) { l.getEntries().forEach(cb); }).observe(o); } catch (e) {}
};
po({ type: 'largest-contentful-paint' }, function (e) { lcp = e.startTime; });
po({ type: 'layout-shift' }, function (e) { if (!e.hadRecentInput) cls += e.value; });
po({ type: 'event', durationThreshold: 40 }, function (e) { if (e.interactionId && e.duration > inp) inp = e.duration; });

// rrweb: ліниво, поля й форма броні під маскою
var rq = [], rseq = 0, recOn = 0;
var recFlush = function (fin) {
  if (!rq.length || rseq > 300) return;
  var b = env({ rec: { q: rseq++, u: path, e: rq.splice(0, rq.length) } });
  if (fin) { if (b.length < 6e4) send(b); return; }
  try {
    if (W.CompressionStream) {
      new Response(new Blob([b]).stream().pipeThrough(new CompressionStream('gzip'))).blob()
        .then(function (z) { post('/t?z=1', z); });
    } else if (b.length < 25e4) post('/t', b);
  } catch (e) {}
};
var startRec = function () {
  if (recOn) return; recOn = 1;
  var s = D.createElement('script');
  s.src = '/static/vendor/rrweb-record-2.1.7.min.js'; s.async = true;
  s.onload = function () {
    try {
      var R = W.rrwebRecord, rec = R && (R.record || (R['default'] || {}).record);
      if (!rec) return;
      rec({
        emit: function (e) { rq.push(e); if (rq.length >= 400) recFlush(); },
        maskAllInputs: true, maskTextSelector: '#booking-form,#coupon-body,.confirmed-details',
        blockSelector: 'iframe', slimDOMOptions: 'all', recordCanvas: false, collectFonts: false,
        sampling: { mousemove: 100, scroll: 150, input: 'last' }
      });
      setTimeout(recFlush, 1500); setInterval(recFlush, 1e4);
    } catch (e) {}
  };
  D.head.appendChild(s);
};
var idle = function () {
  setTimeout(function () { if (W.requestIdleCallback) W.requestIdleCallback(startRec, { timeout: 3000 }); else startRec(); }, 500);
};
if (D.readyState === 'complete') idle(); else on(W, 'load', idle);

// вкладка сховалась / сторінка йде
var hide = function (leaving) {
  try {
    sc(); push({ k: 'sc', m: maxs, hr: M.round(DE.scrollHeight * 100 / (DE.scrollWidth || 1)) / 100, us: us });
    if (!vsent && (lcp || inp || cls)) { vsent = 1; push({ k: 'vt', lcp: M.round(lcp), inp: M.round(inp), cls: +cls.toFixed(4), hb: fh < lcp ? 1 : 0 }); }
    if (leaving) for (var f in touched) push({ k: 'ab', f: touched[f], fm: f });
    flush(); recFlush(true);
  } catch (e) {}
};
on(D, 'visibilitychange', function () { if (D.visibilityState === 'hidden') { nhid++; fh = M.min(fh, performance.now()); hide(0); } });
on(W, 'pagehide', function () { pend = null; hide(1); });
} catch (e) {}
})();
