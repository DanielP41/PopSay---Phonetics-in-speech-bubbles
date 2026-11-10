'use strict';
// Ejecutar con: node --test extension/test
const test = require('node:test');
const assert = require('node:assert/strict');
const FB = require('../src/lib/text.js');

test('timeline con fonemas por palabra', () => {
  const words = FB.splitWords('aa bbbb.');
  const tl = FB.buildWordTimeline(words, [2, 4]);
  assert.ok(Math.abs(tl[1].to - 1) < 1e-9);
  assert.ok(tl[0].to < 0.3);
  assert.equal(FB.buildWordTimeline(words, [1]).length, 2); // largo distinto: cae a sílabas
});

test('normalizeSelection colapsa espacios y quita invisibles', () => {
  assert.equal(FB.normalizeSelection('  Required\n\treviews​  validate  '), 'Required reviews validate');
  assert.equal(FB.normalizeSelection(null), '');
});

test('isLikelyEnglish', () => {
  assert.equal(FB.isLikelyEnglish('Required reviews validate approach'), true);
  assert.equal(FB.isLikelyEnglish('Las revisiones obligatorias validan el enfoque'), false);
  assert.equal(FB.isLikelyEnglish('Pronunciación en inglés'), false);
  assert.equal(FB.isLikelyEnglish('12345 !!!'), false);
  assert.equal(FB.isLikelyEnglish('café'), false);
  assert.equal(FB.isLikelyEnglish('the'), true);
});

test('truncateAtWord respeta palabras', () => {
  const r = FB.truncateAtWord('one two three four five', 12);
  assert.equal(r.truncated, true);
  assert.equal(r.text, 'one two');
  assert.deepEqual(FB.truncateAtWord('short', 100), { text: 'short', truncated: false });
});

test('splitWords devuelve offsets correctos', () => {
  const text = 'Hello, big world.';
  const w = FB.splitWords(text);
  assert.deepEqual(w.map((x) => x.word), ['Hello,', 'big', 'world.']);
  for (const t of w) assert.equal(text.slice(t.start, t.end), t.word);
});

test('countSyllables aproxima bien palabras comunes', () => {
  const cases = { cat: 1, approach: 2, validate: 3, reviews: 2, required: 2, beautiful: 3, the: 1, strategy: 3 };
  for (const [word, n] of Object.entries(cases)) assert.equal(FB.countSyllables(word), n, word);
});

test('timeline cubre 0..1 y es monótona', () => {
  const tl = FB.buildWordTimeline(FB.splitWords('Hi there. How are you, friend?'));
  assert.equal(tl[0].from, 0);
  assert.ok(Math.abs(tl[tl.length - 1].to - 1) < 1e-9);
  for (let i = 1; i < tl.length; i++) assert.ok(tl[i].from >= tl[i - 1].from);
});

test('wordIndexAtProgress y wordIndexAtChar', () => {
  const words = FB.splitWords('aa bb cc');
  const tl = FB.buildWordTimeline(words);
  assert.equal(FB.wordIndexAtProgress(tl, 0), 0);
  assert.equal(FB.wordIndexAtProgress(tl, 0.5), 1);
  assert.equal(FB.wordIndexAtProgress(tl, 1), 2);
  assert.equal(FB.wordIndexAtProgress([], 0.5), -1);
  assert.equal(FB.wordIndexAtChar(words, 0), 0);
  assert.equal(FB.wordIndexAtChar(words, 3), 1);
  assert.equal(FB.wordIndexAtChar(words, 7), 2);
});

test('chunkText corta por oración y conserva offsets', () => {
  const text = 'First sentence is here. Second one follows, with a comma. Third.';
  const chunks = FB.chunkText(text, 30);
  assert.ok(chunks.length >= 2);
  for (const c of chunks) {
    assert.ok(c.text.length <= 30, c.text);
    assert.equal(text.slice(c.offset, c.offset + c.text.length), c.text);
  }
  assert.equal(chunks[0].text, 'First sentence is here.');
});

test('chunkText con texto corto devuelve un solo fragmento', () => {
  assert.deepEqual(FB.chunkText('Hello world', 200), [{ text: 'Hello world', offset: 0 }]);
});

test('chunkText con una palabra gigante no se cuelga', () => {
  const chunks = FB.chunkText('x'.repeat(500), 200);
  assert.equal(chunks.map((c) => c.text).join(''), 'x'.repeat(500));
});

test('hostIsDisabled incluye subdominios', () => {
  const list = ['google.com', '*.example.org'];
  assert.equal(FB.hostIsDisabled('docs.google.com', list), true);
  assert.equal(FB.hostIsDisabled('google.com', list), true);
  assert.equal(FB.hostIsDisabled('notgoogle.com', list), false);
  assert.equal(FB.hostIsDisabled('a.example.org', list), true);
  assert.equal(FB.hostIsDisabled('wikipedia.org', []), false);
});

