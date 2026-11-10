/*
 * PopSay — service worker (MV3).
 * Motores de voz:
 *   - "local"  (predeterminado): Kokoro incluido en la extensión, WASM en un offscreen document. Offline.
 *   - "system": chrome.tts con las voces del sistema/navegador.
 *   - "neural": backend propio (opcional), reproducido en el offscreen document.
 * IPA: diccionario incluido (CMUdict → IPA US/UK) + espeak-ng para palabras desconocidas. Offline.
 */
import './lib/text.js';

const FB = globalThis.PopSay;
const OFFSCREEN_URL = 'src/offscreen.html';
const TOKEN_RE = /[A-Za-z]+(?:['’][A-Za-z]+)*/g;

/** Sesión de reproducción activa (una sola a la vez en todo el navegador). */
let current = null;
let sessionSeq = 0;
const ipaCache = new Map();
let ipaDict = null;

async function getSettings() {
  const stored = await chrome.storage.sync.get(null);
  return { ...FB.DEFAULT_SETTINGS, ...stored };
}

function backendOrigin(url) {
  try {
    const u = new URL(url);
    if (u.protocol !== 'http:' && u.protocol !== 'https:') return null;
    return `${u.protocol}//${u.host}`;
  } catch {
    return null;
  }
}

async function backendReady(settings) {
  const origin = backendOrigin(settings.backendUrl);
  if (!origin) return null;
  const granted = await chrome.permissions.contains({ origins: [origin + '/*'] });
  return granted ? origin + new URL(settings.backendUrl).pathname.replace(/\/+$/, '') : null;
}

function authHeaders(settings) {
  return settings.apiToken ? { 'X-Api-Key': settings.apiToken } : {};
}

chrome.runtime.onInstalled.addListener(async (details) => {
  const stored = await chrome.storage.sync.get(null);
  const merged = { ...FB.DEFAULT_SETTINGS, ...stored };
  if (merged.maxChars === 600) merged.maxChars = 2000; // límite por defecto anterior
  // Quien venía de la 1.0 (voces del sistema por defecto) pasa a la voz incluida.
  if (details.reason === 'update' && !stored.engineChosen) merged.engine = 'local';
  await chrome.storage.sync.set(merged);
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({ id: 'popsay-pronounce', title: 'Pronunciar «%s»', contexts: ['selection'] });
  });
  if (details.reason === 'install') chrome.runtime.openOptionsPage();
});

async function ensureOffscreen() {
  const url = chrome.runtime.getURL(OFFSCREEN_URL);
  if (chrome.runtime.getContexts) {
    const ctx = await chrome.runtime.getContexts({ contextTypes: ['OFFSCREEN_DOCUMENT'], documentUrls: [url] });
    if (ctx.length) return;
  }
  try {
    await chrome.offscreen.createDocument({
      url: OFFSCREEN_URL,
      reasons: ['AUDIO_PLAYBACK', 'WORKERS'],
      justification: 'Generar la voz con el modelo incluido (Web Worker) y reproducir la pronunciación.'
    });
  } catch (e) {
    if (!String(e).includes('single offscreen')) throw e;
  }
}

function toOffscreen(msg) {
  return chrome.runtime.sendMessage({ target: 'offscreen', ...msg });
}

function emit(session, payload) {
  if (!session || !current || current.id !== session.id) return;
  if (session.tabId == null) return; // reproducción sin burbuja (PDF, páginas internas)
  chrome.tabs
    .sendMessage(session.tabId, { type: 'tts-event', sessionId: session.id, ...payload }, { frameId: session.frameId ?? 0 })
    .catch(() => {});
}

async function stopCurrent() {
  const prev = current;
  current = null;
  if (!prev) return;
  if (prev.engine === 'system') chrome.tts.stop();
  else toOffscreen({ type: 'stop' }).catch(() => {});
}

function voiceScore(v, accent) {
  const lang = (v.lang || '').replace('_', '-').toLowerCase();
  let s = 0;
  if (lang === accent.toLowerCase()) s += 100;
  else if (lang.startsWith('en')) s += 10;
  else return -1;
  if (/natural|neural|online/i.test(v.voiceName)) s += 40;
  if (/google/i.test(v.voiceName)) s += 15;
  if ((v.eventTypes || []).includes('word')) s += 10;
  return s;
}

async function pickVoice(settings) {
  const voices = await chrome.tts.getVoices();
  const wanted = settings.accent === 'en-GB' ? settings.voiceGB : settings.voiceUS;
  if (wanted) {
    const v = voices.find((x) => x.voiceName === wanted);
    if (v) return v;
  }
  let best = null;
  let bestScore = -1;
  for (const v of voices) {
    const sc = voiceScore(v, settings.accent);
    if (sc > bestScore) {
      best = v;
      bestScore = sc;
    }
  }
  return best;
}

