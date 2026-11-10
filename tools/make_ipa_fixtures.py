"""Regenera core/tests/ipa-context.json: casos reales (fonemas de espeak-ng → IPA esperado) que comparten
los tests de la app (Python) y de la extensión (JavaScript). Necesita los modelos (tools/build_core.py)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "desktop"))

SENTENCES = [
    "I read the book yesterday, and I will read it again.",
    "I have read that book. I read every day.",
    "They live in a house of the old town. We saw a live show.",
    "I want to record a record.",
    "She will present the present at the event.",
    "The wind will wind down. The apple and the car.",
    "In 2026 the USA spent $3.50 on Dr. Smith.",
    "A well-known tomato — either way, it's Daniel's idea at 9:30!",
    "Kokoro and PopSay use WebAssembly.",
    "Practice your pronunciation every day.",
    "— … !",
]


def main() -> None:
    from popsay import align, paths
    from popsay.engine import Engine
    from popsay.ipa import IpaDictionary

    engine = Engine()
    engine.start().result()
    dic = IpaDictionary(paths.find(paths.IPA_FILE), engine.phonemize)
    cases = []
    for text in SENTENCES:
        words = text.split()
        for accent in ("en-US", "en-GB"):
            heard = [engine.phonemize(" ".join(words[a:b]), accent) for a, b in align.clauses(words)]
            per_word, approx = dic._in_context(words, accent)
            cases.append({"text": text, "accent": accent, "heard": heard, "expected": per_word, "approximate": approx})
    out = ROOT / "core" / "tests" / "ipa-context.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(cases, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(cases)} casos → {out}")


if __name__ == "__main__":
    main()
