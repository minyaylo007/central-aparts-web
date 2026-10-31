/* ============================================================
   Central Square Apartments — v2
   Shared: scroll reveal · nav + hero parallax · FAQ · lightbox · hero search
   Booking page: Flatpickr (blocked-date aware) · price calc · staged form
                 · quick-pick · sticky bar · querystring prefill
   ============================================================ */

(function () {
  'use strict';

  var prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ---------------------------------------------------------
     Utilities
     --------------------------------------------------------- */
  function parseLocalDate(s) { var p = s.split('-'); return new Date(+p[0], +p[1] - 1, +p[2]); }
  function fmt(d) {
    var y = d.getFullYear();
    var m = String(d.getMonth() + 1).padStart(2, '0');
    var day = String(d.getDate()).padStart(2, '0');
    return y + '-' + m + '-' + day;
  }
  // «пт, 23 жовт.» — як у віджеті квартири (аудит 21.10, п.2): лише відображення в полі
  function humanDay(d, ua) {
    try { return d.toLocaleDateString(ua ? 'uk-UA' : 'en-GB', { weekday: 'short', day: 'numeric', month: 'short' }); }
    catch (e) { return fmt(d); }
  }
  function fmt_uah(n) { return n.toLocaleString('uk-UA') + ' грн'; }
  function daysDiff(a, b) { return Math.round((b - a) / 86400000); }
  function clamp(n, lo, hi) { return Math.max(lo, Math.min(hi, n)); }
  // keep in sync with TOURIST_TAX_PER_GUEST_NIGHT in initBooking
  var TOURIST_TAX = 48;
  // 31.08: <html lang> тепер 'uk' (валідний BCP-47); скрипт приймає обидва
  var RAW_LANG = document.documentElement.lang;
  var PAGE_UA = (RAW_LANG === 'uk' || RAW_LANG === 'ua');
  // Купон із SMS: ?coupon=1 запамʼятовується на сесію — прапор переживає
  // будь-який шлях головна → квартира (обзор 31.08)
  var WANT_COUPON = false;
  try {
    if (new URLSearchParams(location.search).get('coupon') === '1') sessionStorage.setItem('cs_coupon', '1');
    WANT_COUPON = sessionStorage.getItem('cs_coupon') === '1';
  } catch (e) {}
  var _cb = document.getElementById('coupon-banner');
  if (WANT_COUPON && _cb) _cb.hidden = false;
  // UX telemetry (01.09): GTM dataLayer + sendBeacon → /api/ux (CloudWatch). No PII. measureShift = how many px the
  // «Оплатити» button moved because of fn() — the number the owner can only describe as «скачет».
  function track(ev, data) {
    var p = Object.assign({ event: 'cs_' + ev, apt: (typeof APT_CODE !== 'undefined' ? APT_CODE : ''), vw: window.innerWidth, lang: RAW_LANG }, data || {});
    try { (window.dataLayer = window.dataLayer || []).push(p); } catch (e) {}
    try { if (navigator.sendBeacon) navigator.sendBeacon('/api/ux', JSON.stringify(p)); } catch (e) {}
  }
  // hidden tab: the browser freezes it, .then runs on return — ms would be minutes (22.10: 97892 ms, server < 0.5 s)
  var nHidden = 0;
  document.addEventListener('visibilitychange', function () { if (document.hidden) nHidden++; });
  function elapsed(t0, h0) { return (h0 === nHidden && !document.hidden) ? { ms: Math.round(performance.now() - t0) } : { bg: 1 }; }
  function measureShift(label, fn) {
    var a = document.getElementById('btn-book'); var before = a ? a.getBoundingClientRect().top : 0;
    fn();
    requestAnimationFrame(function () { var d = a ? Math.round(a.getBoundingClientRect().top - before) : 0; if (Math.abs(d) > 2) track('widget_shift', { label: label, px: d }); });
  }
  function nightWord(n, lang) {
    if (lang === 'ua') {
      if (n % 10 === 1 && n % 100 !== 11) return 'ніч';
      if (n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20)) return 'ночі';
      return 'ночей';
    }
    return n === 1 ? 'night' : 'nights';
  }

  // ---- Календарі Flatpickr: спільне для пошуку головної і віджета квартири ----
  // (розбори 30.10 на стенді: docs/PATTERNS.md §8b — головна, §8c — квартира)
  function isPastDay(d) { return d < new Date(new Date().setHours(0, 0, 0, 0)); }
  // заїзд обрано, виїзд ні → підказка й підсвічене поле виїзду кажуть, що далі
  // (на телефоні календар виїзду сам не відкривається)
  function nextStepHint(hintEl, fieldEl) {
    return function (waiting) {
      if (hintEl) hintEl.hidden = !waiting;
      if (fieldEl) fieldEl.classList.toggle('is-next', waiting);
    };
  }
  // тап по закритому дню Flatpickr ковтає мовчки — пояснюємо рядком у самому календарі;
  // reasonFor(date) дає текст причини. Ховаємо при відкритті, гортанні й виборі
  function explainDisabledDays(fp, reasonFor) {
    var el = document.createElement('div');
    el.className = 'fp-hint';
    el.setAttribute('role', 'status');
    el.hidden = true;
    fp.calendarContainer.appendChild(el);
    fp.daysContainer.addEventListener('click', function (e) {
      var day = e.target.closest && e.target.closest('.flatpickr-day');
      if (!day || !day.classList.contains('flatpickr-disabled') || !day.dateObj) return;
      el.textContent = reasonFor(day.dateObj) || '';
      el.hidden = !el.textContent;
    });
    function hide() { el.hidden = true; }
    ['onOpen', 'onMonthChange', 'onChange'].forEach(function (k) { fp.config[k].push(hide); });
  }
  // низький телефон (висота ~560): над полем місця нема, і календар під ним виходить за низ
  // екрана. Підкручуємо сторінку рівно на нестачу (не вище за верх календаря). Flatpickr
  // ставить календар уже після onOpen, тому міряємо в наступному кадрі — і за style.top
  // (координати сторінки), а не getBoundingClientRect: той бачить зсув анімації появи
  function fitOnPhone(fp) {
    fp.config.onOpen.push(function () {
      if (window.innerWidth >= 768) return;
      requestAnimationFrame(function () {
        var cal = fp.calendarContainer;
        var top = parseFloat(cal.style.top);
        if (!fp.isOpen || isNaN(top)) return;
        top -= window.pageYOffset; // у вікні
        var over = top + cal.offsetHeight - window.innerHeight + 8;
        if (over > 0) window.scrollBy(0, Math.min(over, top - 8));
      });
    });
  }

  /* =========================================================
     SHARED — runs on every page
     ========================================================= */

  // ---- Scroll reveal ----
  function initReveal() {
    var els = document.querySelectorAll('.reveal');
    if (!els.length) return;
    if (!('IntersectionObserver' in window) || prefersReduced) {
      els.forEach(function (el) { el.classList.add('is-in'); });
      return;
    }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) { e.target.classList.add('is-in'); io.unobserve(e.target); }
      });
    }, { threshold: 0.12, rootMargin: '0px 0px -8% 0px' });
    els.forEach(function (el) { io.observe(el); });
  }

  // ---- Nav scroll state + hero parallax (single rAF) ----
  function initScroll() {
    var nav = document.getElementById('siteNav');
    var heroBg = document.querySelector('.hero-bg');
    var canParallax = heroBg && !prefersReduced && window.matchMedia('(pointer:fine)').matches;
    var ticking = false;
    function update() {
      var y = window.scrollY || window.pageYOffset || 0;
      if (nav) { nav.classList.toggle('scrolled', y > 40); }
      if (canParallax && y < window.innerHeight) {
        heroBg.style.transform = 'translate3d(0,' + (y * 0.15) + 'px,0) scale(1.08)';
      }
      ticking = false;
    }
    function onScroll() { if (!ticking) { ticking = true; requestAnimationFrame(update); } }
    window.addEventListener('scroll', onScroll, { passive: true });
    update();
  }

  // ---- FAQ accordion ----
  function initFaq() {
    var questions = document.querySelectorAll('.faq-question');
    questions.forEach(function (btn) {
      btn.addEventListener('click', function () {
        var item = btn.closest('.faq-item');
        var isOpen = item.classList.contains('open');
        document.querySelectorAll('.faq-item.open').forEach(function (i) {
          i.classList.remove('open');
          var q = i.querySelector('.faq-question');
          if (q) q.setAttribute('aria-expanded', 'false');
        });
        if (!isOpen) { item.classList.add('open'); btn.setAttribute('aria-expanded', 'true'); }
      });
    });
  }

  // ---- Меню навбару й закриття flash (замість bootstrap.bundle.js, 22.10) ----
  // Лише те, що робив Bootstrap JS: data-bs-toggle="collapse" перемикає .show на цілі
  // (+ aria-expanded і .collapsed на кнопці), data-bs-dismiss="alert" прибирає повідомлення.
  function initBsLite() {
    document.querySelectorAll('[data-bs-toggle="collapse"]').forEach(function (btn) {
      var target = document.querySelector(btn.getAttribute('data-bs-target'));
      if (!target) return;
      btn.classList.add('collapsed');
      btn.setAttribute('aria-expanded', 'false');
      btn.addEventListener('click', function () {
        var open = target.classList.toggle('show');
        btn.classList.toggle('collapsed', !open);
        btn.setAttribute('aria-expanded', open ? 'true' : 'false');
      });
    });
    document.addEventListener('click', function (e) {
      var btn = e.target.closest && e.target.closest('[data-bs-dismiss="alert"]');
      if (!btn) return;
      var alert = btn.closest('.alert');
      if (alert) alert.remove();
    });
  }

  // ---- Lightbox (gallery) ----
  function initLightbox() {
    var slides = document.querySelectorAll('.apt-hero-swiper .swiper-slide:not(.swiper-slide-duplicate) .gallery-photo');
    var urls = Array.prototype.map.call(slides, function (p) { return p.getAttribute('src'); });
    if (!urls.length) return;

    var box = null, imgEl = null, counterEl = null, idx = 0, lastFocused = null;
    // підписи — з translations.py через LIGHTBOX_TXT у public_apartment.html (мова сторінки)
    if (typeof LIGHTBOX_TXT === 'undefined') window.LIGHTBOX_TXT = {};
    function ltxt(k) { return LIGHTBOX_TXT[k] || ''; }
    var nameEl = document.querySelector('.apt-detail-name');
    var altBase = nameEl ? nameEl.textContent.trim() : ltxt('photo');
    function focusables() { return box ? Array.prototype.slice.call(box.querySelectorAll('button')) : []; }

    function build() {
      box = document.createElement('div');
      box.className = 'lightbox';
      box.setAttribute('role', 'dialog');
      box.setAttribute('aria-modal', 'true');
      box.setAttribute('aria-label', altBase);
      box.innerHTML =
        '<button class="lightbox-close" type="button">&times;</button>' +
        '<button class="lightbox-nav lightbox-prev" type="button">&#8249;</button>' +
        '<img class="lightbox-img" alt="">' +
        '<button class="lightbox-nav lightbox-next" type="button">&#8250;</button>' +
        '<div class="lightbox-counter"></div>';
      box.querySelector('.lightbox-close').setAttribute('aria-label', ltxt('close'));
      box.querySelector('.lightbox-prev').setAttribute('aria-label', ltxt('prev'));
      box.querySelector('.lightbox-next').setAttribute('aria-label', ltxt('next'));
      document.body.appendChild(box);
      imgEl = box.querySelector('.lightbox-img');
      counterEl = box.querySelector('.lightbox-counter');
      box.querySelector('.lightbox-close').addEventListener('click', close);
      box.querySelector('.lightbox-prev').addEventListener('click', function (e) { e.stopPropagation(); nav(-1); });
      box.querySelector('.lightbox-next').addEventListener('click', function (e) { e.stopPropagation(); nav(1); });
      box.addEventListener('click', function (e) { if (e.target === box) close(); });
    }
    function render() {
      imgEl.src = urls[idx];
      imgEl.alt = altBase + ' — ' + ltxt('photoOf').replace('{n}', idx + 1).replace('{total}', urls.length);
      counterEl.textContent = (idx + 1) + ' / ' + urls.length;
    }
    function open(i) {
      if (!box) build();
      lastFocused = document.activeElement;
      idx = i; render();
      // фокус — лише коли вікно вже видиме: на display:none focus() не спрацьовує
      requestAnimationFrame(function () {
        box.classList.add('open');
        var f = focusables(); if (f.length) f[0].focus();
      });
      document.body.style.overflow = 'hidden';
    }
    function close() {
      if (!box) return;
      box.classList.remove('open');
      document.body.style.overflow = '';
      if (lastFocused && lastFocused.focus) lastFocused.focus();
    }
    function nav(d) { idx = (idx + d + urls.length) % urls.length; render(); }

    document.querySelectorAll('.gallery-photo').forEach(function (p) {
      p.addEventListener('click', function () {
        var i = urls.indexOf(p.getAttribute('src'));
        open(i < 0 ? 0 : i);
      });
    });
    var gc = document.getElementById('galleryCount');
    if (gc) gc.addEventListener('click', function () { open(0); });
    // collage "view all photos" button (and any future opener)
    document.querySelectorAll('[data-lightbox-open]').forEach(function (b) {
      b.addEventListener('click', function () { open(0); });
    });
    document.addEventListener('keydown', function (e) {
      if (!box || !box.classList.contains('open')) return;
      if (e.key === 'Escape') { close(); return; }
      if (e.key === 'ArrowLeft') { nav(-1); return; }
      if (e.key === 'ArrowRight') { nav(1); return; }
      if (e.key === 'Tab') {
        var f = focusables(); if (!f.length) return;
        var first = f[0], last = f[f.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    });
  }

  // ---- Hero quick-search (home) ----
  function initHeroSearch() {
    var form = document.getElementById('heroSearch');
    if (!form) return;
    var elCI = document.getElementById('hs-checkin');
    var elCO = document.getElementById('hs-checkout');
    var elCount = document.getElementById('hs-count');
    var elMinus = document.getElementById('hs-minus');
    var elPlus = document.getElementById('hs-plus');
    var elApt = document.getElementById('hs-apt'); // in-form apartment choice
    var guests = 2, pickCI = null, pickCO = null;
    // "Забронювати" pressed before dates were picked: guide through both
    // calendars and continue the search automatically once dates are complete
    var pendingSearch = false;
    var locale = (typeof flatpickr !== 'undefined' && PAGE_UA && flatpickr.l10ns && flatpickr.l10ns.uk) ? flatpickr.l10ns.uk : 'default';
    // 22.10: у полях — «пт, 23 жовт.», як у формі квартири. Значення поля тепер лише для ока;
    // дату (ISO) беремо з selectedDates календаря — у пошук, прогрів і URL іде тільки вона.
    function iso(fp) { return fp && fp.selectedDates.length ? fmt(fp.selectedDates[0]) : ''; }
    function showHuman(dates, str, fp) {
      if (fp && fp.selectedDates.length) fp.input.value = humanDay(fp.selectedDates[0], PAGE_UA);
    }

    // 30.10 (розбір на стенді, docs/PATTERNS.md §8b): на телефоні після заїзду календар виїзду
    // сам не відкривається — підказка під формою й підсвічене поле кажуть, що далі
    var showNextStep = nextStepHint(document.getElementById('hs-hint'), elCO.closest('.hsearch-field'));
    function updateNextHint() { showNextStep(!!iso(pickCI) && !iso(pickCO)); }
    // закритий день у пошуку — минулий або (у виїзду) не пізніше заїзду
    function heroReason(d) {
      return form.getAttribute(isPastDay(d) ? 'data-hint-past' : 'data-hint-before-checkin');
    }

    function submitSearch() {
      if (typeof form.requestSubmit === 'function') form.requestSubmit();
      else form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    }

    // Warm the booking target EARLY — on check-in pick, while the visitor is
    // still choosing checkout. Heats the Lambda container and its server-side
    // caches; by navigation time the same container serves the page fast.
    // (Warming at checkout-pick raced the auto-submit and cold-started a
    // second container — the navigation must not overlap the warm fetch.)
    var warmed = '';
    function warmTarget() {
      var ci = iso(pickCI);
      if (!elApt || !elApt.value || !ci) return;
      var key = elApt.value + '|' + ci;
      if (warmed === key) return; // once per selection
      warmed = key;
      try {
        fetch('/apartment/' + encodeURIComponent(elApt.value) +
              '?check_in=' + encodeURIComponent(ci),
              { credentials: 'same-origin' }).catch(function () {});
      } catch (e) { /* warming is best-effort */ }
    }

    if (typeof flatpickr !== 'undefined') {
      pickCI = flatpickr(elCI, {
        minDate: 'today', dateFormat: 'Y-m-d', disableMobile: true, locale: locale,
        showMonths: window.innerWidth >= 768 ? 2 : 1, onValueUpdate: showHuman,
        onChange: function (d) {
          if (d.length && pickCO) {
            pickCO.set('minDate', new Date(d[0].getTime() + 86400000));
            // desktop auto-chains to checkout; on phones only when the visitor
            // explicitly started from the book button (intent is clear)
            if (!iso(pickCO) && (window.innerWidth >= 768 || pendingSearch)) pickCO.open();
          }
          if (d.length) warmTarget(); // heat the target while checkout is being chosen
          updateNextHint();
        },
        onClose: function () { if (!iso(pickCI)) pendingSearch = false; } // dismissed — cancel intent
      });
      pickCO = flatpickr(elCO, {
        minDate: 'today', dateFormat: 'Y-m-d', disableMobile: true, locale: locale,
        showMonths: window.innerWidth >= 768 ? 2 : 1, onValueUpdate: showHuman,
        onChange: function (d) {
          updateNextHint();
          // both dates picked after a book-button press → continue the search, no second click
          if (d.length && pendingSearch && iso(pickCI) && iso(pickCO)) {
            pendingSearch = false;
            submitSearch();
          }
        },
        onClose: function () { if (!iso(pickCO)) pendingSearch = false; }
      });
      explainDisabledDays(pickCI, heroReason);
      explainDisabledDays(pickCO, heroReason);
      fitOnPhone(pickCI);
      fitOnPhone(pickCO);
    }
    if (elApt) elApt.addEventListener('change', function () { warmed = ''; warmTarget(); });

    // стеля гостей — з сервера (data-max, app.GUESTS_MAX); «+» на ній не мовчить, а пояснює (30.10)
    var hsMax = (elPlus && parseInt(elPlus.parentNode.getAttribute('data-max'), 10)) || 4;
    var elGuestsHint = document.getElementById('hs-guests-hint');
    function syncHsGuests() {
      elCount.textContent = guests;
      if (elMinus) elMinus.disabled = guests <= 1;
      if (elPlus) elPlus.setAttribute('aria-disabled', guests >= hsMax ? 'true' : 'false');
      if (elGuestsHint && guests < hsMax) elGuestsHint.hidden = true;
    }
    if (elMinus) elMinus.addEventListener('click', function () { guests = clamp(guests - 1, 1, hsMax); syncHsGuests(); });
    if (elPlus) elPlus.addEventListener('click', function () {
      if (guests >= hsMax) { if (elGuestsHint) elGuestsHint.hidden = false; return; }
      guests++; syncHsGuests();
    });
    if (elCount) syncHsGuests();

    // ---- Live availability: badge each card, then FILTER the list ----
    // Search result behaves like the big booking sites: only free apartments
    // stay visible; if nothing is free we say so honestly and keep all cards.
    var availCache = {};
    var availEpoch = 0; // rapid consecutive searches: only the latest one may paint
    var elSummary = document.getElementById('availSummary');
    var elSummaryFree = document.getElementById('availSummaryFree');
    var elSummaryNone = document.getElementById('availSummaryNone');
    var elAvailCount = document.getElementById('availCount');
    var elAvailReset = document.getElementById('availReset');

    function validRanges(data) {
      return Array.isArray(data) && data.every(function (r) {
        return r && typeof r.from === 'string' && typeof r.to === 'string';
      });
    }
    function clearFilter() {
      document.querySelectorAll('.apt-row--hidden').forEach(function (c) { c.classList.remove('apt-row--hidden'); });
      if (elSummary) elSummary.hidden = true;
      if (elSummaryFree) elSummaryFree.hidden = true;
      if (elSummaryNone) elSummaryNone.hidden = true;
      if (elAvailReset) elAvailReset.hidden = true;
    }
    if (elAvailReset) elAvailReset.addEventListener('click', clearFilter);

    // mark busy options "— зайнято" + auto-move selection to the first free one
    function updateAptSelect(known) {
      if (!elApt) return;
      var busyWord = elApt.getAttribute('data-busy-word') || '';
      var statusById = {};
      known.forEach(function (r) { statusById[r.id] = r.busy; });
      var firstFree = null;
      Array.prototype.forEach.call(elApt.options, function (opt) {
        if (!opt.getAttribute('data-base')) opt.setAttribute('data-base', opt.textContent);
        var base = opt.getAttribute('data-base');
        var st = statusById[opt.value]; // true=busy, false=free, undefined=unknown
        opt.disabled = st === true;
        opt.textContent = st === true ? base + ' — ' + busyWord : base;
        if (st === false && firstFree === null) firstFree = opt.value;
      });
      var sel = elApt.selectedOptions && elApt.selectedOptions[0];
      if (sel && sel.disabled && firstFree !== null) elApt.value = firstFree;
    }

    function updateAvailability(ci, co, g) {
      var badges = document.querySelectorAll('[data-apt-avail]');
      if (!badges.length) return;
      var epoch = ++availEpoch;
      function resetAll() { badges.forEach(function (b) { b.hidden = true; }); clearFilter(); }
      if (!ci || !co) { resetAll(); return; }
      var a = parseLocalDate(ci), b = parseLocalDate(co);
      var nights = daysDiff(a, b);
      if (!(nights >= 1)) { resetAll(); return; } // also catches NaN
      var lang = PAGE_UA ? 'ua' : 'en';
      clearFilter();

      var checks = Array.prototype.map.call(badges, function (badge) {
        var card = badge.closest('.apt-row');
        var link = card && card.querySelector('.apt-detail-link');
        var id = link && link.getAttribute('data-apt');
        var txt = badge.querySelector('.apt-avail-text');
        if (!id || !txt) return Promise.resolve(null);
        var priceEl = card.querySelector('.price-value');
        var price = priceEl ? parseInt(priceEl.textContent.replace(/\D/g, ''), 10) || 0 : 0;

        function freeText(total) {
          return badge.getAttribute('data-free') + ' · ' + nights + ' ' + nightWord(nights, lang)
            + ' · ' + total.toLocaleString('uk-UA') + (lang === 'ua' ? ' грн разом' : ' UAH total');
        }

        function paint(ranges) {
          if (epoch !== availEpoch) return null; // a newer search took over
          var busy = ranges.some(function (r) {
            return a < parseLocalDate(r.to) && b > parseLocalDate(r.from);
          });
          var icon = badge.querySelector('i');
          badge.classList.toggle('is-free', !busy);
          badge.classList.toggle('is-busy', busy);
          if (icon) icon.className = busy ? 'bi bi-x-circle' : 'bi bi-check-circle';
          if (busy || !price) {
            txt.textContent = busy ? badge.getAttribute('data-busy') : badge.getAttribute('data-free');
          } else {
            // instant estimate from today's price; the server quote (true total,
            // same code as the charge) replaces it as soon as it arrives
            txt.textContent = freeText(nights * price + nights * g * TOURIST_TAX);
            fetch('/api/quote/' + encodeURIComponent(id) +
                  '?check_in=' + ci + '&check_out=' + co + '&guests=' + g)
              .then(function (r) { if (!r.ok) throw new Error('quote ' + r.status); return r.json(); })
              .then(function (q) {
                if (epoch !== availEpoch) return;
                if (q && typeof q.total === 'number') txt.textContent = freeText(q.total);
              })
              .catch(function () {}); // estimate stays — never a dead badge
          }
          badge.hidden = false;
          return { card: card, id: id, busy: busy };
        }

        if (availCache[id]) { return Promise.resolve(paint(availCache[id])); }
        return fetch('/api/availability/' + encodeURIComponent(id))
          .then(function (r) { if (!r.ok) throw new Error('availability ' + r.status); return r.json(); })
          .then(function (data) {
            if (!validRanges(data)) throw new Error('bad payload'); // validate BEFORE caching
            availCache[id] = data;
            return paint(data);
          })
          .catch(function () {
            // no false promises: unknown status — badge hidden, card stays visible
            if (epoch === availEpoch) badge.hidden = true;
            return null;
          });
      });

      return Promise.all(checks).then(function (results) {
        if (epoch !== availEpoch || !elSummary) return null;
        var known = results.filter(Boolean);
        if (!known.length) return null; // every check failed — leave the list untouched
        updateAptSelect(known);
        var free = known.filter(function (r) { return !r.busy; });
        var busyCards = known.filter(function (r) { return r.busy; });
        var totalCards = badges.length;
        if (free.length) {
          // hide booked apartments — visitor sees only what they can book
          busyCards.forEach(function (r) { r.card.classList.add('apt-row--hidden'); });
          if (elAvailCount) {
            var one = elSummary.getAttribute('data-apt-one') || '';
            var few = elSummary.getAttribute('data-apt-few') || '';
            elAvailCount.textContent = free.length + ' ' + (free.length === 1 ? one : few);
          }
          elSummaryFree.hidden = false;
          elSummaryNone.hidden = true;
          if (elAvailReset) elAvailReset.hidden = !busyCards.length;
          elSummary.hidden = false;
        } else if (busyCards.length === totalCards) {
          // EVERY apartment known-busy: honest note, keep all cards visible
          elSummaryFree.hidden = true;
          elSummaryNone.hidden = false;
          if (elAvailReset) elAvailReset.hidden = true;
          elSummary.hidden = false;
        }
        // free=0 but some statuses unknown → badges tell the story, no summary
        return {
          freeIds: free.map(function (r) { return r.id; }),
          busyCount: busyCards.length,
          totalCount: totalCards
        };
      });
    }

    var searching = false;
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var ci = iso(pickCI), co = iso(pickCO);
      // no dead clicks: a missing date opens its calendar and the search
      // auto-continues once both dates are picked (pendingSearch)
      function scrollToList() {
        var t = document.getElementById('apartments');
        if (t) t.scrollIntoView({ behavior: prefersReduced ? 'auto' : 'smooth', block: 'start' });
      }
      if (!ci) {
        if (pickCI) { pendingSearch = true; pickCI.open(); } else { scrollToList(); }
        return;
      }
      if (!co) {
        if (pickCO) { pendingSearch = true; pickCO.open(); } else { scrollToList(); }
        return;
      }
      if (searching) return;

      var params = new URLSearchParams();
      params.set('check_in', ci);
      params.set('check_out', co);
      params.set('guests', guests);
      params.set('book', '1'); // survive the href rewrite below — apartment page auto-scrolls to the widget
      var qs = params.toString();
      try { sessionStorage.setItem('cs_search', qs); } catch (err) {}
      // carry the dates into each apartment link (button and card photos) so the visitor lands pre-priced
      document.querySelectorAll('.apt-detail-link, .apt-photo-link').forEach(function (a) {
        var base = a.getAttribute('href').split('?')[0];
        a.setAttribute('href', base + (qs ? '?' + qs : ''));
      });

      searching = true;
      var btn = form.querySelector('.hsearch-submit');
      var icon = btn ? btn.querySelector('i') : null;
      if (btn) btn.classList.add('is-loading');
      if (icon) icon.className = 'bi bi-arrow-repeat hs-spin';

      function goTo(id) {
        window.location.href = '/apartment/' + encodeURIComponent(id) + '?' + qs;
      }
      function done(summary) {
        searching = false;
        if (btn) btn.classList.remove('is-loading');
        if (icon) icon.className = 'bi bi-search';
        var chosen = elApt && elApt.value;
        if (summary && summary.freeIds) {
          // selection was auto-moved to a free apartment by updateAptSelect
          if (chosen && summary.freeIds.indexOf(chosen) !== -1) { goTo(chosen); return; }
          if (summary.freeIds.length) { goTo(summary.freeIds[0]); return; }
          // nothing free (or chosen unknown-busy mix) → list + honest note
        } else if (chosen) {
          // availability unknown (API hiccup) — never dead-end: the apartment
          // page itself shows real availability in its calendar
          goTo(chosen);
          return;
        }
        scrollToList();
      }

      var p = updateAvailability(ci, co, guests);
      if (p && typeof p.then === 'function') {
        p.then(done).catch(function () { done(null); });
      } else {
        done(null);
      }
    });
  }

  /* =========================================================
     BOOKING WIDGET — apartment detail only
     ========================================================= */
  function initBooking() {
    if (typeof APT_PRICE === 'undefined') return;

    var TOURIST_TAX_PER_GUEST_NIGHT = 48; // UAH — matches backoffice default
    var pickerCI = null, pickerCO = null, checkin = null, checkout = null, guests = 2;
    // auto-opening the second calendar makes the page jump on phones — desktop only
    var autoOpenCheckout = window.innerWidth >= 768;

    var elCheckinInput = document.getElementById('picker-checkin');
    var elCheckoutInput = document.getElementById('picker-checkout');
    var elCheckin = document.getElementById('f-check-in');
    var elCheckout = document.getElementById('f-check-out');
    var elGuestsInput = document.getElementById('f-guests');
    var elGuestDisplay = document.getElementById('guest-display');
    var elGuestMinus = document.getElementById('guest-minus');
    var elGuestPlus = document.getElementById('guest-plus');
    var elNightsLabel = document.getElementById('pb-nights-label');
    var elNightsPrice = document.getElementById('pb-nights-price');
    var elTax = document.getElementById('pb-tax');
    var elTotal = document.getElementById('pb-total');
    var elScarcity = document.getElementById('pb-scarcity');
    var elCouponToggle = document.getElementById('coupon-toggle');
    var elCouponBody = document.getElementById('coupon-body');
    var elCouponInput = document.getElementById('coupon-phone-input');
    var elCouponApply = document.getElementById('coupon-apply');
    var elCouponStatus = document.getElementById('coupon-status');
    var elCouponRow = document.getElementById('pb-coupon-row');
    var elCouponAmount = document.getElementById('pb-coupon');
    var elCouponHidden = document.getElementById('f-coupon-phone');
    var elCouponExpected = document.getElementById('f-coupon-expected');
    var elBtnBook = document.getElementById('btn-book');
    var elHint = document.getElementById('widget-hint');
    var elStickyBar = document.getElementById('stickyBookBar');
    var elWidget = document.getElementById('booking-widget');
    var elForm = document.getElementById('booking-form');
    var elIdemKey = document.getElementById('f-idempotency-key');
    var elStickyPrice = document.getElementById('stickyPrice');
    var elStickyUnit = document.getElementById('stickyUnit');
    var elStickyBtn = document.getElementById('stickyBtn');

    if (elForm) elForm.classList.add('staged');
    var elStage2 = document.querySelector('.widget-stage-2');
    var elBreakdown = document.getElementById('price-breakdown');
    var elBookRow = document.getElementById('pb-booking-row');
    var elBookVal = document.getElementById('pb-booking');
    var elSaveRow = document.getElementById('pb-save-row');
    var elSaveVal = document.getElementById('pb-save');
    var elNightsSub = document.getElementById('pb-nights-sub');
    var elAnnounce = document.getElementById('pb-announce');
    var elCouponPrompt = document.getElementById('coupon-prompt');
    var elBtnBookTxt = document.getElementById('btn-book-txt');
    var lang_ua = (typeof LANG !== 'undefined' && LANG === 'ua');
    var stickySuppress = function (on) { if (elStickyBar) elStickyBar.classList.toggle('kbd', !!on); };

    var blockedRanges = (typeof BLOCKED_DATES !== 'undefined' ? (BLOCKED_DATES || []) : []).map(function (b) {
      return { from: parseLocalDate(b.from), to: parseLocalDate(b.to) };
    });
    function rangeBlocked(a, b) { return blockedRanges.some(function (r) { return a < r.to && b > r.from; }); }

    function nightLabel(n) {
      if (lang_ua) {
        if (n % 10 === 1 && n % 100 !== 11) return 'ніч';
        if (n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20)) return 'ночі';
        return 'ночей';
      }
      return n === 1 ? 'night' : 'nights';
    }
    function addDays(d, n) { return new Date(d.getFullYear(), d.getMonth(), d.getDate() + n); } // DST-safe

    var fpLocale = (lang_ua && typeof flatpickr !== 'undefined' && flatpickr.l10ns && flatpickr.l10ns.uk) ? flatpickr.l10ns.uk : 'default';

    // Редизайн 21.10 (аудит п.2): у видимому полі гість бачить «ср, 22 жовт.», а не 2026-10-22.
    // Лише відображення: серверу йде ISO з прихованих #f-check-in / #f-check-out (fmt), dateFormat не міняємо.
    function humanDate(d) {
      try { return d.toLocaleDateString(lang_ua ? 'uk-UA' : 'en-GB', { weekday: 'short', day: 'numeric', month: 'short' }); }
      catch (e) { return fmt(d); }
    }
    function showHuman(fp) {
      if (fp && fp.input && fp.selectedDates.length) fp.input.value = humanDate(fp.selectedDates[0]);
    }
    function onValueHuman(dates, str, fp) { showHuman(fp); }

    // ---- Nightly price hints in the calendar (/api/rates) ----
    // Fetched ONCE per apartment page and kept in memory; on any failure the
    // calendar simply renders without prices (graceful no-op). dayRates also
    // makes the local estimate exact (same pricing seam as /quote).
    var dayRates = null;
    function onDayCreate(dObj, dStr, fp, dayElem) {
      if (!dayRates || !dayElem.dateObj) return;
      if (dayElem.classList.contains('flatpickr-disabled') ||
          dayElem.classList.contains('prevMonthDay') ||
          dayElem.classList.contains('nextMonthDay')) return;
      var price = dayRates[fmt(dayElem.dateObj)];
      if (typeof price !== 'number' || price <= 0) return;
      var tag = document.createElement('span');
      tag.className = 'fp-day-price';
      tag.textContent = price.toLocaleString('uk-UA');
      dayElem.appendChild(tag);
    }
    function loadRates() {
      fetch('/api/rates/' + encodeURIComponent(APT_ID))
        .then(function (r) { if (!r.ok) throw new Error('rates ' + r.status); return r.json(); })
        .then(function (data) {
          if (!data || typeof data.rates !== 'object' || data.rates === null) return;
          dayRates = data.rates;
          [pickerCI, pickerCO].forEach(function (p) {
            if (!p) return;
            if (p.calendarContainer) p.calendarContainer.classList.add('fp-has-prices');
            p.redraw();
          });
        })
        .catch(function () { /* no prices — calendar still fully works */ });
    }

    if (elCheckinInput && typeof flatpickr !== 'undefined') {
      pickerCI = flatpickr(elCheckinInput, {
        minDate: 'today', dateFormat: 'Y-m-d', showMonths: window.innerWidth >= 768 ? 2 : 1,
        locale: fpLocale, disableMobile: true, onDayCreate: onDayCreate, onValueUpdate: onValueHuman,
        disable: [function (date) { return blockedRanges.some(function (b) { return date >= b.from && date < b.to; }); }],
        onOpen: function () { stickySuppress(true); },
        onClose: function () { stickySuppress(false); },
        onChange: function (dates) {
          if (!dates.length) { checkin = null; elCheckin.value = ''; hideBreakdown(); return; }
          checkin = dates[0];
          elCheckin.value = fmt(checkin);
          if (pickerCO) pickerCO.set('minDate', addDays(checkin, 1));
          // the stay is still valid (guest moved check-in by a day) → re-quote IN PLACE, nothing folds (A7)
          if (checkout && checkout > checkin && !rangeBlocked(checkin, checkout)) { updatePrice(); return; }
          checkout = null; elCheckout.value = '';
          if (pickerCO) { pickerCO.clear(false); if (autoOpenCheckout) pickerCO.open(); }   // clear(false) = no second onChange
          markStale();
        }
      });
    }

    if (elCheckoutInput && typeof flatpickr !== 'undefined') {
      pickerCO = flatpickr(elCheckoutInput, {
        minDate: 'today', dateFormat: 'Y-m-d', showMonths: window.innerWidth >= 768 ? 2 : 1,
        locale: fpLocale, disableMobile: true, onDayCreate: onDayCreate, onValueUpdate: onValueHuman,
        disable: [function (date) {
          if (!checkin || date <= checkin) return true;
          return blockedRanges.some(function (b) { return checkin < b.to && date > b.from; });
        }],
        onOpen: function () { stickySuppress(true); },
        onClose: function () { stickySuppress(false); },
        onChange: function (dates) {
          if (!dates.length) { checkout = null; elCheckout.value = ''; hideBreakdown(); return; }
          checkout = dates[0];
          elCheckout.value = fmt(checkout);
          track('dates_set', { nights: daysDiff(checkin, checkout), lead: daysDiff(new Date(new Date().setHours(0, 0, 0, 0)), checkin) });
          updatePrice();
        }
      });
    }

    // 30.10 (розбір на стенді, docs/PATTERNS.md §8c): тап по закритому дню пояснює причину,
    // після заїзду видно крок «виїзд», на низькому телефоні календар не йде за край
    function hintText(name) { return elForm ? elForm.getAttribute('data-hint-' + name) : ''; }
    var showNextStep = null;
    if (pickerCI && pickerCO) {
      explainDisabledDays(pickerCI, function (d) { return hintText(isPastDay(d) ? 'past' : 'booked'); });
      explainDisabledDays(pickerCO, function (d) {
        if (!checkin) return hintText('checkin-first');
        if (isPastDay(d)) return hintText('past');
        return hintText(d <= checkin ? 'before-checkin' : 'through-booked');
      });
      showNextStep = nextStepHint(document.getElementById('widget-next-hint'), elCheckoutInput.closest('.widget-date-half'));
      // після основного onChange: checkin / checkout уже оновлені
      [pickerCI, pickerCO].forEach(function (p) {
        p.config.onChange.push(updateNextStep);
        fitOnPhone(p);
      });
    }
    // і там, де дати ставляться без onChange (чипи ночей, prefill з URL)
    function updateNextStep() { if (showNextStep) showNextStep(!!checkin && !checkout); }

    if (pickerCI || pickerCO) loadRates();

    // ---- Guest counter (A2: capped at 4 like /quote and /book; debounced re-quote) ----
    // стеля — з сервера (data-max, app.GUESTS_MAX). «+» на ній не disabled, а aria-disabled:
    // тап доходить і показує пояснення замість тиші (30.10, розбір (14))
    var GUESTS_MAX = (elGuestPlus && parseInt(elGuestPlus.parentNode.getAttribute('data-max'), 10)) || 4;
    var elGuestMaxHint = document.getElementById('guest-max-hint');
    function syncGuests() {
      elGuestDisplay.textContent = guests; elGuestsInput.value = guests;
      if (elGuestMinus) elGuestMinus.disabled = guests <= 1;
      if (elGuestPlus) elGuestPlus.setAttribute('aria-disabled', guests >= GUESTS_MAX ? 'true' : 'false');
      if (elGuestMaxHint && guests < GUESTS_MAX) elGuestMaxHint.hidden = true;
    }
    function bump(btn) { btn.classList.add('bump'); setTimeout(function () { btn.classList.remove('bump'); }, 200); }
    var guestTimer = null;
    function onGuestsChanged() {
      syncGuests();
      track('guests', { n: guests });
      // same stay → cached server base is exact, only the tax changes: repaint instantly, ONE confirming quote after the taps stop
      if (lastQuote && lastStay === stayKey()) {
        var tpn = (typeof lastQuote.tax_per_night === 'number') ? lastQuote.tax_per_night : TOURIST_TAX_PER_GUEST_NIGHT;
        renderPrice(lastQuote.nights, lastQuote.base_amount, lastQuote.nights * guests * tpn, lastQuote);
      }
      clearTimeout(guestTimer); guestTimer = setTimeout(function () { updatePrice(); }, 350);
    }
    if (elGuestMinus) elGuestMinus.addEventListener('click', function () { if (guests > 1) { guests--; bump(elGuestMinus); onGuestsChanged(); } });
    if (elGuestPlus) elGuestPlus.addEventListener('click', function () {
      if (guests >= GUESTS_MAX) { if (elGuestMaxHint) elGuestMaxHint.hidden = false; return; }
      guests++; bump(elGuestPlus); onGuestsChanged();
    });
    syncGuests();

    // ---- Quick-pick nights ----
    document.querySelectorAll('#qp-chips .qp-chip').forEach(function (chip) {
      chip.addEventListener('click', function () {
        var n = parseInt(chip.getAttribute('data-nights'), 10) || 2;
        track('chip', { n: n });
        if (!checkin) { if (pickerCI) pickerCI.open(); return; }
        var co = addDays(checkin, n);
        if (rangeBlocked(checkin, co)) { if (pickerCO) pickerCO.open(); return; }
        if (pickerCO) { pickerCO.setDate(co, false); showHuman(pickerCO); }
        checkout = co; elCheckout.value = fmt(co);
        updateNextStep();
        updatePrice();
      });
    });

    // ---- Returning-guest coupon (A4 helpers + B1 state machine) ----
    // The SERVER is the only truth for the discount. States: IDLE →(Apply) CHECKING → APPLIED | BELOW_MIN | NOT_FOUND | ERROR;
    // a later server quote can move APPLIED/BELOW_MIN → GONE (coupon_* missing while a phone is set). One resolver writes the status.
    var couponPhone = '';
    var pendingVerify = false;                    // an Apply is waiting for the next server quote
    if (typeof COUPON_TXT === 'undefined') window.COUPON_TXT = {};
    function ctxt(k) { return COUPON_TXT[k] || ''; }
    function uaPhoneShape(d) { return (d.length === 12 && d.indexOf('380') === 0) || (d.length === 10 && d.charAt(0) === '0') || d.length === 9; }

    function couponStatus(msg, kind) {
      if (!elCouponStatus) return;
      elCouponStatus.textContent = msg || '';                       // empty text keeps the reserved height — no hidden toggling
      elCouponStatus.className = 'coupon-status' + (kind ? ' is-' + kind : '');
    }
    function couponBusy(on) {
      if (!elCouponApply) return;
      elCouponApply.disabled = !!on; elCouponApply.classList.toggle('is-loading', !!on);
    }
    function openCouponBody() {
      if (!elCouponBody) return;
      if (elCouponBody.hidden) { elCouponBody.hidden = false; requestAnimationFrame(function () { elCouponBody.classList.add('is-open'); }); }
      else elCouponBody.classList.add('is-open');
      if ('inert' in elCouponBody) elCouponBody.inert = false;
      if (elCouponToggle) elCouponToggle.setAttribute('aria-expanded', 'true');
    }
    function closeCouponBody() {
      if (!elCouponBody) return;
      elCouponBody.classList.remove('is-open');
      if ('inert' in elCouponBody) elCouponBody.inert = true;     // collapsed 0fr content must not be tabbable
      if (elCouponToggle) elCouponToggle.setAttribute('aria-expanded', 'false');
    }
    function setCouponPhone(p) {
      couponPhone = p || '';
      if (elCouponHidden) elCouponHidden.value = couponPhone;
      try { if (couponPhone) sessionStorage.setItem('cs_coupon_phone', couponPhone); else sessionStorage.removeItem('cs_coupon_phone'); } catch (e) {}
    }
    // Called from updatePrice() on every SERVER quote (never on the local re-render)
    function resolveCouponStatus(q) {
      if (!couponPhone || !q) return;
      var wasVerify = pendingVerify; pendingVerify = false; couponBusy(false);
      var outcome;
      measureShift('coupon_status', function () {
        if (typeof q.coupon_discount === 'number' && q.coupon_discount !== 0) {
          outcome = 'applied';
          couponStatus(ctxt('applied').replace('{n}', fmt_uah(Math.abs(q.coupon_discount))), 'ok');
          var wPhone = document.getElementById('w-phone');
          if (wPhone && !wPhone.value) wPhone.value = couponPhone;   // booking phone == coupon phone for practically every guest
        } else if (q.coupon_applied === false && q.coupon_min_total) {
          outcome = 'below_min';
          var gap = q.coupon_min_total - (q.base_amount + q.tourist_tax);
          couponStatus(ctxt('minTotal').replace('{n}', q.coupon_min_total.toLocaleString('uk-UA')).replace('{gap}', fmt_uah(gap)), 'err');
        } else {
          // quote WITHOUT coupon_* while a phone is set: unknown phone (on Apply) or redeemed/expired since → drop it honestly
          outcome = wasVerify ? 'not_found' : 'gone';
          setCouponPhone('');
          couponStatus(ctxt(outcome === 'not_found' ? 'notFound' : 'gone'), 'err');
        }
      });
      track('coupon_result', { outcome: outcome });
    }
    function applyCoupon() {
      if (!elCouponInput) return;
      var phone = (elCouponInput.value || '').trim();
      var digits = phone.replace(/\D/g, '');
      if (!uaPhoneShape(digits)) { couponStatus(ctxt('shortPhone'), 'err'); return; }
      var prevPhone = couponPhone;
      couponStatus('');
      couponBusy(true);
      if (checkin && checkout) {
        // dates known → ONE round trip: /api/quote?coupon_phone= carries the verdict
        // (coupon_discount = applied; coupon_applied:false + coupon_min_total = below min; neither = not found)
        setCouponPhone(phone);
        pendingVerify = true;
        updatePrice(function (q) {
          if (q) return;                              // resolveCouponStatus() already ran inside updatePrice
          pendingVerify = false; couponBusy(false);
          setCouponPhone(prevPhone);                  // network/5xx: keep whatever was valid before, say so
          couponStatus(ctxt('error'), 'err');
          track('coupon_result', { outcome: 'error' });
        });
        return;
      }
      // no dates yet → eligibility only; the −500 line is added by the first quote (server re-validates)
      fetch('/api/coupon/check', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ phone: phone }) })
        .then(function (r) { if (!r.ok) throw new Error('coupon ' + r.status); return r.json(); })
        .then(function (data) {
          if (data && data.eligible === true) { setCouponPhone(phone); couponStatus(ctxt('foundPickDates'), 'ok'); }
          else { setCouponPhone(''); couponStatus(ctxt('notFound'), 'err'); }
        })
        .catch(function () { couponStatus(ctxt('error'), 'err'); track('coupon_result', { outcome: 'error' }); })
        .then(function () { couponBusy(false); });
    }
    if (WANT_COUPON) {
      if (elCouponPrompt) elCouponPrompt.hidden = false;           // SMS deep link before dates: say where the field is (C6)
      if (elCouponBody) openCouponBody();                           // field open, NO programmatic focus (A5)
    }
    if (elCouponToggle && elCouponBody) {
      elCouponToggle.addEventListener('click', function () {
        if (elCouponBody.classList.contains('is-open')) { closeCouponBody(); return; }
        track('coupon_open');
        measureShift('coupon_open', openCouponBody);
        if (elCouponInput) {
          elCouponInput.focus({ preventScroll: true });             // synchronous → iOS still shows the keyboard, but no scroll yank
          setTimeout(function () { elCouponInput.scrollIntoView({ block: 'center', behavior: prefersReduced ? 'auto' : 'smooth' }); }, 420); // after the 0.4 s expand
        }
      });
    }
    if (elCouponApply) elCouponApply.addEventListener('click', applyCoupon);
    if (elCouponInput) elCouponInput.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); applyCoupon(); } });
    // Restore across reload / the other apartment page / the 409 round trip — the first quote re-verifies server-side
    try {
      var qsp = new URLSearchParams(location.search);
      if (qsp.get('coupon_reset') === '1') sessionStorage.removeItem('cs_coupon_phone');
      var storedPhone = sessionStorage.getItem('cs_coupon_phone');
      if (storedPhone && elCouponInput) {
        elCouponInput.value = storedPhone;
        couponPhone = storedPhone; if (elCouponHidden) elCouponHidden.value = storedPhone;   // prefill()'s quote carries it
        openCouponBody();
        couponStatus(ctxt('foundPickDates'), 'ok');   // replaced by the resolver as soon as a quote lands
      }
    } catch (e) {}

    // ---- Price (A2/A9/C1/C3) ----
    // Totals come from the SERVER (/api/quote — same code as the charge). The local estimate is painted ONLY for a
    // new stay; for the same dates (guests ±, coupon, re-check) the last server quote stays on screen (aria-busy dimmed).
    var quoteEpoch = 0;
    var icSent = false; // одна подія InitiateCheckout на відвідування
    var lastQuote = null, lastStay = '';   // last SERVER quote + the stay ('ci|co') it priced
    var lastEstimateBase = 0;

    function stayKey() { return fmt(checkin) + '|' + fmt(checkout); }

    // Exact local estimate: /api/rates is the SAME pricing seam as /quote (get_price_for_date), so the per-night
    // sum matches the server to the hryvnia; falls back to nights × APT_PRICE only while rates are not loaded.
    function localBase(ci, n) {
      if (!dayRates) return n * APT_PRICE;
      var sum = 0;
      for (var i = 0; i < n; i++) {
        var pr = dayRates[fmt(addDays(ci, i))];
        if (typeof pr !== 'number' || pr <= 0) return n * APT_PRICE;
        sum += pr;
      }
      return sum;
    }

    // Optional rows (coupon / Booking / Ваша вигода / scarcity): grid-animated via .is-on; the `hidden` attribute from the
    // initial markup is removed ONCE on first show and never set again (no display:none height snaps).
    function setOpt(el, on) {
      if (!el) return;
      if (on) {
        if (el.hidden) { el.hidden = false; requestAnimationFrame(function () { el.classList.add('is-on'); }); }
        else el.classList.add('is-on');
      } else {
        el.classList.remove('is-on');
      }
    }

    function renderPrice(nights, base, tax, q) {
      // server-verified coupon line (−500) — never computed locally
      var coupon = (q && typeof q.coupon_discount === 'number') ? q.coupon_discount : 0;
      var total = base + tax + coupon;
      // coupon_expected: "1" exactly while the LAST rendered quote contains the discount — the backoffice then
      // fails closed (409) instead of silently charging full price if the coupon dies before /book
      if (elCouponExpected) elCouponExpected.value = (coupon !== 0) ? '1' : '';
      elNightsLabel.textContent = nights + ' ' + nightLabel(nights);
      if (elNightsSub) {
        if (q && !q.uniform) {
          elNightsSub.textContent = q.min_night.toLocaleString('uk-UA') + '–' + q.max_night.toLocaleString('uk-UA') + (lang_ua ? ' грн / ніч' : ' UAH / night');
        } else {
          var per = (q && q.nightly && q.nightly.length) ? q.nightly[0].price : Math.round(base / nights);
          elNightsSub.textContent = fmt_uah(per) + (lang_ua ? ' / ніч' : ' / night');
        }
      }
      elNightsPrice.textContent = fmt_uah(base);
      elTax.textContent = fmt_uah(tax);
      elTotal.innerHTML = '<b>' + fmt_uah(total) + '</b>';
      // optional rows change ONLY on a server render — the estimate never collapses them
      if (q) {
        if (coupon !== 0 && elCouponAmount) elCouponAmount.textContent = '−' + fmt_uah(Math.abs(coupon));
        setOpt(elCouponRow, coupon !== 0);
        var bt = (typeof q.booking_total === 'number') ? q.booking_total : 0;   // LISTED Booking room price (price1, before Genius) — server-computed
        var showBt = bt > base;
        if (showBt) {
          var saveAbs = bt - base - coupon;                                     // coupon is negative → the −500 is part of the saving
          var pct = Math.round(saveAbs / bt * 100);
          if (elBookVal) elBookVal.innerHTML = '<s>' + fmt_uah(bt) + '</s>';
          if (elSaveVal) elSaveVal.textContent = '−' + fmt_uah(saveAbs) + ' (' + pct + '%)';
        }
        setOpt(elBookRow, showBt);
        setOpt(elSaveRow, showBt);
        setOpt(elScarcity, q.only_one_left === true);
      }
      elBtnBook.disabled = groupBlocks();   // spec 108: a selected group waits for its own server quote
      if (elCouponPrompt) elCouponPrompt.hidden = true;
      if (elStage2 && 'inert' in elStage2) elStage2.inert = false;
      if (elForm) { elForm.classList.add('is-priced'); elForm.classList.remove('is-stale'); }
      if (elStickyPrice) elStickyPrice.textContent = (isGroup() && groupQuote ? groupQuote.total : total).toLocaleString('uk-UA');
      if (elStickyUnit) elStickyUnit.textContent = nights + ' ' + nightLabel(nights) + (lang_ua ? ' · разом' : ' · total');
      if (elStickyBtn && typeof BOOK_TXT !== 'undefined') elStickyBtn.textContent = BOOK_TXT.stickyPay;
      return total;
    }

    function updatePrice(cb) {
      groupRefresh();   // spec 108: dates or guests changed → the group (if any) is re-quoted by the server
      if (!checkin || !checkout) { hideBreakdown(); return; }
      var nights = daysDiff(checkin, checkout);
      if (nights < 1) { hideBreakdown(); return; }
      var key = stayKey();
      if (lastQuote && lastStay === key) {
        // same stay: keep the SERVER rows on screen, recompute only the tax locally (server formula: nights × guests × tax_per_night)
        var tpn = (typeof lastQuote.tax_per_night === 'number') ? lastQuote.tax_per_night : TOURIST_TAX_PER_GUEST_NIGHT;
        renderPrice(nights, lastQuote.base_amount, nights * guests * tpn, lastQuote);
      } else {
        // new stay: instant estimate (exact when /api/rates loaded); optional rows are left as they are until the server answers
        lastQuote = null; lastStay = key;
        lastEstimateBase = localBase(checkin, nights);
        renderPrice(nights, lastEstimateBase, nights * guests * TOURIST_TAX_PER_GUEST_NIGHT, null);
      }
      if (elBreakdown) elBreakdown.setAttribute('aria-busy', 'true');
      var epoch = ++quoteEpoch;
      var t0 = performance.now(), h0 = document.hidden ? -1 : nHidden;
      fetch('/api/quote/' + encodeURIComponent(APT_ID) +
            '?check_in=' + fmt(checkin) + '&check_out=' + fmt(checkout) + '&guests=' + guests +
            (couponPhone ? '&coupon_phone=' + encodeURIComponent(couponPhone) : ''))
        .then(function (r) { if (!r.ok) throw new Error('quote ' + r.status); return r.json(); })
        .then(function (q) {
          if (epoch !== quoteEpoch) return; // selection changed meanwhile
          if (!q || typeof q.base_amount !== 'number' || typeof q.tourist_tax !== 'number') throw new Error('quote shape');
          lastQuote = q; lastStay = key;
          var total = 0;
          measureShift('quote', function () { total = renderPrice(q.nights, q.base_amount, q.tourist_tax, q); });
          if (elAnnounce) elAnnounce.textContent = q.nights + ' ' + nightLabel(q.nights) + ', ' + guests + ' ' + (lang_ua ? 'гостей, разом ' : 'guests, total ') + fmt_uah(total) + ((typeof q.coupon_discount === 'number') ? (lang_ua ? ', купон застосовано' : ', coupon applied') : '');
          resolveCouponStatus(q);
          track('quote_result', Object.assign(elapsed(t0, h0), { nights: q.nights, coupon: !!couponPhone, applied: typeof q.coupon_discount === 'number', has_booking: typeof q.booking_total === 'number', est_diff: q.base_amount - (lastEstimateBase || q.base_amount) }));
          if (cb) cb(q);
          if (typeof fbq !== 'undefined' && !icSent) { icSent = true; fbq('track', 'InitiateCheckout', { value: total, currency: 'UAH' }); }
        })
        .catch(function () {
          if (epoch !== quoteEpoch) return;
          // quote unavailable — the estimate stays (server still charges the true total); nothing confirms optional rows for a NEW stay
          if (!lastQuote) { setOpt(elCouponRow, false); setOpt(elBookRow, false); setOpt(elSaveRow, false); setOpt(elScarcity, false); }
          track('quote_fail', elapsed(t0, h0));
          if (cb) cb(null);
          if (typeof fbq !== 'undefined' && !icSent) { icSent = true; fbq('track', 'InitiateCheckout', { value: nights * APT_PRICE + nights * guests * TOURIST_TAX_PER_GUEST_NIGHT, currency: 'UAH' }); }
        })
        .then(function () { if (epoch === quoteEpoch && elBreakdown) elBreakdown.removeAttribute('aria-busy'); });
    }

    // NOTE: tests slice this function up to its FIRST `}` and require "elCouponRow" and "elCouponExpected" inside — keep
    // every statement here brace-free.
    function hideBreakdown() {
      if (elBtnBook) elBtnBook.disabled = true;
      if (elScarcity) setOpt(elScarcity, false);
      if (elCouponRow) setOpt(elCouponRow, false);
      if (elCouponExpected) elCouponExpected.value = '';
      if (elStage2 && 'inert' in elStage2) elStage2.inert = true;
      lastQuote = null; lastStay = '';
      if (elForm) elForm.classList.remove('is-priced');
      if (elForm) elForm.classList.remove('is-stale');
      if (elStickyPrice) elStickyPrice.textContent = (APT_PRICE).toLocaleString('uk-UA');
      if (elStickyUnit) elStickyUnit.textContent = (lang_ua ? 'грн / ніч' : 'UAH / night');
      if (elStickyBtn && typeof BOOK_TXT !== 'undefined') elStickyBtn.textContent = BOOK_TXT.bookNow;
      groupRefresh();
    }
    // dates incomplete but a price was on screen: keep it, dimmed, until the new checkout lands (no fold/unfold) — A7
    function markStale() {
      if (elBtnBook) elBtnBook.disabled = true;
      if (elCouponExpected) elCouponExpected.value = '';
      lastQuote = null; lastStay = '';
      if (elForm && elForm.classList.contains('is-priced')) elForm.classList.add('is-stale'); else hideBreakdown();
      groupRefresh();
    }
    if (elStage2 && 'inert' in elStage2) elStage2.inert = true;   // collapsed 0fr content is not tabbable until priced

    // ---- Spec 108: more apartments for the same dates, one Monobank payment ----
    // Every number shown here is from /api/quote-group (backoffice, same math as /book-group): the rows per
    // apartment, the tourist tax and the total. JS never adds prices up and never uses the 48 constant here.
    var elGroupField = document.getElementById('group-field');
    var elGroupToggle = document.getElementById('group-toggle');
    var elGroupBody = document.getElementById('group-body');
    var elGroupBreakdown = document.getElementById('group-breakdown');
    var elGroupRows = document.getElementById('group-rows');
    var elGroupTax = document.getElementById('group-tax');
    var elGroupTotal = document.getElementById('group-total');
    var elGroupTotalLabel = document.getElementById('group-total-label');
    var elGroupStatus = document.getElementById('group-status');
    var elGroupInputs = document.getElementById('group-inputs');
    var groupRooms = elGroupField ? Array.prototype.slice.call(elGroupField.querySelectorAll('.group-room')) : [];
    var groupEpoch = 0, groupOk = false, groupQuote = null;
    var groupBlocked = {};          // apt id → [{from, to}] from /api/availability (badges only; the quote decides)

    function gtxt(k) { return (typeof GROUP_TXT !== 'undefined' && GROUP_TXT[k]) || ''; }
    function groupRoomName(id) {
      if (id === APT_ID) return gtxt('thisRoom');
      var li = groupRooms.filter(function (x) { return x.getAttribute('data-apt-id') === id; })[0];
      return li ? li.querySelector('.group-room-name').textContent : '';
    }
    function groupSelected() {
      return groupRooms.filter(function (li) { return li.querySelector('.group-room-cb').checked; }).map(function (li) {
        return { id: li.getAttribute('data-apt-id'), guests: parseInt(li.querySelector('.group-room-guests').value, 10) || 2 };
      });
    }
    function isGroup() { return !!(elGroupField && checkin && checkout && groupSelected().length); }
    // true while the group is selected but its server quote is missing, failed or names a booked apartment
    function groupBlocks() { return isGroup() && !groupOk; }
    function groupStatus(msg, err) {
      if (!elGroupStatus) return;
      elGroupStatus.textContent = msg || '';
      elGroupStatus.classList.toggle('is-err', !!err);
    }
    function setGroupMode(on) {
      if (!elForm) return;
      elForm.classList.toggle('is-group', on);
      elForm.setAttribute('action', on ? '/book-group' : '/book/' + APT_ID);
    }
    function markGroupBusy() {
      groupRooms.forEach(function (li) {
        var ranges = groupBlocked[li.getAttribute('data-apt-id')] || [];
        var busy = !!(checkin && checkout) && ranges.some(function (r) { return checkin < r.to && checkout > r.from; });
        var cb = li.querySelector('.group-room-cb');
        li.classList.toggle('is-busy', busy);
        li.querySelector('.group-room-busy').hidden = !busy;
        if (busy) cb.checked = false;
        cb.disabled = busy;
        li.querySelector('.group-room-guests').disabled = !cb.checked;
      });
    }
    function loadGroupAvailability() {
      groupRooms.forEach(function (li) {
        var id = li.getAttribute('data-apt-id');
        if (groupBlocked[id]) return;
        groupBlocked[id] = [];
        fetch('/api/availability/' + encodeURIComponent(id))
          .then(function (r) { if (!r.ok) throw new Error('availability ' + r.status); return r.json(); })
          .then(function (list) {
            groupBlocked[id] = (list || []).map(function (b) { return { from: parseLocalDate(b.from), to: parseLocalDate(b.to) }; });
            var was = groupSelected().length;
            markGroupBusy();
            if (groupSelected().length !== was) groupRefresh();
          })
          .catch(function () { /* no badge — the server quote still names a booked apartment */ });
      });
    }
    function openGroupBody() {
      if (!elGroupBody) return;
      elGroupBody.hidden = false;
      if (elGroupToggle) elGroupToggle.setAttribute('aria-expanded', 'true');
      loadGroupAvailability();
    }
    function renderGroup(q) {
      elGroupRows.textContent = '';
      (q.rooms || []).forEach(function (r) {
        var row = document.createElement('div');
        row.className = 'pb-row group-row';
        var name = document.createElement('span');
        name.textContent = (r.name || groupRoomName(r.apartment_id) || r.apartment || '') + ' ';
        var sub = document.createElement('span');
        sub.className = 'group-row-sub';
        sub.textContent = '· ' + r.guests + ' ' + gtxt('guests');
        name.appendChild(sub);
        var sum = document.createElement('span');
        sum.textContent = fmt_uah(r.base_amount);
        row.appendChild(name); row.appendChild(sum);
        elGroupRows.appendChild(row);
      });
      var n = (q.rooms || []).length;
      elGroupTax.textContent = fmt_uah(q.tourist_tax);
      elGroupTotal.innerHTML = '<b>' + fmt_uah(q.total) + '</b>';
      if (elGroupTotalLabel) elGroupTotalLabel.textContent = gtxt(n >= 5 ? 'totalMany' : 'totalFew').replace('{n}', n);
      elGroupBreakdown.hidden = false;
      if (elStickyPrice) elStickyPrice.textContent = Number(q.total).toLocaleString('uk-UA');
    }
    function groupRefresh() {
      if (!elGroupField) return;
      var dated = !!(checkin && checkout && daysDiff(checkin, checkout) >= 1);
      elGroupField.hidden = !dated;
      markGroupBusy();
      var sel = dated ? groupSelected() : [];
      var epoch = ++groupEpoch;
      groupOk = false; groupQuote = null;
      if (!sel.length) {
        setGroupMode(false);
        if (elGroupBreakdown) elGroupBreakdown.hidden = true;
        groupStatus('');
        if (elBtnBook && dated && elForm && elForm.classList.contains('is-priced') && !submitting) elBtnBook.disabled = false;
        return;
      }
      setGroupMode(true);
      if (elBtnBook) elBtnBook.disabled = true;
      if (elGroupBreakdown) elGroupBreakdown.setAttribute('aria-busy', 'true');
      groupStatus(gtxt('counting'));
      var ids = [APT_ID].concat(sel.map(function (s) { return s.id; }));
      var gs = [guests].concat(sel.map(function (s) { return s.guests; }));
      fetch('/api/quote-group?apartment_ids=' + ids.map(encodeURIComponent).join(',') +
            '&check_in=' + fmt(checkin) + '&check_out=' + fmt(checkout) + '&guests=' + gs.join(','))
        .then(function (r) { if (!r.ok) throw new Error('quote-group ' + r.status); return r.json(); })
        .then(function (q) {
          if (epoch !== groupEpoch) return;
          if (!q || !q.rooms || typeof q.total !== 'number' || typeof q.tourist_tax !== 'number') throw new Error('quote-group shape');
          groupQuote = q;
          renderGroup(q);
          var busy = q.unavailable || [];
          if (busy.length) {
            var names = busy.map(function (id) {
              var r = (q.rooms || []).filter(function (x) { return x.apartment_id === id; })[0];
              return (r && r.name) || groupRoomName(id) || gtxt('thisRoom');
            });
            groupStatus(gtxt('unavailable').replace('{names}', names.join(', ')), true);
            return;
          }
          groupOk = true;
          groupStatus(elCouponHidden && elCouponHidden.value ? gtxt('couponNote') : '');
          if (elBtnBook && !submitting) elBtnBook.disabled = false;
          track('group_quote', { rooms: ids.length, nights: q.nights });
        })
        .catch(function () {
          if (epoch !== groupEpoch) return;
          if (elGroupBreakdown) elGroupBreakdown.hidden = true;
          groupStatus(gtxt('error'), true);
        })
        .then(function () { if (epoch === groupEpoch && elGroupBreakdown) elGroupBreakdown.removeAttribute('aria-busy'); });
    }
    // submit in group mode: the current apartment first (main request), then the ticked ones
    function fillGroupInputs() {
      if (!elGroupInputs) return;
      elGroupInputs.textContent = '';
      if (!isGroup()) return;
      [{ id: APT_ID, guests: guests }].concat(groupSelected()).forEach(function (s) {
        [['room', s.id], ['room_guests', String(s.guests)]].forEach(function (kv) {
          var inp = document.createElement('input');
          inp.type = 'hidden'; inp.name = kv[0]; inp.value = kv[1];
          elGroupInputs.appendChild(inp);
        });
      });
    }
    if (elGroupToggle) elGroupToggle.addEventListener('click', function () {
      if (elGroupBody.hidden) { openGroupBody(); return; }
      elGroupBody.hidden = true;
      elGroupToggle.setAttribute('aria-expanded', 'false');
    });
    groupRooms.forEach(function (li) {
      li.querySelector('.group-room-cb').addEventListener('change', groupRefresh);
      li.querySelector('.group-room-guests').addEventListener('change', groupRefresh);
    });
    // error round trip from /book-group: ?group=<id>:<guests>,… re-ticks the same apartments
    (function () {
      var spec = new URLSearchParams(window.location.search).get('group');
      if (!spec || !groupRooms.length) return;
      spec.split(',').forEach(function (part) {
        var bits = part.split(':');
        groupRooms.forEach(function (li) {
          if (li.getAttribute('data-apt-id') !== bits[0]) return;
          li.querySelector('.group-room-cb').checked = true;
          var g = clamp(parseInt(bits[1], 10) || 2, 1, GUESTS_MAX);
          li.querySelector('.group-room-guests').value = String(g);
        });
      });
      if (groupSelected().length) openGroupBody();
    })();

    // ---- Idempotency-Key: one UUID per booking attempt (backoffice spec 107) ----
    // NEW on every submit, even if the field is already filled (bfcache «back» keeps the
    // old value). The browser re-sending THIS POST after a dropped connection carries the
    // same body, hence the same key — exactly what the backoffice needs.
    function newUuid() {
      var c = window.crypto;
      if (c && typeof c.randomUUID === 'function') return c.randomUUID();
      if (c && typeof c.getRandomValues === 'function') {
        var b = c.getRandomValues(new Uint8Array(16));
        b[6] = (b[6] & 0x0f) | 0x40; b[8] = (b[8] & 0x3f) | 0x80;   // version 4, variant 10
        var h = [];
        for (var i = 0; i < 16; i++) h.push((b[i] + 0x100).toString(16).slice(1));
        return h.slice(0, 4).join('') + '-' + h.slice(4, 6).join('') + '-' + h.slice(6, 8).join('') + '-' +
               h.slice(8, 10).join('') + '-' + h.slice(10).join('');
      }
      return '';   // no crypto at all — the site server makes its own uuid4
    }

    // ---- Submit: loading state + double-tap guard (C4) ----
    var submitting = false;
    if (elForm) elForm.addEventListener('submit', function (e) {
      if (submitting || groupBlocks()) { e.preventDefault(); return; }
      fillGroupInputs();   // spec 108: room/room_guests for /book-group; cleared for a single booking
      if (elIdemKey) elIdemKey.value = newUuid();
      submitting = true; elBtnBook.classList.add('is-loading'); elBtnBook.disabled = true;
      if (elBtnBookTxt && typeof BOOK_TXT !== 'undefined') elBtnBookTxt.textContent = BOOK_TXT.creating;
      try { sessionStorage.removeItem('cs_coupon'); } catch (err) {}
      track('submit', { total: isGroup() && groupQuote ? groupQuote.total : lastQuote && lastQuote.total, coupon: !!couponPhone, guests: guests, rooms: isGroup() ? groupSelected().length + 1 : 1 });
    });
    window.addEventListener('pageshow', function () {           // back from Monobank via bfcache
      submitting = false; if (elBtnBook) elBtnBook.classList.remove('is-loading');
      if (elBtnBookTxt && typeof BOOK_TXT !== 'undefined') elBtnBookTxt.textContent = BOOK_TXT.pay;
      if (checkin && checkout && elBtnBook) elBtnBook.disabled = groupBlocks();
    });

    // ---- Sticky mobile bar (A6): shows only while the WHOLE widget is off-screen; hidden while typing / calendar open ----
    if (elStickyBar && elWidget && 'IntersectionObserver' in window) {
      var io = new IntersectionObserver(function (entries) {
        elStickyBar.classList.toggle('visible', !entries[0].isIntersecting);
      }, { threshold: 0, rootMargin: '-76px 0px 0px 0px' });
      io.observe(elWidget);
      elWidget.addEventListener('focusin', function (e) { if (e.target && e.target.matches('input')) stickySuppress(true); });
      elWidget.addEventListener('focusout', function () { setTimeout(function () { if (!elWidget.contains(document.activeElement)) stickySuppress(false); }, 80); });
    }
    function scrollToWidget() { if (elWidget) elWidget.scrollIntoView({ behavior: prefersReduced ? 'auto' : 'smooth', block: 'start' }); }
    // run fn once the smooth scroll has actually ended (scrollend where supported; timer fallback — also covers 'no scroll needed')
    function scrollThen(fn) {
      scrollToWidget();
      var done = false;
      function once() { if (done) return; done = true; fn(); }
      if ('onscrollend' in window) window.addEventListener('scrollend', once, { once: true });
      setTimeout(once, prefersReduced ? 80 : 900);
    }
    // every "book" CTA leads INTO the widget; open the check-in calendar only after the scroll settled (Flatpickr measures position on open)
    function goToBooking() { scrollThen(function () { if (!checkin && pickerCI) pickerCI.open(); }); }
    if (elStickyBtn) elStickyBtn.addEventListener('click', function () {
      track('sticky_cta', { priced: !!(checkin && checkout) });
      if (!(checkin && checkout)) { goToBooking(); return; }
      var nm = document.getElementById('w-name'), ph = document.getElementById('w-phone');
      var target = (nm && !nm.value.trim()) ? nm : (ph && !ph.value.trim()) ? ph : null;
      if (target) { target.scrollIntoView({ behavior: prefersReduced ? 'auto' : 'smooth', block: 'center' }); target.focus({ preventScroll: true }); return; }
      if (elForm && elForm.requestSubmit) elForm.requestSubmit(); else if (elBtnBook) elBtnBook.click();
    });

    // nav "Book now" on the apartment page: go to this page's widget, not back to home
    document.querySelectorAll('.btn-book-nav').forEach(function (btn) {
      btn.addEventListener('click', function (e) {
        e.preventDefault();
        goToBooking();
      });
    });

    // ---- Prefill from querystring / sessionStorage (hero search) ----
    function prefill() {
      var params = new URLSearchParams(window.location.search);
      var ci = params.get('check_in'), co = params.get('check_out'), g = params.get('guests');
      if (!ci && !co && !g) {
        try {
          var stored = sessionStorage.getItem('cs_search');
          if (stored) { var sp = new URLSearchParams(stored); ci = sp.get('check_in'); co = sp.get('check_out'); g = sp.get('guests'); }
        } catch (e) {}
      }
      if (g) { guests = clamp(parseInt(g, 10) || 1, 1, GUESTS_MAX); syncGuests(); }
      if (ci && pickerCI) {
        var ciD = parseLocalDate(ci);
        if (ciD >= new Date(new Date().setHours(0, 0, 0, 0))) {
          pickerCI.setDate(ciD, false); showHuman(pickerCI);
          checkin = ciD; elCheckin.value = ci;
          if (pickerCO) pickerCO.set('minDate', addDays(ciD, 1));
          if (co) {
            var coD = parseLocalDate(co);
            if (coD > ciD && !rangeBlocked(ciD, coD)) {
              pickerCO.setDate(coD, false); showHuman(pickerCO);
              checkout = coD; elCheckout.value = co;
            }
          }
          updateNextStep();
          updatePrice();
        }
      }
      // ?book=1 (Book CTA / hero search / error round trip) or the SMS coupon deep link: ONE orchestrated landing — scroll,
      // then exactly one of {focus coupon input (desktop, dates priced) | open check-in calendar (no dates)}. Never both,
      // never before the scroll ends (A5).
      var wantBook = params.get('book') === '1' || !!document.querySelector('.alert-danger');
      if (wantBook || WANT_COUPON) {
        setTimeout(function () {
          scrollThen(function () {
            if (WANT_COUPON && elCouponInput && checkin && checkout && window.innerWidth >= 768) elCouponInput.focus({ preventScroll: true });
            else if (wantBook && !checkin && pickerCI) pickerCI.open();
          });
        }, 250);
      }
    }
    prefill();
  }

  /* =========================================================
     INIT
     ========================================================= */
  // 22.10: повні аркуші (з ними й Flatpickr) не блокують відмальовку і вмикаються разом за подією
  // fullcss (public_base.html). Календар, створений раніше за свої стилі, малюється на всю ширину —
  // тож віджети з календарем стартують після неї. Аркуші не прийшли — стартують однаково:
  // форма важливіша за вигляд календаря.
  function whenFullCss(fn) {
    var done = false;
    function run() { if (!done) { done = true; fn(); } }
    if (window.__fullCss || !document.querySelector('link[data-full-css]')) return run();
    document.addEventListener('fullcss', run);
    setTimeout(run, 4000); // страховка: календар без стилів краще, ніж без календаря
  }

  function init() {
    initBsLite();
    initReveal();
    initScroll();
    initFaq();
    initLightbox();
    whenFullCss(function () {
      initHeroSearch();
      initBooking();
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
