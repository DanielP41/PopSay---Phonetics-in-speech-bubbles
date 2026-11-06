'use strict';

const FB = globalThis.PopSay;
const form = document.getElementById('form');
const savedEl = document.getElementById('saved');
const statusEl = document.getElementById('backend-status');

let savedTimer = 0;
function flashSaved(msg) {
  savedEl.textContent = msg || 'Guardado';
  clearTimeout(savedTimer);
  savedTimer = setTimeout(() => (savedEl.textContent = ''), 1500);
}

function readForm() {
  const fd = new FormData(form);
  return {
    accent: fd.get('accent') || 'en-US',
    rate: Number(fd.get('rate')) || 1,
    engine: fd.get('engine') || 'local',
    voiceUS: fd.get('voiceUS') || '',
    voiceGB: fd.get('voiceGB') || '',
    kokoroUS: fd.get('kokoroUS') || 'af_heart',
    kokoroGB: fd.get('kokoroGB') || 'bf_emma',
    backendUrl: String(fd.get('backendUrl') || '').trim().replace(/\/+$/, ''),
    apiToken: String(fd.get('apiToken') || '').trim(),
    triggerMode: fd.get('triggerMode') || 'auto',
    autoPlay: fd.get('autoPlay') === 'on',
    showIpa: fd.get('showIpa') === 'on',
    onlyEnglish: fd.get('onlyEnglish') === 'on',
    skipEditable: fd.get('skipEditable') === 'on',
    maxChars: Math.max(20, Math.min(2000, Number(fd.get('maxChars')) || FB.DEFAULT_SETTINGS.maxChars)),
    theme: fd.get('theme') || 'auto',
    disabledSites: String(fd.get('disabledSites') || '')
      .split(/\s+/)
      .map((s) => s.trim().toLowerCase().replace(/^https?:\/\//, '').replace(/\/.*$/, ''))
      .filter(Boolean)
  };
}

function fillForm(s) {
  for (const name of ['accent', 'rate', 'engine', 'triggerMode']) {
    const input = form.querySelector(`input[name="${name}"][value="${String(s[name])}"]`);
    if (input) input.checked = true;
  }
  for (const name of ['autoPlay', 'showIpa', 'onlyEnglish', 'skipEditable']) form.elements[name].checked = !!s[name];
  form.elements.backendUrl.value = s.backendUrl || '';
  form.elements.apiToken.value = s.apiToken || '';
  form.elements.maxChars.value = s.maxChars;
  form.elements.theme.value = s.theme;
  form.elements.disabledSites.value = (s.disabledSites || []).join('\n');
}

function syncEngineSections() {
  const engine = (form.querySelector('input[name="engine"]:checked') || {}).value || 'local';
  document.getElementById('local-voices').hidden = engine !== 'local';
  document.getElementById('system-voices').hidden = engine !== 'system';
}

async function loadLocalVoices(s) {
  const voices = (await chrome.runtime.sendMessage({ type: 'local-voices' })) || [];
  for (const [id, accent] of [['kokoroUS', 'en-US'], ['kokoroGB', 'en-GB']]) {
    const select = document.getElementById(id);
    select.textContent = '';
    for (const v of voices.filter((x) => x.accent === accent)) select.append(new Option(v.label, v.id));
    select.value = s[id];
  }
}

async function loadVoices(s) {
  const voices = (await chrome.runtime.sendMessage({ type: 'list-voices' })) || [];
  for (const [id, lang] of [['voiceUS', 'en-us'], ['voiceGB', 'en-gb']]) {
    const select = document.getElementById(id);
    select.textContent = '';
    select.append(new Option('Automática (la mejor disponible)', ''));
    const sorted = voices
      .filter((v) => (v.lang || '').replace('_', '-').toLowerCase() === lang)
      .concat(voices.filter((v) => (v.lang || '').replace('_', '-').toLowerCase() !== lang))
      .filter((v, i, arr) => arr.findIndex((x) => x.name === v.name) === i);
    for (const v of sorted) {
      const tags = [v.lang, v.remote ? 'online' : 'local', v.wordEvents ? 'resaltado exacto' : null].filter(Boolean).join(' · ');
      select.append(new Option(`${v.name} (${tags})`, v.name));
    }
    select.value = s[id] || '';
  }
}

async function save() {
  const values = readForm();
  await chrome.storage.sync.set(values);
  flashSaved();
}

let debounce = 0;
form.addEventListener('input', (e) => {
  if (e.target.name === 'engine') {
    syncEngineSections();
    chrome.storage.sync.set({ engineChosen: true });
  }
  if (e.target.matches('input[type="text"], input[type="url"], input[type="password"], input[type="number"], textarea')) {
    clearTimeout(debounce);
    debounce = setTimeout(save, 500);
  } else {
    save();
  }
});
form.addEventListener('submit', (e) => e.preventDefault());

document.getElementById('play-sample').addEventListener('click', async () => {
  const s = readForm();
  await chrome.storage.sync.set(s);
  const text = document.getElementById('sample').value;
  const res = await chrome.runtime.sendMessage({ type: 'speak', text, accent: s.accent, rate: s.rate });
  if (!res || !res.ok) flashSaved((res && res.error) || 'No se pudo reproducir');
});

document.getElementById('grant').addEventListener('click', async () => {
  const s = readForm();
  let origin;
  try {
    const u = new URL(s.backendUrl);
    origin = `${u.protocol}//${u.host}/*`;
  } catch {
    statusEl.textContent = 'Escribí una URL válida, por ejemplo http://localhost:8000';
    return;
  }
  // Debe llamarse dentro del gesto del usuario.
  const granted = await chrome.permissions.request({ origins: [origin] });
  if (!granted) {
    statusEl.textContent = 'Permiso denegado.';
    return;
  }
  await chrome.storage.sync.set(s);
  statusEl.textContent = 'Probando…';
  const res = await chrome.runtime.sendMessage({ type: 'test-backend', settings: s });
  if (res && res.ok) {
    const d = res.data || {};
    statusEl.textContent = `Conectado · proveedor: ${d.provider || '—'} · caché: ${d.cache || '—'}`;
  } else {
    statusEl.textContent = `Sin conexión: ${(res && res.error) || 'error desconocido'}`;
  }
});

document.getElementById('shortcuts-link').addEventListener('click', (e) => {
  e.preventDefault();
  const isEdge = navigator.userAgent.includes('Edg/');
  chrome.tabs.create({ url: isEdge ? 'edge://extensions/shortcuts' : 'chrome://extensions/shortcuts' });
});

(async function init() {
  const s = { ...FB.DEFAULT_SETTINGS, ...(await chrome.storage.sync.get(null)) };
  fillForm(s);
  syncEngineSections();
  loadLocalVoices(s);
  loadVoices(s);
  const cmds = await chrome.commands.getAll();
  const cmd = cmds.find((c) => c.name === 'pronounce-selection');
  document.getElementById('shortcut').textContent = (cmd && cmd.shortcut) || 'sin asignar';
})();
