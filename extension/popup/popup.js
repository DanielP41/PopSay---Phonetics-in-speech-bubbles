'use strict';

const FB = globalThis.PopSay;

(async function init() {
  const s = { ...FB.DEFAULT_SETTINGS, ...(await chrome.storage.sync.get(null)) };

  document.getElementById('engine-line').textContent =
    s.engine === 'local' ? 'Voz incluida · sin internet' : s.engine === 'neural' ? 'Backend propio' : 'Voces del sistema';

  const accentInput = document.querySelector(`input[name="accent"][value="${s.accent}"]`);
  if (accentInput) accentInput.checked = true;
  document.querySelectorAll('input[name="accent"]').forEach((el) =>
    el.addEventListener('change', () => chrome.storage.sync.set({ accent: el.value }))
  );

  // Sitio actual (activeTab nos da la URL al abrir el popup)
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const toggle = document.getElementById('site-toggle');
  const hint = document.getElementById('site-hint');
  let hostname = '';
  try {
    const u = new URL(tab && tab.url);
    if (u.protocol === 'http:' || u.protocol === 'https:') hostname = u.hostname;
  } catch {
    hostname = '';
  }
  if (!hostname) {
    toggle.disabled = true;
    document.getElementById('host').textContent = 'esta página';
    hint.textContent = 'Las páginas internas del navegador y el visor de PDF no permiten la burbuja; usá el cuadro de abajo o el menú contextual.';
  } else {
    document.getElementById('host').textContent = hostname;
    toggle.checked = !FB.hostIsDisabled(hostname, s.disabledSites);
    toggle.addEventListener('change', async () => {
      const cur = (await chrome.storage.sync.get('disabledSites')).disabledSites || [];
      const next = toggle.checked
        ? cur.filter((h) => !FB.hostIsDisabled(hostname, [h]))
        : Array.from(new Set([...cur, hostname]));
      await chrome.storage.sync.set({ disabledSites: next });
      hint.textContent = toggle.checked ? '' : 'Desactivada aquí. El atajo y el menú contextual siguen funcionando.';
    });
  }

  const quick = document.getElementById('quick');
  const speak = async () => {
    const text = quick.value.trim();
    if (!text) return quick.focus();
    await chrome.runtime.sendMessage({ type: 'speak', text });
  };
  document.getElementById('speak').addEventListener('click', speak);
  quick.addEventListener('keydown', (e) => e.key === 'Enter' && speak());

  document.getElementById('options').addEventListener('click', () => chrome.runtime.openOptionsPage());

  const cmds = await chrome.commands.getAll();
  const cmd = cmds.find((c) => c.name === 'pronounce-selection');
  document.getElementById('shortcut').textContent = (cmd && cmd.shortcut) || 'sin asignar';
})();
