/*
 * PopSay — lógica de texto pura (sin DOM ni APIs de Chrome).
 * Se carga como script clásico en el content script, se importa por efecto
 * en el service worker, y se testea con `node --test`.
 */
(function (root) {
  'use strict';

  const DEFAULT_SETTINGS = Object.freeze({
    accent: 'en-US',           // 'en-US' | 'en-GB'
    rate: 1,                   // 0.6 | 0.8 | 1 | 1.2
    engine: 'local',           // 'local' (voz incluida, offline) | 'system' (voces del navegador) | 'neural' (backend)
    engineChosen: false,       // el usuario eligió el motor a mano
    kokoroUS: 'af_heart',      // voz incluida para en-US
    kokoroGB: 'bf_emma',       // voz incluida para en-GB
    backendUrl: '',            // ej. http://localhost:8000
    apiToken: '',              // X-Api-Key del backend, si está configurado
    voiceUS: '',               // nombre de voz del sistema preferida para en-US
    voiceGB: '',               // nombre de voz del sistema preferida para en-GB
    triggerMode: 'auto',       // 'auto' | 'icon' | 'shortcut'
    maxChars: 2000,
    showIpa: true,
    onlyEnglish: true,
    skipEditable: true,
    theme: 'auto',             // 'auto' | 'dark' | 'light'
    autoPlay: false,           // reproducir apenas se abre la burbuja
    bubbleWidth: 0,            // ancho de la burbuja en px (0 = automático)
    bubbleTextHeight: 0,       // alto del cuadro de texto en px (0 = automático)
    disabledSites: []          // hostnames
  });

  function normalizeSelection(text) {
    return String(text || '')
      .replace(/[­​-‍﻿]/g, '')   // soft hyphens y zero-width
      .replace(/\s+/g, ' ')
      .trim();
  }

  /** Heurística barata: ¿el texto parece inglés (o al menos alfabeto latino sin acentos)? */
  function isLikelyEnglish(text) {
    const letters = text.match(/\p{L}/gu);
    if (!letters || letters.length === 0) return false;
    const ascii = letters.filter((c) => /[A-Za-z]/.test(c)).length;
    if (ascii / letters.length < 0.85) return false;
    // Signos que el inglés casi nunca usa (salvo préstamos como "café").
    if (/[ñ¿¡]/i.test(text) || (text.match(/[áéíóú]/gi) || []).length >= 2) return false;
    if (/[áéíóú]/i.test(text) && letters.length > 12) return false;
    // Palabras muy españolas que delatan el idioma aunque no lleven tilde.
    const words = text.toLowerCase().match(/[a-z']+/g) || [];
    if (words.length >= 3) {
      const es = new Set(['el', 'la', 'los', 'las', 'que', 'de', 'del', 'y', 'en', 'un', 'una', 'es', 'por', 'con', 'para', 'pero', 'muy', 'como']);
      const hits = words.filter((w) => es.has(w)).length;
      if (hits / words.length > 0.25) return false;
    }
    return true;
  }

  function truncateAtWord(text, maxChars) {
    if (!maxChars || text.length <= maxChars) return { text, truncated: false };
    let cut = text.slice(0, maxChars);
    const lastSpace = cut.lastIndexOf(' ');
    if (lastSpace > 0) cut = cut.slice(0, lastSpace);
    return { text: cut.trim(), truncated: true };
  }

  /** Tokens visibles (todo lo que no es espacio), con offsets en el texto original. */
  function splitWords(text) {
    const out = [];
    const re = /\S+/g;
    let m;
    while ((m = re.exec(text)) !== null) {
      out.push({ word: m[0], start: m.index, end: m.index + m[0].length });
    }
    return out;
  }

  function isSingleWord(text) {
    return /^[A-Za-z][A-Za-z'’-]*$/.test(text.trim());
  }

  /** Conteo heurístico de sílabas en inglés; suficiente para estimar tiempos. */
  function countSyllables(word) {
    let w = String(word).toLowerCase().replace(/[^a-z]/g, '');
    if (!w) return /\d/.test(word) ? Math.max(1, String(word).replace(/\D/g, '').length) : 0;
    if (w.length <= 3) return 1;
    w = w.replace(/(?:[^laeiouy]es|ed|[^laeiouy]e)$/, '').replace(/^y/, '');
    const groups = w.match(/[aeiouy]+/g);
    return Math.max(1, groups ? groups.length : 1);
  }

  /**
   * Línea de tiempo estimada: para cada token, la fracción [from, to) del audio
   * que ocupa. Se usa cuando la voz no emite eventos de palabra (voces remotas
   * de Google, audio neuronal del backend).
   */
  function buildWordTimeline(words, phonemeWeights) {
    // Con fonemas por palabra (motor local) el reparto es más fiel que con sílabas.
    const usePh = Array.isArray(phonemeWeights) && phonemeWeights.length === words.length;
    const weights = words.map((t, i) => {
      let w = usePh ? phonemeWeights[i] : countSyllables(t.word) || 0.5;
      if (/[,;:]$/.test(t.word)) w += usePh ? 3 : 0.8;          // pausa corta
      if (/[.!?]["')\]]*$/.test(t.word)) w += usePh ? 5 : 1.4; // pausa de fin de oración
      return Math.max(w, 0.5);
    });
    const total = weights.reduce((a, b) => a + b, 0) || 1;
    let acc = 0;
    return weights.map((w) => {
      const from = acc / total;
      acc += w;
      return { from, to: acc / total };
    });
  }

  function wordIndexAtProgress(timeline, progress) {
    if (!timeline.length) return -1;
    if (progress <= 0) return 0;
    if (progress >= 1) return timeline.length - 1;
    for (let i = 0; i < timeline.length; i++) {
      if (progress < timeline[i].to) return i;
    }
    return timeline.length - 1;
  }

  function wordIndexAtChar(words, charIndex) {
    for (let i = words.length - 1; i >= 0; i--) {
      if (charIndex >= words[i].start) return i;
    }
    return 0;
  }

  /**
   * Parte el texto en fragmentos (texto + offset) para sintetizar por partes.
   * El primero puede ser más corto (firstMax) para que empiece a sonar enseguida; después va de a una
   * oración, así la siguiente se genera mientras suena la actual. Misma lógica que la app de escritorio.
   */
  function chunkText(text, maxLen, firstMax) {
    maxLen = maxLen || 220;
    const chunks = [];
    let pos = 0;
    while (pos < text.length) {
      while (pos < text.length && text[pos] === ' ') pos++;
      if (pos >= text.length) break;
      const limit = chunks.length ? maxLen : firstMax || maxLen;
      if (chunks.length) {
        const m = /[.!?]["')\]]*\s/.exec(text.slice(pos + 25, pos + limit));
        if (m) {
          const cut = 25 + m.index + m[0].length - 1;
          chunks.push({ text: text.slice(pos, pos + cut).trimEnd(), offset: pos });
          pos += cut;
          continue;
        }
      }
      if (text.length - pos <= limit) {
        chunks.push({ text: text.slice(pos), offset: pos });
        break;
      }
      const window = text.slice(pos, pos + limit);
      const floor = limit * (chunks.length ? 0.3 : firstMax ? 0.12 : 0.3);
      let cut = -1;
      const sentence = /[.!?]["')\]]*\s/g;
      let m;
      while ((m = sentence.exec(window)) !== null) cut = m.index + m[0].length - 1;
      if (cut < floor) {
        const comma = Math.max(window.lastIndexOf(', '), window.lastIndexOf('; '), window.lastIndexOf(': '));
        if (comma > floor) cut = comma + 1;
      }
      if (cut < floor) {
        const space = window.lastIndexOf(' ');
        cut = space > 0 ? space : limit;
      }
      chunks.push({ text: text.slice(pos, pos + cut).trimEnd(), offset: pos });
      pos += cut;
    }
    return chunks;
  }

  const CONJ = new Set(['and', 'but', 'or', 'because', 'when', 'while', 'if', 'that', 'which', 'who', 'where', 'so',
    'although', 'though', 'since', 'until', 'unless', 'before', 'after', 'whether', 'whereas']);
  const PREP = new Set(['to', 'for', 'with', 'in', 'on', 'at', 'of', 'by', 'from', 'about', 'into', 'through', 'without',
    'than', 'like', 'during', 'between', 'under', 'over']);

  function cutAt(text, pos, limit, preferFrom, stretch) {
    const rest = text.length - pos;
    if (rest <= limit) return rest;
    for (const hi of [limit, Math.min(stretch, rest)]) {
      const win = text.slice(pos, pos + hi);
      for (const re of [/[.!?]["')\]]*\s/g, /[,;:]\s/g]) {
        const cuts = [];
        let m;
        while ((m = re.exec(win)) !== null) if (m.index + m[0].length - 1 >= preferFrom) cuts.push(m.index + m[0].length - 1);
        if (cuts.length) return hi === limit ? cuts[cuts.length - 1] : cuts[0];
      }
      if (hi >= rest) return rest;
    }
    const wide = text.slice(pos, pos + Math.max(limit, Math.min(stretch, rest)));
    for (const words of [CONJ, PREP]) {
      const cuts = [];
      const re = / (\w+)/g;
      let m;
      while ((m = re.exec(wide)) !== null) {
        if (m.index >= preferFrom && words.has(m[1].toLowerCase()) && m.index + m[0].length < wide.length) cuts.push(m.index);
      }
      const inside = cuts.filter((c) => c <= limit);
      if (inside.length) return inside[inside.length - 1];
      if (cuts.length) return cuts[0];
    }
    const space = text.slice(pos, pos + limit).lastIndexOf(' ');
    return space > 0 ? space : limit;
  }

  /**
   * Fragmentos para sintetizar mientras suena, sin huecos (igual que la app de escritorio).
   * rtf: segundos de cálculo por segundo de audio en esta compu. El primero es corto; cada uno de los
   * siguientes se dimensiona para estar listo antes de que termine lo que ya está sonando.
   */
  function planChunks(text, rtf, speed, firstMax, maxLen) {
    rtf = Math.max(0.2, rtf || 0.7);
    speed = speed || 1;
    firstMax = firstMax || 70;
    maxLen = maxLen || 400;
    const cpsLow = 15 * speed, cpsHigh = 19 * speed;
    const chunks = [];
    let pos = 0, ahead = 0;
    while (pos < text.length) {
      while (pos < text.length && text[pos] === ' ') pos++;
      if (pos >= text.length) break;
      let limit, prefer, stretch;
      if (!chunks.length) {
        limit = firstMax; prefer = Math.floor(firstMax / 2); stretch = Math.floor(firstMax * 1.4);
      } else {
        limit = Math.floor(Math.min(maxLen, Math.max(30, ahead * 0.8 * cpsLow / rtf)));
        prefer = Math.floor(limit * 0.3); stretch = limit;
      }
      const k = cutAt(text, pos, limit, prefer, stretch);
      const piece = text.slice(pos, pos + k).trimEnd();
      if (piece) {
        const audio = piece.length / cpsHigh;
        ahead = chunks.length ? ahead - rtf * audio + audio : audio;
        chunks.push({ text: piece, offset: pos });
      }
      pos += Math.max(k, 1);
    }
    return chunks;
  }

  function hostIsDisabled(hostname, list) {
    const host = String(hostname || '').toLowerCase();
    return (list || []).some((entry) => {
      const e = String(entry || '').trim().toLowerCase().replace(/^\*\./, '');
      return e && (host === e || host.endsWith('.' + e));
    });
  }

  function cacheKey(parts) {
    return parts.map((p) => String(p)).join('␟');
  }

  const api = {
    DEFAULT_SETTINGS,
    normalizeSelection,
    isLikelyEnglish,
    truncateAtWord,
    splitWords,
    isSingleWord,
    countSyllables,
    buildWordTimeline,
    wordIndexAtProgress,
    wordIndexAtChar,
    chunkText,
    planChunks,
    hostIsDisabled,
    cacheKey
  };

  root.PopSay = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