async function speakSystem(session, text, settings) {
  session.engine = 'system';
  const voice = await pickVoice(settings);
  const wordEvents = !!(voice && (voice.eventTypes || []).includes('word'));
  const chunks = FB.chunkText(text, 200);
  chunks.forEach((chunk, i) => {
    const opts = {
      lang: settings.accent,
      rate: Number(settings.rate) || 1,
      enqueue: i > 0,
      onEvent: (ev) => {
        if (!current || current.id !== session.id) return;
        switch (ev.type) {
          case 'start':
            emit(session, { kind: 'chunk-start', offset: chunk.offset, length: chunk.text.length, wordEvents, rate: opts.rate });
            break;
          case 'word':
            emit(session, { kind: 'word', charIndex: chunk.offset + (ev.charIndex || 0) });
            break;
          case 'end':
            if (i === chunks.length - 1) {
              emit(session, { kind: 'end' });
              current = null;
            }
            break;
          case 'error':
            emit(session, {
              kind: 'error',
              message: voice
                ? ev.errorMessage || 'Error de la voz del sistema.'
                : 'No hay voces en inglés instaladas en el sistema. Usá la voz incluida (Ajustes › Motor).'
            });
            current = null;
            break;
          default:
            break;
        }
      }
    };
    if (voice) opts.voiceName = voice.voiceName;
    chrome.tts.speak(chunk.text, opts);
  });
  return { voice: voice ? voice.voiceName : null, wordEvents };
}

async function speakLocal(session, text, settings) {
  session.engine = 'local';
  session.text = text;
  session.settings = settings;
  await ensureOffscreen();
  const voice = settings.accent === 'en-GB' ? settings.kokoroGB : settings.kokoroUS;
  const res = await toOffscreen({
    type: 'local-play',
    sessionId: session.id,
    text, // el reproductor lo parte según la velocidad medida en esta compu
    chunks: FB.chunkText(text, 220, 70),
    voice,
    accent: settings.accent,
    speed: Math.max(0.5, Math.min(2, Number(settings.rate) || 1))
  });
  if (!res || !res.ok) throw new Error((res && res.error) || 'El reproductor no respondió');
}

async function speakNeural(session, text, settings, base) {
  session.engine = 'neural';
  await ensureOffscreen();
  const res = await toOffscreen({
    type: 'play',
    sessionId: session.id,
    url: base + '/tts',
    headers: { 'Content-Type': 'application/json', ...authHeaders(settings) },
    body: { text, accent: settings.accent },
    rate: Number(settings.rate) || 1
  });
  if (!res || !res.ok) throw new Error((res && res.error) || 'El reproductor no respondió');
}

async function speak({ text, accent, rate }, sender) {
  const settings = await getSettings();
  if (accent) settings.accent = accent;
  if (rate) settings.rate = rate;
  const clean = FB.truncateAtWord(FB.normalizeSelection(text), settings.maxChars).text;
  if (!clean) return { ok: false, error: 'No hay texto' };

  await stopCurrent();
  const session = {
    id: ++sessionSeq,
    tabId: sender && sender.tab ? sender.tab.id : null,
    frameId: sender ? sender.frameId : 0,
    engine: settings.engine
  };
  current = session;

  if (settings.engine === 'local') {
    try {
      await speakLocal(session, clean, settings);
      return { ok: true, sessionId: session.id, engine: 'local', wordEvents: false };
    } catch (e) {
      emit(session, { kind: 'notice', message: `Voz incluida no disponible (${e.message}); uso la del sistema.` });
    }
  }

  if (settings.engine === 'neural') {
    const base = await backendReady(settings);
    if (base) {
      try {
        await speakNeural(session, clean, settings, base);
        return { ok: true, sessionId: session.id, engine: 'neural', wordEvents: false };
      } catch {
        emit(session, { kind: 'notice', message: 'Backend no disponible; uso la voz del sistema.' });
      }
    } else {
      emit(session, { kind: 'notice', message: 'Backend sin configurar o sin permiso; uso la voz del sistema.' });
    }
  }

  const info = await speakSystem(session, clean, settings);
  return { ok: true, sessionId: session.id, engine: 'system', ...info };
}

let hyphDict = null;
async function loadHyph() {
  if (!hyphDict) {
    try {
      hyphDict = await (await fetch(chrome.runtime.getURL('models/hyph-en.json'))).json();
    } catch {
      hyphDict = { maxlen: 0, patterns: {} };
    }
  }
  return hyphDict;
}

async function loadIpaDict() {
  if (!ipaDict) {
    const res = await fetch(chrome.runtime.getURL('models/ipa-en.json'));
    ipaDict = await res.json();
  }
  return ipaDict;
}

/** IPA que sigue a la voz: el fonemizador del motor decide entre variantes y lee números y siglas. */
async function ipaFollowingVoice(dict, clean, accent) {
  const words = clean.split(' ').filter(Boolean);
  const clauses = FB.ipaClauses(words).map(([a, b]) => words.slice(a, b).join(' '));
  if (!clauses.length) return null;
  try {
    await ensureOffscreen();
    const r = await toOffscreen({ type: 'phonemize', words: clauses, accent });
    if (!r || !r.ok || !Array.isArray(r.phonemes) || r.phonemes.length !== clauses.length) return null;
    const { perWord, approximate } = FB.ipaInContext(words, accent, dict, r.phonemes);
    const shown = perWord.filter(Boolean);
    if (!shown.length) return { ipa: null };
    const unknown = shown.filter((p) => p.startsWith('‹')).map((p) => p.slice(1, -1));
    return { ipa: shown.join(' '), source: 'local', approximate, unknown, perWord: perWord.map((x) => (x && !x.startsWith('‹') ? x : null)) };
  } catch {
    return null; // sin motor: el diccionario solo
  }
}

