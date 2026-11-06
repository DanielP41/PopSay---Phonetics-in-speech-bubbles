/*
 * Offscreen document: reproduce audio para el service worker (que no puede).
 * - Motor "local": Kokoro en un Worker (WASM), 100 % offline. Genera por fragmentos y
 *   empieza a sonar con el primero mientras prepara el siguiente.
 * - Motor "neural": audio del backend propio (opcional).
 */
'use strict';

const audioEl = document.getElementById('player');
const blobCache = new Map();
const MAX_BLOBS = 40;

let sessionId = null;
let ticker = null;

function send(kind, extra, sid) {
  const id = sid != null ? sid : sessionId;
  if (id == null) return;
  chrome.runtime.sendMessage({ type: 'offscreen-event', sessionId: id, kind, ...(extra || {}) }).catch(() => {});
}

let worker = null;
let reqSeq = 0;
const pending = new Map();
let modelReady = false;

function getWorker() {
  if (worker) return worker;
  worker = new Worker(chrome.runtime.getURL('src/engine/kokoro-worker.js'), { type: 'module' });
  worker.onmessage = ({ data }) => {
    const p = pending.get(data.id);
    if (!p) return;
    pending.delete(data.id);
    data.ok ? p.resolve(data) : p.reject(new Error(data.error));
  };
  worker.onerror = (e) => {
    for (const p of pending.values()) p.reject(new Error(e.message || 'Error en el worker de voz'));
    pending.clear();
    worker = null;
    modelReady = false;
  };
  return worker;
}

function call(type, payload) {
  const id = ++reqSeq;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    getWorker().postMessage({ id, type, ...payload });
  });
}

const synthCache = new Map(); // clave -> {audio, weights}
const MAX_SYNTH = 30;

function synth(text, voice, accent, speed) {
  const key = JSON.stringify([text, voice, accent, speed]);
  if (synthCache.has(key)) {
    const v = synthCache.get(key);
    synthCache.delete(key);
    synthCache.set(key, v);
    return v;
  }
  const p = call('synth', { text, voice, accent, speed }).then((r) => ({ audio: r.audio, weights: r.weights }));
  synthCache.set(key, p);
  p.catch(() => synthCache.delete(key));
  while (synthCache.size > MAX_SYNTH) synthCache.delete(synthCache.keys().next().value);
  return p;
}

let ctx = null;
let source = null;

function audioContext() {
  if (!ctx || ctx.state === 'closed') ctx = new AudioContext();
  return ctx;
}

function playBuffer(samples) {
  return new Promise((resolve) => {
    const ac = audioContext();
    const buf = ac.createBuffer(1, samples.length, 24000);
    buf.copyToChannel(samples, 0);
    const src = ac.createBufferSource();
    src.buffer = buf;
    src.connect(ac.destination);
    src.onended = () => resolve();
    source = src;
    src.start();
  });
}

async function playLocal(msg) {
  stop();
  sessionId = msg.sessionId;
  const sid = sessionId;
  const alive = () => sessionId === sid;
  const { chunks, voice, accent, speed } = msg;

  try {
    if (!modelReady) {
      send('notice', { message: 'Cargando la voz incluida (sólo la primera vez)…' }, sid);
      const info = await call('init', {});
      modelReady = true;
      console.info('Kokoro listo', info);
    }
    if (!alive()) return;
    await audioContext().resume();

    let next = synth(chunks[0].text, voice, accent, speed);
    for (let i = 0; i < chunks.length; i++) {
      const current = await next;
      if (!alive()) return;
      if (i + 1 < chunks.length) next = synth(chunks[i + 1].text, voice, accent, speed);
      send('chunk-start', {
        offset: chunks[i].offset,
        length: chunks[i].text.length,
        duration: current.audio.length / 24000,
        weights: current.weights,
        wordEvents: false
      }, sid);
      await playBuffer(current.audio);
      if (!alive()) return;
    }
    send('end', {}, sid);
    sessionId = null;
  } catch (e) {
    if (!alive()) return;
    send('error', { message: `Voz incluida: ${e.message || e}`, fatal: !modelReady }, sid);
    sessionId = null;
  }
}

function stopTicker() {
  clearInterval(ticker);
  ticker = null;
}

function remember(key, url) {
  blobCache.set(key, url);
  if (blobCache.size > MAX_BLOBS) {
    const [oldKey, oldUrl] = blobCache.entries().next().value;
    URL.revokeObjectURL(oldUrl);
    blobCache.delete(oldKey);
  }
}

async function fetchAudio(msg) {
  const key = JSON.stringify([msg.url, msg.body]);
  if (blobCache.has(key)) return blobCache.get(key);
  const res = await fetch(msg.url, { method: 'POST', headers: msg.headers, body: JSON.stringify(msg.body) });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const data = await res.json();
      if (data && data.detail) detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail);
    } catch { /* cuerpo no JSON */ }
    throw new Error(detail);
  }
  const url = URL.createObjectURL(await res.blob());
  remember(key, url);
  return url;
}

async function playRemote(msg) {
  stop();
  sessionId = msg.sessionId;
  const mine = sessionId;
  const url = await fetchAudio(msg);
  if (sessionId !== mine) return;
  audioEl.src = url;
  audioEl.playbackRate = msg.rate || 1;
  audioEl.preservesPitch = true;
  await audioEl.play();
  send('start', { progress: 0 });
  ticker = setInterval(() => {
    if (audioEl.duration && isFinite(audioEl.duration)) send('progress', { progress: audioEl.currentTime / audioEl.duration });
  }, 80);
}

audioEl.addEventListener('ended', () => {
  stopTicker();
  send('end', { progress: 1 });
  sessionId = null;
});

audioEl.addEventListener('error', () => {
  if (!audioEl.getAttribute('src')) return;
  stopTicker();
  send('error', { message: 'No se pudo reproducir el audio' });
  sessionId = null;
});

function stop() {
  sessionId = null;
  stopTicker();
  if (source) {
    try {
      source.onended = null;
      source.stop();
    } catch { /* ya terminó */ }
    source = null;
  }
  audioEl.pause();
  audioEl.removeAttribute('src');
  audioEl.load();
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg || msg.target !== 'offscreen') return false;
  switch (msg.type) {
    case 'stop':
      stop();
      sendResponse({ ok: true });
      return false;
    case 'local-play':
      playLocal(msg); // los errores llegan como eventos
      sendResponse({ ok: true });
      return false;
    case 'local-warmup':
      call('init', {}).then(() => (modelReady = true)).catch(() => {});
      sendResponse({ ok: true });
      return false;
    case 'phonemize':
      call('phonemize', { words: msg.words, accent: msg.accent })
        .then((r) => sendResponse({ ok: true, phonemes: r.phonemes }))
        .catch((e) => sendResponse({ ok: false, error: String(e.message || e) }));
      return true;
    case 'play':
      playRemote(msg)
        .then(() => sendResponse({ ok: true }))
        .catch((e) => {
          stop();
          sendResponse({ ok: false, error: String((e && e.message) || e) });
        });
      return true;
    default:
      return false;
  }
});
