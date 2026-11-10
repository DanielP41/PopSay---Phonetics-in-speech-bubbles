/*
 * PopSay — content script.
 * Detecta la selección, muestra la burbuja (Shadow DOM cerrado, aislada del CSS
 * de la página) y sincroniza el resaltado con los eventos de voz del service worker.
 */
(function () {
  'use strict';
  if (window.__popsayLoaded) return;
  window.__popsayLoaded = true;

  const FB = globalThis.PopSay;
  const SPEEDS = [
    { value: 0.6, label: 'Lento' },
    { value: 0.8, label: '0.8×' },
    { value: 1, label: '1×' },
    { value: 1.2, label: '1.2×' }
  ];
  const BAR_COUNT = 28;

  let settings = { ...FB.DEFAULT_SETTINGS };
  let cssText = null;
  let host = null;
  let shadow = null;
  let ui = null;           // referencias a nodos de la burbuja abierta
  let trigger = null;      // botoncito del modo "icon"
  let pendingSelection = null;
  let hyphCache = null;

  // Estado de reproducción de la burbuja abierta
  const play = {
    text: '',
    words: [],
    timeline: [],
    sessionId: null,
    awaiting: false,
    state: 'idle',         // idle | loading | playing
    repeat: false,
    estimate: null,        // { offset, length, startedAt, duration, timeline, words }
    raf: 0
  };

  chrome.storage.sync.get(null).then((s) => {
    settings = { ...FB.DEFAULT_SETTINGS, ...s };
  });
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== 'sync') return;
    for (const [k, v] of Object.entries(changes)) settings[k] = v.newValue;
    if (ui) syncControls();
  });

  function saveSetting(key, value) {
    settings[key] = value;
    chrome.storage.sync.set({ [key]: value }).catch(() => {});
  }

  function h(tag, attrs, children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k === 'text') el.textContent = v;
      else if (k === 'html') el.innerHTML = v; // sólo para íconos estáticos propios
      else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? '' : v);
    }
    for (const c of [].concat(children || [])) if (c) el.append(c);
    return el;
  }

  const ICON = {
    speaker: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M11 5 6 9H2v6h4l5 4V5z"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M19 5a10 10 0 0 1 0 14"/></svg>',
    play: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5.5v13a1 1 0 0 0 1.5.86l10.5-6.5a1 1 0 0 0 0-1.72L9.5 4.64A1 1 0 0 0 8 5.5z"/></svg>',
    stop: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>',
    spinner: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" aria-hidden="true" class="spin"><path d="M12 3a9 9 0 1 0 9 9"/></svg>',
    gear: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>',
    close: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>',
    repeat: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m17 2 4 4-4 4"/><path d="M3 11v-1a4 4 0 0 1 4-4h14"/><path d="m7 22-4-4 4-4"/><path d="M21 13v1a4 4 0 0 1-4 4H3"/></svg>',
    grip: '<svg viewBox="0 0 12 12" aria-hidden="true"><g fill="currentColor"><circle cx="10" cy="10" r="1.15"/><circle cx="6" cy="10" r="1.15"/><circle cx="2" cy="10" r="1.15"/><circle cx="10" cy="6" r="1.15"/><circle cx="6" cy="6" r="1.15"/><circle cx="10" cy="2" r="1.15"/></g></svg>',
    copy: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>'
  };

  async function loadCss() {
    if (cssText !== null) return cssText;
    try {
      const res = await fetch(chrome.runtime.getURL('src/bubble.css'));
      cssText = await res.text();
    } catch {
      cssText = '';
    }
    return cssText;
  }

  async function ensureHost() {
    if (host && host.isConnected) return;
    host = document.createElement('popsay-host');
    host.setAttribute('style', 'all: initial; position: fixed; top: 0; left: 0; width: 0; height: 0; z-index: 2147483647;');
    shadow = host.attachShadow({ mode: 'closed' });
    const css = await loadCss();
    if (css) {
      shadow.append(h('style', { text: css }));
    } else {
      shadow.append(h('link', { rel: 'stylesheet', href: chrome.runtime.getURL('src/bubble.css') }));
    }
    (document.body || document.documentElement).append(host);
  }

  function pageIsDark() {
    for (const el of [document.body, document.documentElement]) {
      if (!el) continue;
      const m = getComputedStyle(el).backgroundColor.match(/rgba?\(([^)]+)\)/);
      if (!m) continue;
      const [r, g, b, a = 1] = m[1].split(',').map((x) => parseFloat(x));
      if (a < 0.1) continue;
      const lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
      return lum < 0.5;
    }
    return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
  }

  function themeName() {
    if (settings.theme === 'dark' || settings.theme === 'light') return settings.theme;
    return pageIsDark() ? 'dark' : 'light';
  }

  function isEditable(node) {
    const el = node && node.nodeType === 1 ? node : node && node.parentElement;
    if (!el) return false;
    if (el.closest('input, textarea, select, [contenteditable=""], [contenteditable="true"]')) return true;
    return !!(el.isContentEditable);
  }

  function readSelection() {
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0 || sel.isCollapsed) {
      // Selección dentro de input/textarea (getSelection no la ve)
      const ae = document.activeElement;
      if (ae && (ae.tagName === 'TEXTAREA' || (ae.tagName === 'INPUT' && /^(text|search|url|email)?$/i.test(ae.type)))) {
        const raw = ae.value.slice(ae.selectionStart || 0, ae.selectionEnd || 0);
        const text = FB.normalizeSelection(raw);
        if (text) return { text, rect: ae.getBoundingClientRect(), range: null, editable: true };
      }
      return null;
    }
    const text = FB.normalizeSelection(sel.toString());
    if (!text) return null;
    const range = sel.getRangeAt(0).cloneRange();
    let rect = range.getBoundingClientRect();
    if (!rect || (rect.width === 0 && rect.height === 0)) {
      const rects = range.getClientRects();
      rect = rects.length ? rects[rects.length - 1] : null;
    }
    if (!rect) return null;
    return { text, rect, range, editable: isEditable(sel.anchorNode) };
  }

  function insideOurUi(e) {
    return host && e.composedPath && e.composedPath().includes(host);
  }

  function onPointerUp(e) {
    if (e.button !== undefined && e.button !== 0) return;
    if (insideOurUi(e)) return;
    setTimeout(handleSelectionGesture, 10);
  }

  function onKeyUp(e) {
    if (e.key === 'Escape') {
      if (ui || trigger) {
        closeBubble();
        hideTrigger();
      }
      return;
    }
    if (e.shiftKey || e.key === 'Shift') setTimeout(handleSelectionGesture, 10);
  }

  function handleSelectionGesture() {
    if (FB.hostIsDisabled(location.hostname, settings.disabledSites)) return;
    if (settings.triggerMode === 'shortcut') return;
    const info = readSelection();
    if (!info) {
      hideTrigger();
      return;
    }
    if (ui && info.text === play.text) return; // misma selección, burbuja ya abierta
    if (settings.skipEditable && info.editable) return;
    if (settings.onlyEnglish && !FB.isLikelyEnglish(info.text)) return;
    if (settings.triggerMode === 'icon') showTrigger(info);
    else openBubble(info);
  }

  async function showTrigger(info) {
    await ensureHost();
    hideTrigger();
    pendingSelection = info;
    trigger = h('button', {
      class: 'fb-trigger',
      type: 'button',
      'data-theme': themeName(),
      'aria-label': 'Escuchar pronunciación',
      title: 'Escuchar pronunciación',
      html: ICON.speaker,
      onmousedown: (e) => e.preventDefault(), // no perder la selección
      onclick: () => {
        const sel = pendingSelection;
        hideTrigger();
        if (sel) openBubble(sel, { autoPlay: true });
      }
    });
    const r = info.rect;
    const x = Math.min(window.innerWidth - 40, Math.max(4, r.right + 4));
    const y = Math.min(window.innerHeight - 40, Math.max(4, r.bottom + 4));
    trigger.style.left = x + 'px';
    trigger.style.top = y + 'px';
    shadow.append(trigger);
  }

  function hideTrigger() {
    if (trigger) trigger.remove();
    trigger = null;
    pendingSelection = null;
  }

  async function openBubble(info, opts) {
    opts = opts || {};
    // Que la voz incluida vaya cargando mientras se dibuja la burbuja.
    chrome.runtime.sendMessage({ type: 'warmup' }).catch(() => {});
    await ensureHost();
    closeBubble();
    hideTrigger();

    const { text, truncated } = FB.truncateAtWord(info.text, settings.maxChars);
    play.text = text;
    play.words = FB.splitWords(text);
    play.timeline = FB.buildWordTimeline(play.words);
    play.perWord = null;
    play.details = null;
    play.cur = { index: -1, j: -2, k: -2 };
    play.state = 'idle';
    play.sessionId = null;
    play.awaiting = false;

    const wordEls = play.words.map((w) => h('span', { class: 'fb-word', text: w.word }));
    const bars = Array.from({ length: BAR_COUNT }, (_, i) => {
      const height = 22 + Math.round(70 * Math.abs(Math.sin(i * 1.7) * Math.cos(i * 0.45)));
      const b = h('span', { class: 'fb-bar' });
      b.style.setProperty('--h', height + '%');
      b.style.setProperty('--d', (i % 7) * 0.07 + 's');
      return b;
    });

    const accentBtns = [
      ['en-US', 'US'],
      ['en-GB', 'UK']
    ].map(([value, label]) =>
      h('button', {
        type: 'button',
        class: 'fb-pill',
        'data-value': value,
        'aria-pressed': 'false',
        title: value === 'en-US' ? 'Inglés de EE. UU.' : 'Inglés británico',
        text: label,
        onclick: () => {
          if (settings.accent === value) return;
          saveSetting('accent', value);
          syncControls();
          loadIpa();
          if (play.state !== 'idle') startPlayback();
        }
      })
    );

    const speedBtns = SPEEDS.map((s) =>
      h('button', {
        type: 'button',
        class: 'fb-pill',
        'data-value': String(s.value),
        'aria-pressed': 'false',
        text: s.label,
        onclick: () => {
          saveSetting('rate', s.value);
          syncControls();
          if (play.state !== 'idle') startPlayback();
        }
      })
    );

    const playBtn = h('button', { type: 'button', class: 'fb-play', 'aria-label': 'Reproducir pronunciación', html: ICON.play, onclick: togglePlay });
    const repeatBtn = h('button', {
      type: 'button',
      class: 'fb-pill fb-icon-pill',
      'aria-pressed': 'false',
      title: 'Repetir en bucle',
      html: ICON.repeat + '<span>Repetir</span>',
      onclick: () => {
        play.repeat = !play.repeat;
        syncControls();
      }
    });
    const ipaEl = h('div', { class: 'fb-ipa', 'aria-live': 'polite' });
    const notice = h('div', { class: 'fb-notice', role: 'status' });
    const textEl = h('div', { class: 'fb-text', lang: 'en' }, wordEls);
    const grip = h('div', { class: 'fb-grip', title: 'Arrastrá para cambiar el ancho y el alto máximo del texto · doble clic: automático', html: ICON.grip });

    const card = h('div', { class: 'fb-card', role: 'dialog', 'aria-label': 'Pronunciación en inglés', 'data-theme': themeName() }, [
      h('div', { class: 'fb-head' }, [
        h('div', { class: 'fb-brand' }, [h('span', { class: 'fb-brand-icon', html: ICON.speaker }), h('span', { text: 'PopSay' })]),
        h('div', { class: 'fb-seg', role: 'group', 'aria-label': 'Acento' }, accentBtns),
        h('div', { class: 'fb-head-actions' }, [
          h('button', { type: 'button', class: 'fb-icon', 'aria-label': 'Ajustes', title: 'Ajustes', html: ICON.gear, onclick: () => chrome.runtime.sendMessage({ type: 'open-options' }) }),
          h('button', { type: 'button', class: 'fb-icon', 'aria-label': 'Cerrar', title: 'Cerrar (Esc)', html: ICON.close, onclick: closeBubble })
        ])
      ]),
      h('div', { class: 'fb-panel' }, [
        textEl,
        h('div', { class: 'fb-ipa-row' }, [ipaEl]),
        h('div', { class: 'fb-player' }, [playBtn, h('div', { class: 'fb-wave', 'aria-hidden': 'true' }, bars)])
      ]),
      h('div', { class: 'fb-foot' }, [h('div', { class: 'fb-seg', role: 'group', 'aria-label': 'Velocidad' }, speedBtns), repeatBtn]),
      notice,
      grip
    ]);
    setupGrip(grip, card, textEl);
    // Que los clics dentro de la burbuja no borren la selección de la página.
    card.addEventListener('mousedown', (e) => {
      if (!(e.target instanceof HTMLButtonElement)) e.preventDefault();
    });

    shadow.append(card);
    ui = { card, text: textEl, wordEls, bars, playBtn, repeatBtn, accentBtns, speedBtns, ipaEl, notice, range: info.range, anchorRect: info.rect, resizing: false };
    applySize();

    syncControls();
    position();
    if (truncated) setNotice(`Texto recortado a ${settings.maxChars} caracteres.`);
    if (!FB.isLikelyEnglish(text)) setNotice('No parece texto en inglés; la pronunciación puede sonar rara.');
    loadHyph();
    loadIpa();
    if (opts.autoPlay || settings.autoPlay) startPlayback();
    else playBtn.focus({ preventScroll: true });
  }

  function closeBubble() {
    if (!ui) return;
    if (play.state !== 'idle') chrome.runtime.sendMessage({ type: 'stop' }).catch(() => {});
    stopEstimate();
    play.state = 'idle';
    play.sessionId = null;
    play.awaiting = false;
    play.repeat = false;
    ui.card.remove();
    ui = null;
  }

  /** Ancho de la burbuja y alto del cuadro de texto elegidos por el usuario (0 = automático). */
  function applySize() {
    if (!ui) return;
    const w = Number(settings.bubbleWidth) || 0;
    const th = Number(settings.bubbleTextHeight) || 0;
    ui.card.style.width = w ? Math.min(w, window.innerWidth - 16) + 'px' : '';
    // El alto elegido es un MÁXIMO: la burbuja se ajusta al texto y lo que no entra se desplaza.
    ui.text.style.height = '';
    ui.text.style.maxHeight = th ? th + 'px' : '';
  }

  function setupGrip(grip, card, textEl) {
    grip.addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      e.stopPropagation();
      grip.setPointerCapture(e.pointerId);
      const r = card.getBoundingClientRect();
      const start = { x: e.clientX, y: e.clientY, w: r.width, h: Number(settings.bubbleTextHeight) || textEl.getBoundingClientRect().height, left: r.left };
      if (ui) ui.resizing = true;
      const move = (ev) => {
        const maxW = Math.min(900, window.innerWidth - start.left - 8);
        settings.bubbleWidth = Math.round(Math.max(320, Math.min(maxW, start.w + ev.clientX - start.x)));
        settings.bubbleTextHeight = Math.round(Math.max(48, Math.min(640, start.h + ev.clientY - start.y)));
        applySize();
      };
      const up = () => {
        grip.removeEventListener('pointermove', move);
        grip.removeEventListener('pointerup', up);
        grip.removeEventListener('pointercancel', up);
        if (ui) ui.resizing = false;
        chrome.storage.sync.set({ bubbleWidth: settings.bubbleWidth, bubbleTextHeight: settings.bubbleTextHeight }).catch(() => {});
      };
      grip.addEventListener('pointermove', move);
      grip.addEventListener('pointerup', up);
      grip.addEventListener('pointercancel', up);
    });
    grip.addEventListener('dblclick', (e) => {
      e.preventDefault();
      settings.bubbleWidth = 0;
      settings.bubbleTextHeight = 0;
      applySize();
      position();
      chrome.storage.sync.set({ bubbleWidth: 0, bubbleTextHeight: 0 }).catch(() => {});
    });
  }

  function position() {
    if (!ui || ui.resizing) return;
    const card = ui.card;
    const margin = 8;
    let r = ui.anchorRect;
    if (ui.range) {
      const live = ui.range.getBoundingClientRect();
      if (live && (live.width || live.height)) r = live;
    }
    const w = card.offsetWidth;
    const hgt = card.offsetHeight;
    let left = r.left + r.width / 2 - w / 2;
    left = Math.max(margin, Math.min(window.innerWidth - w - margin, left));
    let top = r.bottom + 10;
    if (top + hgt > window.innerHeight - margin && r.top - hgt - 10 > margin) top = r.top - hgt - 10;
    top = Math.max(margin, Math.min(window.innerHeight - hgt - margin, top));
    card.style.left = Math.round(left) + 'px';
    card.style.top = Math.round(top) + 'px';
    ui.anchorRect = r;
    return r;
  }

  let scrollRaf = 0;
  function onScrollOrResize() {
    if (!ui || scrollRaf) return;
    scrollRaf = requestAnimationFrame(() => {
      scrollRaf = 0;
      if (!ui) return;
      const r = position();
      const offscreen = r.bottom < 0 || r.top > window.innerHeight;
      if (offscreen && play.state === 'idle') closeBubble();
    });
  }

  function syncControls() {
    if (!ui) return;
    for (const b of ui.accentBtns) b.setAttribute('aria-pressed', String(b.dataset.value === settings.accent));
    for (const b of ui.speedBtns) b.setAttribute('aria-pressed', String(Number(b.dataset.value) === Number(settings.rate)));
    ui.repeatBtn.setAttribute('aria-pressed', String(play.repeat));
    ui.card.dataset.state = play.state;
    if (!ui.resizing) applySize();
    ui.card.dataset.showIpa = String(!!settings.showIpa);
    const icon = play.state === 'loading' ? ICON.spinner : play.state === 'playing' ? ICON.stop : ICON.play;
    ui.playBtn.innerHTML = icon;
    ui.playBtn.setAttribute('aria-label', play.state === 'idle' ? 'Reproducir pronunciación' : 'Detener');
  }

  function setNotice(msg) {
    if (!ui) return;
    ui.notice.textContent = msg || '';
    ui.card.classList.toggle('has-notice', !!msg);
    position();
  }

  let noticeTimer = 0;
  function flashNotice(msg) {
    setNotice(msg);
    clearTimeout(noticeTimer);
    noticeTimer = setTimeout(() => setNotice(''), 1800);
  }


  async function loadHyph() {
    if (!hyphCache) {
      try {
        const r = await chrome.runtime.sendMessage({ type: 'hyph' });
        hyphCache = r && r.patterns ? r : { maxlen: 0, patterns: {} };
      } catch {
        hyphCache = { maxlen: 0, patterns: {} };
      }
    }
    if (ui) refreshDetails();
  }

  const mk = (cls, text) => { const e = document.createElement('span'); e.className = cls; e.textContent = text; return e; };

  /** Reparte cada palabra en sílabas (texto) y arma la transcripción palabra por palabra (IPA). */
  function refreshDetails() {
    if (!ui || !hyphCache) return;
    const perWord = play.perWord || play.words.map(() => null);
    play.details = FB.wordDetails(play.words.map((w) => w.word), perWord, hyphCache);
    play.details.forEach((d, i) => {
      const el = ui.wordEls[i];
      el.textContent = '';
      if (d.ortho.length > 1) d.ortho.forEach((t) => el.append(mk('fb-syl', t)));
      else el.textContent = play.words[i].word;
    });
    ui.sylEls = ui.wordEls.map((el) => Array.from(el.children));
    ui.ipaWordEls = null;
    if (play.perWord && ui.ipaEl.dataset.value) {
      ui.ipaEl.textContent = '';
      ui.ipaEl.append('/');
      ui.ipaWordEls = [];
      let first = true;
      play.details.forEach((d, i) => {
        if (!play.perWord[i]) { ui.ipaWordEls[i] = null; return; }
        if (!first) ui.ipaEl.append(' ');
        first = false;
        const w = mk('fb-ipaw', '');
        if (d.ipa && d.ipa.length > 1) d.ipa.forEach((t) => w.append(mk('fb-ipas', t)));
        else w.textContent = play.perWord[i];
        ui.ipaEl.append(w);
        ui.ipaWordEls[i] = w;
      });
      ui.ipaEl.append('/' + (play.ipaApprox ? '  ≈' : ''));
    }
    play.cur = { index: -1, j: -2, k: -2 };
    if (play.state === 'playing' && play.last) highlight(play.last.index, play.last.frac);
  }

  async function loadIpa() {
    if (!ui || !settings.showIpa) return;
    const myText = play.text;
    const myAccent = settings.accent;
    ui.ipaEl.textContent = 'Buscando transcripción…';
    ui.ipaEl.dataset.value = '';
    ui.ipaEl.classList.add('is-muted');
    let res = null;
    try {
      res = await chrome.runtime.sendMessage({ type: 'ipa', text: myText, accent: myAccent });
    } catch {
      res = null;
    }
    if (!ui || play.text !== myText || settings.accent !== myAccent) return;
    if (res && res.ipa) {
      const value = `/${res.ipa}/`;
      play.perWord = Array.isArray(res.perWord) && res.perWord.length === play.words.length ? res.perWord : null;
      play.ipaApprox = !!res.approximate;
      ui.ipaEl.title = res.approximate ? 'Transcripción aproximada (generada por reglas)' : res.source === 'dictionary' ? 'Fuente: Free Dictionary API' : 'Fuente: backend';
      ui.ipaEl.dataset.value = value;
      ui.ipaEl.classList.remove('is-muted');
      ui.ipaEl.textContent = value + (res.approximate ? '  ≈' : '');
      refreshDetails();
    } else {
      ui.ipaEl.textContent = 'Sin transcripción disponible';
      ui.ipaEl.title = '';
    }
    position();
  }

  function togglePlay() {
    if (play.state === 'idle') startPlayback();
    else stopPlayback();
  }

  async function startPlayback() {
    if (!ui) return;
    stopEstimate();
    clearHighlight();
    play.state = 'loading';
    play.awaiting = true;
    play.sessionId = null;
    syncControls();
    let res;
    try {
      res = await chrome.runtime.sendMessage({ type: 'speak', text: play.text, accent: settings.accent, rate: settings.rate });
    } catch (e) {
      res = { ok: false, error: String(e.message || e) };
    }
    if (!ui) return;
    if (!res || !res.ok) {
      play.state = 'idle';
      play.awaiting = false;
      syncControls();
      setNotice((res && res.error) || 'No se pudo reproducir.');
      return;
    }
    play.sessionId = res.sessionId;
    play.awaiting = false;
    if (play.state === 'loading' && res.engine === 'system') {
      play.state = 'playing';
      syncControls();
    }
  }

  function stopPlayback() {
    chrome.runtime.sendMessage({ type: 'stop' }).catch(() => {});
    play.repeat = false;
    finishPlayback();
  }

  function finishPlayback() {
    stopEstimate();
    clearHighlight();
    play.state = 'idle';
    play.sessionId = null;
    syncControls();
  }

  function clearHighlight() {
    if (!ui) return;
    play.last = null;
    for (const el of ui.wordEls) el.classList.remove('is-active', 'is-done', 'is-whole');
    for (const list of ui.sylEls || []) for (const e of list) e.classList.remove('is-now');
    for (const w of ui.ipaWordEls || []) if (w) { w.classList.remove('is-active', 'is-done', 'is-whole'); for (const e of w.children) e.classList.remove('is-now'); }
    play.cur = { index: -1, j: -2, k: -2 };
    setProgress(0);
  }

  /** index = palabra que suena; frac = cuánto de ella ya sonó (0..1) o null si no se sabe. */
  function highlight(index, frac = null) {
    if (!ui) return;
    play.last = { index, frac };
    const d = play.details && play.details[index];
    const { j, k } = FB.sylState(d, frac);
    const cur = play.cur;
    if (cur.index !== index) {
      ui.wordEls.forEach((el, i) => {
        el.classList.toggle('is-active', i === index);
        el.classList.toggle('is-done', i < index);
      });
      (ui.ipaWordEls || []).forEach((w, i) => {
        if (!w) return;
        w.classList.toggle('is-active', i === index);
        w.classList.toggle('is-done', i < index);
      });
      for (const list of ui.sylEls || []) for (const e of list) e.classList.remove('is-now');
      for (const w of ui.ipaWordEls || []) if (w) for (const e of w.children) e.classList.remove('is-now');
      cur.k = cur.j = -2;
      const t = ui.text;
      const el = ui.wordEls[index];
      if (el && t.scrollHeight > t.clientHeight) {
        const top = el.offsetTop;
        if (top < t.scrollTop || top + el.offsetHeight > t.scrollTop + t.clientHeight) t.scrollTop = Math.max(0, top - t.clientHeight / 3);
      }
      cur.index = index;
    }
    if (cur.k !== k) {
      const list = (ui.sylEls || [])[index] || [];
      list.forEach((e, n) => e.classList.toggle('is-now', n === k));
      ui.wordEls[index].classList.toggle('is-whole', k < 0);
      cur.k = k;
    }
    if (cur.j !== j) {
      const w = (ui.ipaWordEls || [])[index];
      if (w) {
        Array.from(w.children).forEach((e, n) => e.classList.toggle('is-now', n === j));
        w.classList.toggle('is-whole', j < 0);
      }
      cur.j = j;
    }
  }

  function fracIn(timeline, i, p) {
    const e = timeline[i];
    return e && e.to > e.from ? Math.max(0, Math.min(1, (p - e.from) / (e.to - e.from))) : null;
  }

  function setProgress(p) {
    if (!ui) return;
    const lit = Math.round(Math.max(0, Math.min(1, p)) * BAR_COUNT);
    ui.bars.forEach((b, i) => b.classList.toggle('is-lit', i < lit));
  }

  // Estimación de tiempos para voces sin eventos de palabra
  function startEstimate(offset, length, rate, knownDuration, weights) {
    stopEstimate();
    const words = play.words.map((w, i) => ({ ...w, i })).filter((w) => w.start >= offset && w.start < offset + length);
    if (!words.length) return;
    const timeline = FB.buildWordTimeline(words, weights);
    const syllables = words.reduce((a, w) => a + (FB.countSyllables(w.word) || 0.5), 0);
    // Duración real si el motor la conoce (voz incluida); si no, estimada por sílabas.
    const duration = knownDuration > 0 ? knownDuration : (0.25 + syllables * 0.2) / (rate || 1);
    const startedAt = performance.now();
    play.estimate = { words, timeline, duration, startedAt };
    const tick = () => {
      const est = play.estimate;
      if (!est || !ui) return;
      const p = Math.min(0.98, (performance.now() - est.startedAt) / 1000 / est.duration);
      const local = FB.wordIndexAtProgress(est.timeline, p);
      const global = est.words[local].i;
      highlight(global, fracIn(est.timeline, local, p));
      setProgress((est.words[0].i + p * est.words.length) / Math.max(1, play.words.length));
      play.raf = requestAnimationFrame(tick);
    };
    play.raf = requestAnimationFrame(tick);
  }

  function stopEstimate() {
    cancelAnimationFrame(play.raf);
    play.raf = 0;
    play.estimate = null;
  }

  function onTtsEvent(msg) {
    if (!ui) return;
    if (play.awaiting) {
      play.sessionId = msg.sessionId;
      play.awaiting = false;
    }
    if (msg.sessionId !== play.sessionId) return;

    switch (msg.kind) {
      case 'notice':
        flashNotice(msg.message);
        break;
      case 'chunk-start':
        play.state = 'playing';
        syncControls();
        if (!msg.wordEvents) startEstimate(msg.offset, msg.length, msg.rate, msg.duration, msg.weights);
        else {
          const idx = FB.wordIndexAtChar(play.words, msg.offset);
          highlight(idx);
        }
        break;
      case 'word': {
        const idx = FB.wordIndexAtChar(play.words, msg.charIndex);
        highlight(idx);
        setProgress(idx / Math.max(1, play.words.length));
        break;
      }
      case 'start':
      case 'progress': {
        if (play.state !== 'playing') {
          play.state = 'playing';
          syncControls();
        }
        const p = msg.progress || 0;
        const wi = FB.wordIndexAtProgress(play.timeline, p);
        highlight(wi, fracIn(play.timeline, wi, p));
        setProgress(p);
        break;
      }
      case 'end':
        finishPlayback();
        if (play.repeat) setTimeout(() => ui && play.repeat && play.state === 'idle' && startPlayback(), 450);
        break;
      case 'error':
        play.repeat = false;
        finishPlayback();
        setNotice(msg.message || 'Error de reproducción.');
        break;
      default:
        break;
    }
  }

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (!msg) return false;
    if (msg.type === 'tts-event') {
      onTtsEvent(msg);
      return false;
    }
    if (msg.type === 'open-bubble') {
      const info = readSelection();
      const text = FB.normalizeSelection(msg.text || (info && info.text) || '');
      if (!text) return false; // este frame no tiene la selección
      const rect =
        (info && info.rect) || new DOMRect(window.innerWidth / 2 - 1, window.innerHeight / 3, 2, 2);
      openBubble({ text, rect, range: info ? info.range : null, editable: false }, { autoPlay: true });
      sendResponse({ ok: true });
      return false;
    }
    return false;
  });

  document.addEventListener('mouseup', onPointerUp, true);
  document.addEventListener('keyup', onKeyUp, true);
  document.addEventListener(
    'mousedown',
    (e) => {
      if (insideOurUi(e)) return;
      hideTrigger();
      if (ui) closeBubble();
    },
    true
  );
  window.addEventListener('scroll', onScrollOrResize, true);
  window.addEventListener('resize', onScrollOrResize);
})();
