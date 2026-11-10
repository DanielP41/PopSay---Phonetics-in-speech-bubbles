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

  // ---- IPA que sigue a la voz (misma lógica que desktop/popsay/align.py e ipa.py) ----

  const IPA_ALT_KEY = '#alt';
  const IPA_UNIT = /[A-Za-z]+(?:['’][A-Za-z]+)*|[0-9$€£%&@#+=][0-9$€£%&@#+=.,:/]*/g;
  const IPA_CLAUSE_END = /[.!?;:,]["')\]]*$/;
  const IPA_MAX_CLAUSE_WORDS = 24;
  const IPA_VOWELS = new Set('aeiouæɑɒɔəɛɜɪʊʌ');
  const IPA_MAP = [['ɚ', 'ər'], ['ɝ', 'ər'], ['ɜ', 'ər'], ['ɹ', 'r'], ['ᵻ', 'ɪ'], ['ɐ', 'ə'], ['ɾ', 't'], ['ʌ', 'ə'],
    ['ɒ', 'ɑ'], ['ɔ', 'ɑ'], ['g', 'ɡ'], ['i', 'ɪ']];
  const IPA_SOUND = /[a-zæðŋθɐ-ʯ]/;

  /** Forma gruesa común al diccionario y a espeak, con el acento principal pegado a su vocal. */
  function ipaNorm(ipa) {
    let s = ipa.replace(/a(?![ɪʊ])/g, 'æ').replace(/e(?![ɪə])/g, 'ɛ');
    for (const [a, b] of IPA_MAP) s = s.split(a).join(b);
    let out = '';
    let pending = false;
    for (const ch of s) {
      if (ch === 'ˈ') pending = true;
      else if (IPA_VOWELS.has(ch)) {
        if (pending) { out += 'ˈ'; pending = false; }
        out += ch;
      } else if (IPA_SOUND.test(ch)) out += ch;
    }
    return out.split('rr').join('r');
  }

  function lev(a, b) {
    if (a === b) return 0;
    let prev = Array.from({ length: b.length + 1 }, (_, i) => i);
    for (let i = 1; i <= a.length; i++) {
      const cur = [i];
      for (let j = 1; j <= b.length; j++) {
        cur.push(Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] !== b[j - 1] ? 1 : 0)));
      }
      prev = cur;
    }
    return prev[b.length];
  }

  /** El diccionario no acentúa los monosílabos y espeak sí: sin marca en la variante, el acento no se compara. */
  function ipaDist(variant, heard) {
    return lev(variant, variant.includes('ˈ') ? heard : heard.split('ˈ').join(''));
  }

  /** Rangos [desde, hasta) de palabras que se fonemizan juntas (hasta un signo de puntuación). */
  function ipaClauses(words) {
    const out = [];
    let start = 0;
    words.forEach((w, i) => {
      if (IPA_CLAUSE_END.test(w) || i + 1 - start >= IPA_MAX_CLAUSE_WORDS) {
        out.push([start, i + 1]);
        start = i + 1;
      }
    });
    if (start < words.length) out.push([start, words.length]);
    return out;
  }

  function ipaTokens(phonemes) {
    return String(phonemes || '').split(/\s+/).map((t) => t.replace(/^[.,;:!?"'()[\]—–\-…]+|[.,;:!?"'()[\]—–\-…]+$/g, '')).filter(Boolean);
  }

  function ipaSplit(exps, s) {
    const g = exps.length, n = s.length;
    const best = Array.from({ length: g + 1 }, () => new Array(n + 1).fill(Infinity));
    const cut = Array.from({ length: g + 1 }, () => new Array(n + 1).fill(0));
    best[0][0] = 0;
    for (let u = 0; u < g; u++) {
      for (let p = 0; p <= n; p++) {
        if (best[u][p] === Infinity) continue;
        for (let q = p + 1; q <= n; q++) {
          const c = best[u][p] + Math.min(...exps[u].map((v) => ipaDist(v, s.slice(p, q))));
          if (c < best[u + 1][q]) { best[u + 1][q] = c; cut[u + 1][q] = p; }
        }
      }
    }
    const pieces = [];
    let q = n;
    for (let u = g; u > 0; u--) {
      const p = cut[u][q];
      pieces.unshift(s.slice(p, q));
      q = p;
    }
    return [best[g][n], pieces];
  }

  /**
   * Para cada unidad del texto: [fonemas normalizados, fonemas tal como los da espeak].
   * expected[i]: variantes normalizadas del diccionario, o null si no está (número, sigla, nombre).
   */
  function ipaAlign(expected, toks) {
    const n = expected.length, m = toks.length;
    const nt = toks.map(ipaNorm);
    const dp = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(Infinity));
    const back = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(null));
    dp[0][0] = 0;
    const relax = (i2, j2, cost, step) => {
      if (cost < dp[i2][j2]) { dp[i2][j2] = cost; back[i2][j2] = step; }
    };
    for (let i = 0; i <= n; i++) {
      for (let j = 0; j <= m; j++) {
        const base = dp[i][j];
        if (base === Infinity) continue;
        if (j < m) relax(i, j + 1, base + 3, [i, j, []]);
        if (i === n) continue;
        const exp = expected[i];
        relax(i + 1, j, base + (exp ? 4 : 2), [i, j, [['', '']]]);
        let seg = '';
        for (let k = 1; k <= (exp ? 3 : 12); k++) {
          if (j + k > m) break;
          seg += nt[j + k - 1];
          const cost = exp ? Math.min(...exp.map((v) => ipaDist(v, seg))) + 0.3 * (k - 1) : 1 + 0.01 * k;
          relax(i + 1, j + k, base + cost, [i, j, [[seg, toks.slice(j, j + k).join(' ')]]]);
        }
        if (j < m && exp) {
          for (const g of [2, 3]) {
            const group = expected.slice(i, i + g);
            if (group.length < g || !group.every(Boolean)) break;
            const [cost, pieces] = ipaSplit(group, nt[j]);
            relax(i + g, j + 1, base + cost + 0.2, [i, j, pieces.map((p) => [p, p])]);
          }
        }
      }
    }
    let out = [];
    let i = n, j = m;
    while (i !== 0 || j !== 0) {
      const [pi, pj, segs] = back[i][j];
      out = segs.concat(out);
      i = pi; j = pj;
    }
    return out;
  }

  /** Índice de la variante del diccionario más parecida a lo que suena (empate: la primera). */
  function ipaPick(variants, heard) {
    if (!heard || variants.length < 2) return 0;
    const costs = variants.map((v) => ipaDist(ipaNorm(v), heard));
    return costs.indexOf(Math.min(...costs));
  }

  /** IPA de diccionario de una palabra (con posesivo 's), o null. */
  function ipaDictWord(dict, word, accent) {
    const key = word.toLowerCase().replace(/’/g, "'");
    const pick = (e) => (typeof e === 'string' ? e : e[accent === 'en-GB' ? 1 : 0]);
    if (key !== IPA_ALT_KEY && dict[key] !== undefined) return pick(dict[key]);
    if (key.endsWith("'s") && dict[key.slice(0, -2)] !== undefined) {
      const b = pick(dict[key.slice(0, -2)]);
      const last = b.slice(-1);
      return b + ('ptkfθ'.includes(last) ? 's' : 'szʃʒ'.includes(last) || /tʃ$|dʒ$/.test(b) ? 'ɪz' : 'z');
    }
    return null;
  }

  /** Pronunciaciones de diccionario de una palabra para ese acento (la primera es la más común). */
  function ipaVariants(dict, word, accent) {
    const key = word.toLowerCase().replace(/’/g, "'");
    if (key === IPA_ALT_KEY || dict[key] === undefined) {
      const w = ipaDictWord(dict, word, accent);
      return w ? [w] : [];
    }
    const k = accent === 'en-GB' ? 1 : 0;
    const out = [];
    for (const e of [dict[key], ...((dict[IPA_ALT_KEY] || {})[key] || [])]) {
      const v = typeof e === 'string' ? e : e[k];
      if (!out.includes(v)) out.push(v);
    }
    return out;
  }

  /** espeak-ng usa algunos símbolos poco familiares para quien estudia; los acercamos al diccionario. */
  function ipaCleanEspeak(ph) {
    return String(ph || '').replace(/[^\p{L}\p{N}_ ˈˌː]/gu, '')
      .replace(/ᵻ/g, 'ɪ').replace(/ɐ/g, 'ə').replace(/ɾ/g, 't').replace(/ʔ/g, '').replace(/ɹ/g, 'r').trim();
  }

  /**
   * IPA de cada palabra visible siguiendo lo que pronuncia la voz. heard[c] son los fonemas de espeak
   * para la cláusula c de ipaClauses(words). Devuelve { perWord: (string|null)[], approximate }.
   */
  function ipaInContext(words, accent, dict, heard) {
    const perWord = [];
    let approximate = accent === 'en-GB';
    ipaClauses(words).forEach(([a, b], c) => {
      const units = [];
      for (let i = a; i < b; i++) {
        for (const m of words[i].matchAll(IPA_UNIT)) units.push([i, m[0].replace(/[.,:/]+$/, '')]);
      }
      const variants = units.map(([, u]) => (/[A-Za-z]/.test(u[0]) ? ipaVariants(dict, u, accent) : []));
      const toks = units.length ? ipaTokens(heard[c]) : [];
      const segs = ipaAlign(variants.map((vs) => (vs.length ? vs.map(ipaNorm) : null)), toks);
      const parts = {};
      units.forEach(([i, unit], x) => {
        const vs = variants[x];
        const [heardNorm, heardRaw] = segs[x];
        let ipa;
        if (vs.length) ipa = vs[ipaPick(vs, heardNorm)];
        else {
          ipa = ipaCleanEspeak(heardRaw);
          if (!ipa && /[A-Za-z]/.test(unit[0])) ipa = `‹${unit}›`;
          approximate = approximate || Boolean(ipa);
        }
        if (ipa) (parts[i] = parts[i] || []).push(ipa);
      });
      for (let i = a; i < b; i++) perWord.push(parts[i] ? parts[i].join('') : null);
    });
    return { perWord, approximate };
  }

  // ---- Sílabas (misma lógica que desktop/popsay/syllables.py) ----

  const SYL_DIPH = ['aɪ', 'aʊ', 'eɪ', 'oʊ', 'əʊ', 'ɔɪ', 'ɪə', 'eə', 'ʊə'];
  const SYL_VOWELS = new Set('aeiouæɑɒɔəɚɛɜɝɪʊʌɐᵻy');
  const SYL_ONSETS2 = new Set(['pl', 'pr', 'bl', 'br', 'tr', 'dr', 'kr', 'kl', 'ɡr', 'ɡl', 'fr', 'fl', 'θr', 'ʃr', 'sp', 'st', 'sk',
    'sm', 'sn', 'sl', 'sw', 'tw', 'dw', 'kw', 'pj', 'bj', 'fj', 'vj', 'kj', 'mj', 'hj', 'ɡj', 'nj', 'lj']);

  /** Posiciones de corte de una palabra según los patrones de Pyphen ({ maxlen, patterns }). */
  function hyphenPositions(word, hyph, left, right) {
    const pointed = `.${word.toLowerCase()}.`;
    const refs = new Array(pointed.length + 1).fill(0);
    for (let i = 0; i < pointed.length - 1; i++) {
      const stop = Math.min(i + hyph.maxlen, pointed.length) + 1;
      for (let j = i + 1; j < stop; j++) {
        const pat = hyph.patterns[pointed.slice(i, j)];
        if (!pat) continue;
        const [offset, values] = pat;
        for (let k = 0; k < values.length; k++) {
          const at = i + offset + k;
          if (at < refs.length) refs[at] = Math.max(refs[at], values.charCodeAt(k) - 48);
        }
      }
    }
    const out = [];
    refs.forEach((r, i) => { if (r % 2 && i - 1 >= left && i - 1 <= word.length - right) out.push(i - 1); });
    return out;
  }

  function sylHeuristic(word) {
    let groups = [...word.matchAll(/[aeiouy]+/gi)].map((m) => [m.index, m.index + m[0].length]);
    if (groups.length <= 1) return [word];
    if (word.toLowerCase().endsWith('e') && groups[groups.length - 1][0] === word.length - 1) groups = groups.slice(0, -1);
    const cuts = [];
    for (let g = 0; g + 1 < groups.length; g++) {
      const aEnd = groups[g][1], bStart = groups[g + 1][0];
      cuts.push(bStart - aEnd <= 1 ? aEnd : bStart - 1);
    }
    const pieces = [];
    let prev = 0;
    for (const c of cuts) { pieces.push(word.slice(prev, c)); prev = c; }
    pieces.push(word.slice(prev));
    const kept = pieces.filter(Boolean);
    return kept.length ? kept : [word];
  }

  const sylHasVowel = (piece) => /[aeiou]/i.test(piece) || /(?<!^)y/i.test(piece);

  function sylMergeVoiceless(pieces) {
    const out = [];
    for (const s of pieces) {
      if (out.length && (!sylHasVowel(out[out.length - 1]) || !sylHasVowel(s))) out[out.length - 1] += s;
      else out.push(s);
    }
    return out;
  }

  /** Sílabas de una palabra escrita; unidas devuelven exactamente la palabra (con su puntuación). */
  function sylOrtho(word, hyph) {
    const m = /^([^A-Za-z]*)(.*?)([^A-Za-z]*)$/s.exec(word);
    const [lead, core, tail] = m ? [m[1], m[2], m[3]] : ['', word, ''];
    if (!core) return [word];
    const out = [];
    core.split('-').forEach((piece, i) => {
      if (!piece) return;
      let sy;
      if (hyph) {
        sy = [];
        let prev = 0;
        for (const pos of hyphenPositions(piece, hyph, 1, 2)) { sy.push(piece.slice(prev, pos)); prev = pos; }
        sy.push(piece.slice(prev));
      } else sy = sylHeuristic(piece);
      sy = sylMergeVoiceless(sy.filter(Boolean));
      if (!sy.length) sy = [piece];
      if (sy.length === 1) sy = sylHeuristic(piece);
      if (i > 0 || core.startsWith('-')) out.push('-');
      out.push(...sy);
    });
    const merged = [];
    for (const s of out) {
      if (s === '-' && merged.length) merged[merged.length - 1] += '-';
      else merged.push(s);
    }
    if (!merged.length) return [word];
    merged[0] = lead + merged[0];
    merged[merged.length - 1] += tail;
    return merged.join('') === word ? merged : [word];
  }

  /** Separa el IPA en unidades [texto, tipo]: 'V' vocal/diptongo, 'C' consonante, 'S' acento. */
  function sylUnits(ipa) {
    const out = [];
    let i = 0;
    while (i < ipa.length) {
      const two = ipa.slice(i, i + 2);
      let u, kind;
      if (SYL_DIPH.includes(two)) { u = two; kind = 'V'; i += 2; }
      else if ('ˈˌ'.includes(ipa[i])) { u = ipa[i]; kind = 'S'; i += 1; }
      else if (SYL_VOWELS.has(ipa[i])) { u = ipa[i]; kind = 'V'; i += 1; }
      else {
        u = ipa[i]; kind = 'C'; i += 1;
        if ((ipa[i] === 'ʃ' && u === 't') || (ipa[i] === 'ʒ' && u === 'd')) { u += ipa[i]; i += 1; }
      }
      while (i < ipa.length && 'ːˑ̃'.includes(ipa[i])) { u += ipa[i]; i += 1; }
      out.push([u, kind]);
    }
    return out;
  }

  /** Sílabas de la transcripción de UNA palabra (unidas devuelven el texto original). */
  function sylIpa(wordIpa) {
    if (wordIpa.trim().includes(' ')) {
      const out = [];
      for (const part of wordIpa.match(/\S+\s*|^\s+/g) || []) {
        const body = part.trimEnd();
        if (body.trim()) out.push(...sylIpa(body));
        else out.push(part);
        if (body.trim()) out[out.length - 1] += part.slice(body.length);
      }
      return out;
    }
    const units = sylUnits(wordIpa);
    const nuclei = [];
    units.forEach(([, t], k) => { if (t === 'V') nuclei.push(k); });
    if (nuclei.length <= 1) return [wordIpa];
    const starts = [0];
    for (let n = 0; n + 1 < nuclei.length; n++) {
      const a = nuclei[n], b = nuclei[n + 1];
      const stress = [], cons = [];
      for (let k = a + 1; k < b; k++) {
        if (units[k][1] === 'S') stress.push(k);
        else if (units[k][1] === 'C') cons.push(k);
      }
      if (stress.length && !(cons.length && stress[0] === b - 1)) { starts.push(stress[0]); continue; }
      if (!cons.length) starts.push(b);
      else if (cons.length === 1) starts.push(cons[0]);
      else {
        const pair = units[cons[cons.length - 2]][0] + units[cons[cons.length - 1]][0];
        starts.push(SYL_ONSETS2.has(pair) ? cons[cons.length - 2] : cons[cons.length - 1]);
      }
    }
    starts.push(units.length);
    const out = [];
    for (let k = 0; k + 1 < starts.length; k++) {
      if (starts[k + 1] > starts[k]) out.push(units.slice(starts[k], starts[k + 1]).map(([u]) => u).join(''));
    }
    return out;
  }

  /** Peso de cada sílaba IPA (cantidad de sonidos) para repartir el tiempo de la palabra. */
  function sylWeights(ipaSyllables) {
    return ipaSyllables.map((s) => Math.max(1, sylUnits(s).filter(([, t]) => t !== 'S').length));
  }

  /** Índice de la sílaba IPA que suena en `fraction` (0..1) de la palabra. */
  function sylActive(fraction, weights) {
    const total = weights.reduce((a, b) => a + b, 0);
    let acc = 0;
    for (let k = 0; k < weights.length; k++) {
      acc += weights[k];
      if (fraction < acc / total) return k;
    }
    return weights.length - 1;
  }

  function sylMapIndex(k, nFrom, nTo) {
    if (nFrom <= 1 || nTo <= 1) return nTo <= 1 ? 0 : Math.min(nTo - 1, k);
    return Math.min(nTo - 1, Math.floor((k * nTo) / nFrom));
  }

  /**
   * Para la palabra que suena: qué sílaba resaltar en el IPA (j) y en el texto (k); -1 = la palabra entera.
   * detail = { ipa: sílabas IPA | null, w: pesos, ortho: sílabas escritas }, frac = cuánto de la palabra ya sonó.
   */
  function sylState(detail, frac) {
    if (!detail || frac === null || frac === undefined) return { j: -1, k: -1 };
    const ortho = detail.ortho || [];
    if (detail.ipa && detail.ipa.length) {
      if (detail.ipa.length === 1) return { j: -1, k: -1 };
      const j = sylActive(frac, detail.w);
      return { j, k: ortho.length > 1 ? sylMapIndex(j, detail.ipa.length, ortho.length) : -1 };
    }
    return { j: -1, k: ortho.length > 1 ? Math.min(ortho.length - 1, Math.floor(frac * ortho.length)) : -1 };
  }

  /** Lo que la burbuja necesita de cada palabra para resaltar por sílabas, en el texto y en el IPA. */
  function wordDetails(words, perWord, hyph) {
    return words.map((word, i) => {
      const ipa = perWord[i] ? sylIpa(perWord[i]) : null;
      return { ipa, w: ipa ? sylWeights(ipa) : null, ortho: sylOrtho(word, hyph) };
    });
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
    ipaNorm,
    ipaClauses,
    ipaTokens,
    ipaAlign,
    ipaPick,
    ipaDictWord,
    ipaVariants,
    ipaCleanEspeak,
    ipaInContext,
    sylOrtho,
    sylIpa,
    sylWeights,
    sylActive,
    sylMapIndex,
    sylState,
    wordDetails,
    hostIsDisabled,
    cacheKey
  };

  root.PopSay = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