function ipaFromDictionary(dict, clean, accent) {
  const words = clean.match(TOKEN_RE) || [];
  if (!words.length) return { ipa: null };
  const parts = words.map((w) => FB.ipaDictWord(dict, w, accent));
  const unknown = words.filter((_, i) => !parts[i]);
  return { ipa: parts.map((p, i) => p || `‹${words[i]}›`).join(' '), source: 'local', approximate: accent === 'en-GB', unknown };
}

async function getIpa({ text, accent }) {
  const settings = await getSettings();
  accent = accent || settings.accent;
  const clean = FB.normalizeSelection(text).slice(0, 2000);
  if (!clean) return { ipa: null };
  const key = FB.cacheKey(['ipa', accent, clean]);
  if (ipaCache.has(key)) return ipaCache.get(key);

  const dict = await loadIpaDict();
  const result = (await ipaFollowingVoice(dict, clean, accent)) || ipaFromDictionary(dict, clean, accent);
  ipaCache.set(key, result);
  if (ipaCache.size > 300) ipaCache.delete(ipaCache.keys().next().value);
  return result;
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || msg.target === 'offscreen') return false;

  if (msg.type === 'offscreen-event') {
    if (current && current.id === msg.sessionId) {
      const { type: _t, sessionId: _s, ...payload } = msg;
      // Si la voz incluida no pudo cargar, seguimos con la del sistema sin que el usuario haga nada.
      if (msg.kind === 'error' && msg.fatal && current.engine === 'local' && current.text) {
        const session = current;
        emit(session, { kind: 'notice', message: `${msg.message}. Uso la voz del sistema.` });
        speakSystem(session, session.text, session.settings).catch(() => {});
        return false;
      }
      emit(current, payload);
      if (msg.kind === 'end' || msg.kind === 'error') current = null;
    }
    return false;
  }

  const handlers = {
    speak: () => speak(msg, sender),
    stop: async () => {
      await stopCurrent();
      return { ok: true };
    },
    warmup: async () => {
      const s = await getSettings();
      if (s.engine === 'local') {
        await ensureOffscreen();
        await toOffscreen({ type: 'local-warmup' });
      }
      return { ok: true };
    },
    ipa: () => getIpa(msg),
    hyph: () => loadHyph(),
    'open-options': async () => {
      await chrome.runtime.openOptionsPage();
      return { ok: true };
    },
    'list-voices': async () => {
      const voices = await chrome.tts.getVoices();
      return voices
        .filter((v) => (v.lang || '').toLowerCase().startsWith('en'))
        .map((v) => ({ name: v.voiceName, lang: v.lang, remote: !!v.remote, wordEvents: (v.eventTypes || []).includes('word') }));
    },
    'local-voices': async () => (await fetch(chrome.runtime.getURL('models/voices.json'))).json(),
    'test-backend': async () => {
      const settings = { ...(await getSettings()), ...(msg.settings || {}) };
      const base = await backendReady(settings);
      if (!base) return { ok: false, error: 'URL inválida o permiso no concedido' };
      try {
        const res = await fetch(base + '/health', { headers: authHeaders(settings) });
        const data = await res.json();
        return { ok: res.ok, data };
      } catch (e) {
        return { ok: false, error: String(e.message || e) };
      }
    }
  };

  const handler = handlers[msg.type];
  if (!handler) return false;
  handler()
    .then((r) => sendResponse(r))
    .catch((e) => sendResponse({ ok: false, error: String(e && e.message ? e.message : e) }));
  return true; // respuesta asíncrona
});

async function openBubbleOrSpeak(tabId, frameId, text) {
  try {
    const res = await chrome.tabs.sendMessage(tabId, { type: 'open-bubble', text: text || null }, frameId != null ? { frameId } : undefined);
    if (res && res.ok) return;
  } catch {
    /* sin content script (visor de PDF, páginas internas, Web Store) */
  }
  if (text) await speak({ text }, null);
}

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId !== 'popsay-pronounce' || !tab) return;
  openBubbleOrSpeak(tab.id, info.frameId, info.selectionText);
});

chrome.commands.onCommand.addListener(async (command, tab) => {
  if (command !== 'pronounce-selection') return;
  if (!tab) [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) return;
  try {
    await chrome.tabs.sendMessage(tab.id, { type: 'open-bubble', text: null });
  } catch {
    /* página sin content script */
  }
});

// Si cambian los ajustes de voz mientras suena, cortamos para no mezclar.
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'sync' && (changes.engine || changes.voiceUS || changes.voiceGB || changes.kokoroUS || changes.kokoroGB)) stopCurrent();
});
