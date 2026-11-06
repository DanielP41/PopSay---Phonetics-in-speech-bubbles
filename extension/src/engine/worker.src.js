/*
 * Worker de voz offline: carga Kokoro (ONNX Runtime Web, WASM) y espeak-ng (WASM) desde
 * los archivos de la propia extensión. Nada sale a internet.
 * Se empaqueta con esbuild en src/engine/kokoro-worker.js (ver build.mjs).
 */
import * as ort from 'onnxruntime-web/wasm';
import { phonemize } from 'phonemizer';
import * as K from './kokoro-core.mjs';

const BASE = new URL('../../', self.location.href); // raíz de la extensión
ort.env.wasm.wasmPaths = new URL('vendor/ort/', BASE).href;
ort.env.wasm.numThreads = self.crossOriginIsolated ? Math.max(1, Math.min(4, (navigator.hardwareConcurrency || 2) - 1)) : 1;
ort.env.wasm.proxy = false;

let session = null;
let loading = null;
let vocab = null;
const voices = new Map();

async function fetchBuffer(path) {
  const res = await fetch(new URL(path, BASE));
  if (!res.ok) throw new Error(`No se pudo leer ${path} (${res.status})`);
  return res.arrayBuffer();
}

function load() {
  if (session) return Promise.resolve();
  if (!loading) {
    loading = (async () => {
      const t0 = performance.now();
      vocab = JSON.parse(new TextDecoder().decode(await fetchBuffer('models/kokoro-vocab.json')));
      const model = await fetchBuffer('models/kokoro-v1.0.int8.onnx');
      session = await ort.InferenceSession.create(model, { executionProviders: ['wasm'], graphOptimizationLevel: 'all' });
      // Precalentamiento: la primera inferencia es mucho más lenta.
      await K.synthesize({ ort, session, phonemize, vocab, voice: await getVoice('af_heart'), text: 'Hi.', accent: 'en-US', speed: 1 });
      return Math.round(performance.now() - t0);
    })().catch((e) => {
      loading = null;
      throw e;
    });
  }
  return loading;
}

async function getVoice(id) {
  if (!/^[a-z]{2}_[a-z]+$/.test(id)) throw new Error('Voz inválida');
  if (!voices.has(id)) voices.set(id, new Float32Array(await fetchBuffer(`models/${id}.bin`)));
  return voices.get(id);
}

/** Recorta silencio al principio y al final (dejando un margen corto), como kokoro-onnx. */
function trim(audio, threshold = 0.004, marginSec = 0.06) {
  let a = 0;
  let b = audio.length - 1;
  while (a < b && Math.abs(audio[a]) < threshold) a++;
  while (b > a && Math.abs(audio[b]) < threshold) b--;
  const m = Math.round(marginSec * K.SAMPLE_RATE);
  return audio.slice(Math.max(0, a - m), Math.min(audio.length, b + m + 1));
}

const handlers = {
  async init() {
    const ms = await load();
    return { ms, threads: ort.env.wasm.numThreads, isolated: self.crossOriginIsolated };
  },

  async synth({ text, voice, accent, speed }) {
    await load();
    const r = await K.synthesize({ ort, session, phonemize, vocab, voice: await getVoice(voice), text, accent, speed });
    const audio = trim(r.audio);
    return { audio, weights: r.weights, transfer: [audio.buffer] };
  },

  async phonemize({ words, accent }) {
    const lang = K.espeakLang(accent);
    const out = [];
    for (const w of words) out.push((await phonemize(w, lang)).join(' '));
    return { phonemes: out };
  }
};

self.onmessage = async ({ data }) => {
  const { id, type } = data || {};
  const handler = handlers[type];
  if (!handler) return;
  try {
    const result = await handler(data);
    const transfer = result.transfer || [];
    delete result.transfer;
    self.postMessage({ id, ok: true, ...result }, transfer);
  } catch (e) {
    self.postMessage({ id, ok: false, error: String((e && e.message) || e) });
  }
};
