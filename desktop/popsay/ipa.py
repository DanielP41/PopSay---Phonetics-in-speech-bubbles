"""IPA offline: diccionario compartido (CMUdict → IPA, US y UK) con respaldo en espeak-ng."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import align

log = logging.getLogger(__name__)
ALT_KEY = "#alt"  # dentro del diccionario: palabra → pronunciaciones alternativas
TOKEN = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)*")

# espeak-ng usa algunos símbolos poco familiares para quien estudia; los acercamos al diccionario.
_ESPEAK_FIX = [("ᵻ", "ɪ"), ("ɐ", "ə"), ("ɾ", "t"), ("ʔ", ""), ("ɹ", "r")]


@dataclass
class IpaResult:
    ipa: str
    approximate: bool
    unknown: list[str]
    parts: list[tuple[int, int, str | None]] = None  # (inicio, fin) de cada palabra en el texto y su IPA


class IpaDictionary:
    def __init__(self, path: Path, fallback: Callable[[str, str], str] | None = None):
        self.data: dict[str, str | list[str]] = json.loads(path.read_text(encoding="utf-8"))
        self.alt: dict[str, list[str | list[str]]] = self.data.pop(ALT_KEY, {})
        self.fallback = fallback  # (texto, accent) -> fonemas de espeak; el mismo fonemizador que usa la voz

    def variants(self, word: str, accent: str) -> list[str]:
        """Pronunciaciones de diccionario de una palabra para ese acento (la primera es la más común)."""
        key = word.lower().replace("’", "'")
        entry = self.data.get(key)
        if entry is None:
            if key.endswith("'s"):
                ipa, _ = self.word(word, accent)
                return [ipa] if ipa else []
            return []
        k = 1 if accent == "en-GB" else 0
        out = []
        for e in [entry, *self.alt.get(key, [])]:
            v = e if isinstance(e, str) else e[k]
            if v not in out:
                out.append(v)
        return out

    @staticmethod
    def _clean(ph: str) -> str:
        ph = re.sub(r"[^\w ˈˌːəɪʊɛæɑɔʌɜɒɹθðʃʒŋɡɚɝʤʧ]", "", ph)
        for a, b in _ESPEAK_FIX:
            ph = ph.replace(a, b)
        return ph.strip()

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
            ph = self._clean(ph).replace(" ", "")
            if ph:
                return ph, True
        return None, False

    def transcribe_words(self, words: list[str], accent: str = "en-US",
                         in_context: bool = True) -> tuple[list[str | None], bool]:
        """IPA de cada palabra visible de la burbuja (misma división por espacios), para resaltarla a la par.

        Con el fonemizador de la voz disponible, el IPA sigue lo que suena: elige la variante del diccionario
        que pronuncia el motor ("read", "the", "record") y transcribe números y siglas en contexto.
        """
        if self.fallback and in_context:
            try:
                return self._in_context(words, accent)
            except Exception:  # ante cualquier sorpresa del fonemizador, el diccionario solo
                log.warning("IPA en contexto falló; uso el diccionario", exc_info=True)
        return self._by_dictionary(words, accent)

    def _in_context(self, words: list[str], accent: str) -> tuple[list[str | None], bool]:
        out: list[str | None] = []
        approx = accent == "en-GB"
        for a, b in align.clauses(words):
            units = [(i, m.group(0).rstrip(".,:/")) for i in range(a, b) for m in align.UNIT.finditer(words[i])]
            variants = [self.variants(u, accent) if u[0].isalpha() else [] for _, u in units]
            heard = align.tokens(self.fallback(" ".join(words[a:b]), accent)) if units else []
            segs = align.align([[align.norm(v) for v in vs] or None for vs in variants], heard)
            parts: dict[int, list[str]] = {}
            for (i, unit), vs, (heard_norm, heard_raw) in zip(units, variants, segs):
                if vs:
                    ipa = vs[align.pick(vs, heard_norm)]
                else:
                    ipa = self._clean(heard_raw)
                    if not ipa and unit[0].isalpha():
                        ipa = self.word(unit, accent)[0] or f"‹{unit}›"
                    approx = approx or bool(ipa)
                if ipa:
                    parts.setdefault(i, []).append(ipa)
            out += ["".join(parts[i]) if i in parts else None for i in range(a, b)]
        return out, approx

    def _by_dictionary(self, words: list[str], accent: str) -> tuple[list[str | None], bool]:
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