test('isSingleWord', () => {
  assert.equal(FB.isSingleWord("don't"), true);
  assert.equal(FB.isSingleWord('two words'), false);
});

test('chunkText: primero corto y después de a una oración (igual que la app de escritorio)', () => {
  const t = 'Before merging, required reviews validate approach and catch regressions early. ' +
    'Small pull requests get faster feedback from the team, and everyone learns. Ok.';
  const c = FB.chunkText(t, 200, 70);
  assert.deepEqual(c.map((x) => x.text), ['Before merging,', 'required reviews validate approach and catch regressions early.',
    'Small pull requests get faster feedback from the team, and everyone learns.', 'Ok.']);
  for (const x of c) assert.equal(t.slice(x.offset, x.offset + x.text.length), x.text);
});

test('planChunks: mismos cortes que la app de escritorio y sin perder texto', () => {
  const t = 'However, the most important thing when you are learning a new language is to practice speaking every single day with real people, even if you make mistakes. Mistakes are part of the process.';
  const c = FB.planChunks(t, 0.7, 1);
  assert.deepEqual(c.map((x) => x.text), [
    'However, the most important thing when you are learning a new language is',
    'to practice speaking every single day with real people,',
    'even if you make mistakes. Mistakes are part of the process.'
  ]);
  for (const x of c) assert.equal(t.slice(x.offset, x.offset + x.text.length), x.text);
  assert.deepEqual(FB.planChunks('Hello world.', 0.7, 1), [{ text: 'Hello world.', offset: 0 }]);
});

test('IPA en contexto: mismos resultados que la app en los casos compartidos', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const core = path.join(__dirname, '..', '..', 'core');
  const dict = JSON.parse(fs.readFileSync(path.join(core, 'ipa', 'ipa-en.json'), 'utf8'));
  const cases = JSON.parse(fs.readFileSync(path.join(core, 'tests', 'ipa-context.json'), 'utf8'));
  assert.ok(cases.length >= 20);
  for (const c of cases) {
    const words = c.text.split(/\s+/).filter(Boolean);
    const r = FB.ipaInContext(words, c.accent, dict, c.heard);
    assert.deepEqual(r.perWord, c.expected, `${c.accent} · ${c.text}`);
    assert.equal(r.approximate, c.approximate, c.text);
  }
});

test('IPA en contexto: elige la variante que suena y no confunde la clave interna', () => {
  const dict = { read: 'rɛd', it: 'ɪt', will: 'wɪl', i: 'aɪ', '#alt': { read: ['rid'] } };
  assert.deepEqual(FB.ipaInContext(['I', 'will', 'read', 'it.'], 'en-US', dict, ['aɪ wɪl ɹˈiːd ɪt.']).perWord, ['aɪ', 'wɪl', 'rid', 'ɪt']);
  assert.deepEqual(FB.ipaInContext(['read'], 'en-US', dict, ['']).perWord, ['rɛd']);
  assert.equal(FB.ipaDictWord(dict, '#alt', 'en-US'), null);
  assert.deepEqual(FB.ipaVariants(dict, 'read', 'en-US'), ['rɛd', 'rid']);
});

test('sílabas: mismos cortes que la app (Pyphen + reglas) en el texto y en el IPA', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const core = path.join(__dirname, '..', '..', 'core');
  const hyph = JSON.parse(fs.readFileSync(path.join(core, 'ipa', 'hyph-en.json'), 'utf8'));
  const cases = JSON.parse(fs.readFileSync(path.join(core, 'tests', 'syllables.json'), 'utf8'));
  for (const [word, expected] of cases.ortho) assert.deepEqual(FB.sylOrtho(word, hyph), expected, `ortho ${word}`);
  for (const [ipa, expected, weights] of cases.ipa) {
    assert.deepEqual(FB.sylIpa(ipa), expected, `ipa ${ipa}`);
    assert.deepEqual(FB.sylWeights(FB.sylIpa(ipa)), weights, `pesos ${ipa}`);
  }
});

test('sílabas: qué resaltar según cuánto sonó la palabra', () => {
  const d = FB.wordDetails(['pronunciation', 'your', '2026'], ['prəˌnʌnsɪˈeɪʃən', 'jʊr', null], null)
  assert.equal(d[0].ipa.join(''), 'prəˌnʌnsɪˈeɪʃən');
  assert.deepEqual(FB.sylState(d[0], 0), { j: 0, k: 0 });
  assert.equal(FB.sylState(d[0], 0.99).j, d[0].ipa.length - 1);
  assert.deepEqual(FB.sylState(d[1], 0.5), { j: -1, k: -1 }); // una sílaba: la palabra entera
  assert.deepEqual(FB.sylState(d[2], 0.5), { j: -1, k: -1 });
  assert.deepEqual(FB.sylState(d[0], undefined), { j: -1, k: -1 }); // voces sin avance dentro de la palabra
});
