"""Prepara el núcleo offline compartido por la extensión y la app de escritorio.

1. Baja el modelo Kokoro int8 y el paquete de voces (una sola vez, desde GitHub).
2. Extrae un subconjunto de voces en inglés:
   - core/voices/<voz>.bin   float32 crudo (510 x 256), para la extensión (WASM)
   - core/voices/voices-en.npz  para la app de escritorio (kokoro-onnx)
3. Genera el diccionario IPA compartido (US + UK) desde CMUdict:
   - core/ipa/ipa-en.json   {"palabra": "us"} o {"palabra": ["us", "gb"]}

Uso:  python tools/build_core.py
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
CORE = ROOT / "core"
MODELS = CORE / "models"
BASE_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
# int8: extensión (en WebAssembly es el más rápido). fp32: app de escritorio (en CPU nativo es ~2.3x más rápido).
FILES = {"kokoro-v1.0.int8.onnx": 92_361_271, "kokoro-v1.0.onnx": 325_532_387, "voices-v1.0.bin": 28_214_398}

# id -> (acento, nombre visible). El primero de cada acento es el predeterminado.
VOICES = {
    "af_heart": ("en-US", "Heart (mujer)"),
    "am_michael": ("en-US", "Michael (hombre)"),
    "af_bella": ("en-US", "Bella (mujer)"),
    "am_fenrir": ("en-US", "Fenrir (hombre)"),
    "bf_emma": ("en-GB", "Emma (mujer)"),
    "bm_george": ("en-GB", "George (hombre)"),
    "bf_isabella": ("en-GB", "Isabella (mujer)"),
    "bm_fable": ("en-GB", "Fable (hombre)"),
}


def fetch_models() -> None:
    MODELS.mkdir(parents=True, exist_ok=True)
    for name, size in FILES.items():
        dest = MODELS / name
        if dest.exists() and dest.stat().st_size == size:
            continue
        print(f"Bajando {name} ({size / 1e6:.0f} MB)…")
        tmp = dest.with_suffix(".part")
        urllib.request.urlretrieve(f"{BASE_URL}/{name}", tmp)
        if tmp.stat().st_size != size:
            sys.exit(f"Tamaño inesperado en {name}")
        tmp.replace(dest)


def build_voices() -> None:
    out = CORE / "voices"
    out.mkdir(parents=True, exist_ok=True)
    allv = np.load(MODELS / "voices-v1.0.bin")
    subset = {}
    for vid in VOICES:
        arr = np.asarray(allv[vid], dtype="<f4").reshape(510, 256)
        (out / f"{vid}.bin").write_bytes(arr.tobytes())
        subset[vid] = arr.reshape(510, 1, 256)
    np.savez(out / "voices-en.npz", **subset)
    manifest = [{"id": k, "accent": a, "label": label} for k, (a, label) in VOICES.items()]
    (out / "voices.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Voces: {len(VOICES)} → {out}")


def build_vocab() -> None:
    """Vocabulario de fonemas de Kokoro (lo usa la extensión; la app de escritorio lo trae en kokoro-onnx)."""
    import kokoro_onnx

    cfg = json.loads((Path(kokoro_onnx.__file__).parent / "config.json").read_text(encoding="utf-8"))
    (MODELS / "kokoro-vocab.json").write_text(json.dumps(cfg["vocab"], ensure_ascii=False), encoding="utf-8")


def build_ipa() -> None:
    sys.path.insert(0, str(ROOT / "backend"))
    import cmudict
    from app.ipa import phones_to_ipa

    out = CORE / "ipa"
    out.mkdir(parents=True, exist_ok=True)
    data: dict[str, str | list[str]] = {}
    for word, prons in cmudict.dict().items():
        if not any(c.isalpha() for c in word):
            continue
        us = phones_to_ipa(prons[0], "en-US", word)
        gb = phones_to_ipa(prons[0], "en-GB", word)
        data[word] = us if us == gb else [us, gb]
    path = out / "ipa-en.json"
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    print(f"IPA: {len(data)} palabras → {path} ({path.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    fetch_models()
    build_voices()
    build_vocab()
    build_ipa()
