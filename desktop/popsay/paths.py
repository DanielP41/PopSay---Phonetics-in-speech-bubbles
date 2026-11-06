"""Ubicación de la app, los modelos y los datos del usuario (modo portable)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# fp32 primero: en CPU es ~2.3x más rápido que el int8. El int8 queda como respaldo (más liviano).
MODEL_FILES = ("kokoro-v1.0.onnx", "kokoro-v1.0.int8.onnx")
VOICES_FILE = "voices-en.npz"
VOICES_JSON = "voices.json"
IPA_FILE = "ipa-en.json"


def app_dir() -> Path:
    """Carpeta raíz de la instalación portable.

    - Congelado con PyInstaller: la carpeta del .exe.
    - Carpeta portable armada con Python incluido: <raíz>/app/popsay → <raíz>.
    - Desarrollo: la raíz del repositorio.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    here = Path(__file__).resolve().parent
    return here.parent.parent  # app/ -> raíz portable, o desktop/ -> raíz del repo


def _candidates(name: str) -> list[Path]:
    root = app_dir()
    env = os.environ.get("POPSAY_MODELS")
    dirs = [Path(env)] if env else []
    dirs += [root / "models", root / "core" / "models", root / "core" / "voices", root / "core" / "ipa"]
    return [d / name for d in dirs]


def find(name: str) -> Path:
    for p in _candidates(name):
        if p.is_file():
            return p
    tried = "\n  ".join(str(p) for p in _candidates(name))
    raise FileNotFoundError(f"No encuentro {name}. Busqué en:\n  {tried}")


def find_first(names: tuple[str, ...]) -> Path:
    for name in names:
        try:
            return find(name)
        except FileNotFoundError:
            continue
    raise FileNotFoundError(f"No encuentro ninguno de: {', '.join(names)}")


def data_dir() -> Path:
    """Ajustes y registro junto a la app (portable). Si no se puede escribir ahí, %APPDATA%."""
    preferred = app_dir() / "data"
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        probe = preferred / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return preferred
    except OSError:
        base = Path(os.environ.get("APPDATA") or Path.home() / ".config")
        fallback = base / "PopSay"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def asset(name: str) -> Path:
    return Path(__file__).resolve().parent / "assets" / name
