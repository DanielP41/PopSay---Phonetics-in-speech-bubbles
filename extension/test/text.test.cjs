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
