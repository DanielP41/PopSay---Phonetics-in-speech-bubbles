// Núcleo de Kokoro en JS: debe producir los mismos fonemas que kokoro-onnx (Python).
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { phonemize } from 'phonemizer';
import * as K from '../src/engine/kokoro-core.mjs';

const vocabPath = new URL('../models/kokoro-vocab.json', import.meta.url);
const vocab = fs.existsSync(vocabPath) ? JSON.parse(fs.readFileSync(vocabPath, 'utf8')) : null;

test('fonemas idénticos a kokoro-onnx (US y UK)', async () => {
  const text = 'Required reviews validate approach. Water, car!';
  assert.equal(await K.textToPhonemes(text, 'en-US', phonemize), 'ɹᵻkwˈaɪɚd ɹᵻvjˈuːz vˈælᵻdˌeɪt ɐpɹˈoʊtʃ. wˈɔːɾɚ, kˈɑːɹ!');
  assert.equal(await K.textToPhonemes(text, 'en-GB', phonemize), 'ɹɪkwˈaɪəd ɹɪvjˈuːz vˈalɪdˌeɪt ɐpɹˈəʊtʃ. wˈɔːtə, kˈɑː!');
  // y volver a US después de UK (el fonemizador JS tiene un error al alternar con 'en-gb')
  assert.match(await K.textToPhonemes('water', 'en-US', phonemize), /ɾɚ/);
});

test('tokens idénticos a kokoro-onnx', { skip: !vocab && 'falta models/kokoro-vocab.json (npm run build)' }, async () => {
  const ph = await K.textToPhonemes('Required reviews validate approach. Water, car!', 'en-US', phonemize);
  const ids = K.tokenize(ph, vocab);
  assert.equal(ids.length, 54);
  assert.deepEqual(ids.slice(0, 12), [123, 177, 53, 65, 156, 43, 102, 85, 46, 16, 123, 177]);
});

test('splitPunctuation conserva la puntuación', () => {
  const s = K.splitPunctuation('Hi, there! Ok');
  assert.deepEqual(s.map((x) => x.text).join(''), 'Hi, there! Ok');
  assert.deepEqual(s.map((x) => x.punct), [false, true, false, true, false]);
});

test('splitPhonemes respeta el máximo', () => {
  const long = Array.from({ length: 200 }, (_, i) => (i % 9 === 8 ? 'wˈɜːd.' : 'wˈɜːd')).join(' ');
  const parts = K.splitPhonemes(long, 100);
  assert.ok(parts.length > 1);
  for (const p of parts) assert.ok([...p].length <= 100, p);
  assert.equal(parts.join(' ').replace(/\s+/g, ' '), long);
});

test('styleFor elige la fila n-1, acotada', () => {
  const voice = new Float32Array(K.VOICE_ROWS * K.STYLE_DIM).map((_, i) => Math.floor(i / K.STYLE_DIM));
  assert.equal(K.styleFor(voice, 1)[0], 0);
  assert.equal(K.styleFor(voice, 54)[0], 53);
  assert.equal(K.styleFor(voice, 9999)[0], K.VOICE_ROWS - 1);
  assert.equal(K.styleFor(voice, 3).length, K.STYLE_DIM);
});

test('wordWeights alinea palabras con fonemas', () => {
  assert.deepEqual(K.wordWeights('Hi there.', 'hˈaɪ ðˈɛɹ.'), [3, 3]); // sin marcas de acento ni puntuación
  assert.equal(K.wordWeights('Hi there', 'hˈaɪ'), null);
});
