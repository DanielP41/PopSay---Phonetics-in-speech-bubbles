"""Empaqueta extension/ (ya construida con `npm run build`) en dist/ para las tiendas, sin tests ni node_modules."""

import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXT = ROOT / "extension"
EXCLUDE_DIRS = {"test", "__pycache__", "node_modules"}
EXCLUDE_FILES = {"package.json", "package-lock.json", "build.mjs"}


def main() -> Path:
    if not (EXT / "models" / "kokoro-v1.0.int8.onnx").exists() or not (EXT / "src/engine/kokoro-worker.js").exists():
        raise SystemExit("Primero: python tools/build_core.py  y  cd extension && npm install && npm run build")
    version = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))["version"]
    out = ROOT / "dist" / f"popsay-extension-{version}.zip"
    out.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(EXT.rglob("*")):
            rel = f.relative_to(EXT)
            if f.is_dir() or EXCLUDE_DIRS & set(rel.parts) or f.name.startswith(".") or f.name in EXCLUDE_FILES:
                continue
            z.write(f, rel.as_posix())
    print(out)
    return out


if __name__ == "__main__":
    main()
