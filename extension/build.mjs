// Prepara la extensión offline: empaqueta el worker de voz y copia el runtime WASM y los modelos.
// Uso: npm install && npm run build   (antes: python ../tools/build_core.py)
import { build } from 'esbuild';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const core = path.resolve(here, '..', 'core');

await build({
  entryPoints: [path.join(here, 'src/engine/worker.src.js')],
  outfile: path.join(here, 'src/engine/kokoro-worker.js'),
  bundle: true,
  format: 'esm',
  platform: 'browser',
  target: 'chrome116',
  minify: true,
  legalComments: 'eof',
  // ONNX Runtime carga su .mjs/.wasm desde wasmPaths en tiempo de ejecución
  external: ['*.wasm'],
  logLevel: 'warning'
});

const vendor = path.join(here, 'vendor/ort');
fs.mkdirSync(vendor, { recursive: true });
for (const f of ['ort-wasm-simd-threaded.wasm', 'ort-wasm-simd-threaded.mjs']) {
  fs.copyFileSync(path.join(here, 'node_modules/onnxruntime-web/dist', f), path.join(vendor, f));
}

const models = path.join(here, 'models');
fs.mkdirSync(models, { recursive: true });
const copies = [
  ['models/kokoro-v1.0.int8.onnx', 'kokoro-v1.0.int8.onnx'],
  ['models/kokoro-vocab.json', 'kokoro-vocab.json'],
  ['voices/voices.json', 'voices.json'],
  ['ipa/ipa-en.json', 'ipa-en.json']
];
const voiceList = JSON.parse(fs.readFileSync(path.join(core, 'voices/voices.json'), 'utf8'));
for (const v of voiceList) copies.push([`voices/${v.id}.bin`, `${v.id}.bin`]);
for (const [src, dst] of copies) {
  const from = path.join(core, src);
  if (!fs.existsSync(from)) {
    console.error(`Falta ${from}. Corré primero: python tools/build_core.py`);
    process.exit(1);
  }
  fs.copyFileSync(from, path.join(models, dst));
}

let total = 0;
for (const dir of ['models', 'vendor/ort', 'src/engine']) {
  for (const f of fs.readdirSync(path.join(here, dir))) total += fs.statSync(path.join(here, dir, f)).size;
}
console.log(`Extensión lista (${(total / 1e6).toFixed(0)} MB de voz, runtime y diccionario).`);
