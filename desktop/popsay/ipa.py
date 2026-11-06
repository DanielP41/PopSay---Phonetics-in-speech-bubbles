"""IPA offline: diccionario compartido (CMUdict → IPA, US y UK) con respaldo en espeak-ng."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

TOKEN = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)*")

# espeak-ng usa algunos símbolos poco familiares para quien estudia; los acercamos al diccionario.
_ESPEAK_FIX = [("ᵻ", "ɪ"), ("ɐ", "ə"), ("ɾ", "t"), ("ʔ", ""), ("ɚ", "ɚ")]


@dataclass
class IpaResult:
    ipa: str
    approximate: bool
    unknown: list[str]
    parts: list[tuple[int, int, str | None]] = None  # (inicio, fin) de cada palabra en el texto y su IPA


class IpaDictionary:
    def __init__(self, path: Path, fallback: Callable[[str, str], str] | None = None):
        self.data: dict[str, str | list[str]] = json.loads(path.read_text(encoding="utf-8"))
        self.fallback = fallback  # (palabra, accent) -> fonemas de espeak

    def word(self, word: str, accent: str) -> tuple[str | None, bool]:
        key = word.lower().replace("’", "'")
        entry = self.data.get(key)
        if entry is None and key.endswith("'s"):
            base = self.data.get(key[:-2])
            if base is not None:
                b = base if isinstance(base, str) else base[1 if accent == "en-GB" else 0]
                return b + ("s" if b[-1] in "ptkfθ" else "ɪz" if b[-1] in "szʃʒ" or b.endswith(("tʃ", "dʒ")) else "z"), False
        if entry is not None:
            if isinstance(entry, str):
                return entry, False
            return entry[1 if accent == "en-GB" else 0], False
        if self.fallback:
            try:
                ph = self.fallback(word, accent)
            except Exception:
                ph = ""
            ph = re.sub(r"[^\wˈˌːəɪʊɛæɑɔʌɜɒɹθðʃʒŋɡɚɝʤʧ]", "", ph)
            for a, b in _ESPEAK_FIX:
                ph = ph.replace(a, b)
            if ph:
                return ph, True
        return None, False

    def transcribe_words(self, words: list[str], accent: str = "en-US") -> tuple[list[str | None], bool]:
        """IPA de cada palabra visible de la burbuja (misma división por espacios), para resaltarla a la par.
        Una "palabra" sin letras (p. ej. "14") queda en None."""
        out: list[str | None] = []
        approx = accent == "en-GB"
        for w in words:
            parts = []
            for m in TOKEN.finditer(w):
                ipa, guessed = self.word(m.group(0), accent)
                approx = approx or guessed
                parts.append(ipa if ipa is not None else f"‹{m.group(0)}›")
            out.append("".join(parts) if parts else None)
        return out, approx

    def transcribe(self, text: str, accent: str = "en-US") -> IpaResult:
        parts, spans, unknown, approx = [], [], [], accent == "en-GB"
        for m in TOKEN.finditer(text):
            ipa, guessed = self.word(m.group(0), accent)
            spans.append((m.start(), m.end(), ipa))
            if ipa is None:
                unknown.append(m.group(0))
                parts.append(f"‹{m.group(0)}›")
            else:
                approx = approx or guessed
                parts.append(ipa)
        return IpaResult(" ".join(parts), approx, unknown, spans)
