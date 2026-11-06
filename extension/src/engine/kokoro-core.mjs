/*
 * Núcleo de Kokoro-82M en JavaScript (mismo pipeline que kokoro-onnx en Python):
 * texto -> fonemas (espeak-ng WASM) -> ids -> ONNX -> audio float32 a 24 kHz.
 * Sin DOM ni APIs de Chrome: corre en un Worker, en el navegador o en Node (tests).
 */

export const SAMPLE_RATE = 24000;
export const MAX_PHONEMES = 510;
export const VOICE_ROWS = 510;
export const STYLE_DIM = 256;

const PUNCT = ';:,.!?¡¿—…"«»“”(){}[]';
const PUNCT_RE = new RegExp(`(\\s*[${PUNCT.replace(/[\]\\[^-]/g, '\\$&')}]+\\s*)+`, 'g');

// Error del fonemizador JS: después de usar 'en-us' ignora 'en-gb'. 'en' es británico en espeak-ng y alterna bien.
export function espeakLang(accent) {
  return accent === 'en-GB' ? 'en' : 'en-us';
}

/** Parte el texto en tramos de palabras y de puntuación (la puntuación se conserva tal cual). */
export function splitPunctuation(text) {
  const out = [];
  let last = 0;
  for (const m of text.matchAll(PUNCT_RE)) {
    if (m.index > last) out.push({ punct: false, text: text.slice(last, m.index) });
    out.push({ punct: true, text: m[0] });
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push({ punct: false, text: text.slice(last) });
  return out;
}

export async function textToPhonemes(text, accent, phonemize) {
  const lang = espeakLang(accent);
  const parts = [];
  for (const sec of splitPunctuation(text)) {
    if (sec.punct) parts.push(sec.text);
    else if (sec.text.trim()) parts.push((await phonemize(sec.text, lang)).join(' '));
    else parts.push(sec.text);
  }
  return parts.join('').replace(/\s+/g, ' ').trim();
}

export function tokenize(phonemes, vocab) {
  const ids = [];
  for (const ch of phonemes) {
    const id = vocab[ch];
    if (id !== undefined) ids.push(id);
  }
  return ids;
}

/** Corta los fonemas en tandas de <= max, preferentemente en puntuación y si no en espacios. */
export function splitPhonemes(phonemes, max = MAX_PHONEMES) {
  const out = [];
  let rest = phonemes.trim();
  while ([...rest].length > max) {
    const chars = [...rest];
    const window = chars.slice(0, max).join('');
    let cut = Math.max(window.lastIndexOf('. '), window.lastIndexOf('! '), window.lastIndexOf('? '));
    if (cut < max * 0.3) cut = Math.max(window.lastIndexOf(', '), window.lastIndexOf('; '));
    if (cut < max * 0.3) cut = window.lastIndexOf(' ');
    if (cut <= 0) cut = window.length - 1;
    out.push(window.slice(0, cut + 1).trim());
    rest = rest.slice(cut + 1).trim();
  }
  if (rest) out.push(rest);
  return out;
}

/** Una fila de estilo por cantidad de fonemas: n fonemas usan la fila n - 1 (como kokoro-onnx). */
export function styleFor(voice, nTokens) {
  const row = Math.max(0, Math.min(nTokens, VOICE_ROWS) - 1);
  return voice.subarray(row * STYLE_DIM, (row + 1) * STYLE_DIM);
}

export async function infer(ort, session, ids, style, speed) {
  const tokens = new ort.Tensor('int64', BigInt64Array.from([0, ...ids, 0].map(BigInt)), [1, ids.length + 2]);
  const feeds = {
    tokens,
    style: new ort.Tensor('float32', Float32Array.from(style), [1, STYLE_DIM]),
    speed: new ort.Tensor('float32', Float32Array.from([speed]), [1])
  };
  const out = await session.run(feeds);
  const audio = out[session.outputNames[0]].data;
  return audio instanceof Float32Array ? audio : Float32Array.from(audio);
}

/** Peso de cada palabra para el resaltado: cantidad de fonemas, si se pueden alinear. */
export function wordWeights(text, phonemes) {
  const words = text.split(/\s+/).filter(Boolean);
  const ph = phonemes.split(/\s+/).filter(Boolean);
  if (words.length !== ph.length) return null;
  return ph.map((p) => Math.max(1, [...p.replace(/[.,;:!?"'()ˈˌ]/g, '')].length));
}

/** Pipeline completo para un fragmento de texto. */
export async function synthesize({ ort, session, phonemize, vocab, voice, text, accent, speed }) {
  const phonemes = await textToPhonemes(text, accent, phonemize);
  const pieces = [];
  for (const batch of splitPhonemes(phonemes)) {
    const ids = tokenize(batch, vocab);
    if (!ids.length) continue;
    pieces.push(await infer(ort, session, ids, styleFor(voice, ids.length), speed));
  }
  const total = pieces.reduce((a, p) => a + p.length, 0);
  const audio = new Float32Array(total);
  let pos = 0;
  for (const p of pieces) {
    audio.set(p, pos);
    pos += p.length;
  }
  return { audio, phonemes, weights: wordWeights(text, phonemes) };
}
