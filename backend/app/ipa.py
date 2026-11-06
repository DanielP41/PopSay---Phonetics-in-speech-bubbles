"""Transcripción IPA a partir de CMUdict (ARPAbet), con variante británica por reglas.

- en-US: mapeo ARPAbet -> IPA estilo diccionario (Cambridge/Merriam simplificado).
- en-GB: mismas pronunciaciones de CMUdict convertidas con reglas (no rótico,
  vocales largas, GOAT = əʊ, LOT = ɒ, palabras BATH). Se marca como aproximada.
- Palabras fuera del diccionario: g2p_en si está instalado; si no, quedan sin transcribir.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

VOWELS = {"AA", "AE", "AH", "AO", "AW", "AY", "EH", "ER", "EY", "IH", "IY", "OW", "OY", "UH", "UW"}

CONSONANTS_IPA = {
    "B": "b", "CH": "tʃ", "D": "d", "DH": "ð", "F": "f", "G": "ɡ", "HH": "h", "JH": "dʒ",
    "K": "k", "L": "l", "M": "m", "N": "n", "NG": "ŋ", "P": "p", "R": "r", "S": "s",
    "SH": "ʃ", "T": "t", "TH": "θ", "V": "v", "W": "w", "Y": "j", "Z": "z", "ZH": "ʒ",
}

# Ataques silábicos válidos en inglés (para ubicar la marca de acento antes de la sílaba).
VALID_ONSETS = {
    tuple(x.split())
    for x in [
        "P R", "P L", "B R", "B L", "T R", "D R", "K R", "K L", "G R", "G L", "F R", "F L",
        "TH R", "SH R", "S P", "S T", "S K", "S M", "S N", "S L", "S W", "S F",
        "S P R", "S P L", "S T R", "S K R", "S K W", "S K L", "S K Y", "S P Y",
        "T W", "D W", "K W", "G W", "TH W", "P Y", "B Y", "F Y", "V Y", "K Y", "M Y", "HH Y", "G Y",
    ]
} | {(c,) for c in CONSONANTS_IPA if c != "NG"}

# Palabras con vocal BATH (æ en EE. UU., ɑː en el sur de Inglaterra). Lista corta y frecuente.
BATH_WORDS = {
    "after", "afternoon", "answer", "ask", "asked", "asking", "bath", "can't", "castle", "chance",
    "class", "classes", "dance", "dancing", "example", "fast", "faster", "glass", "grass", "half",
    "last", "laugh", "master", "pass", "passed", "past", "path", "plant", "rather", "staff", "task",
    "advance", "advantage", "branch", "command", "demand", "disaster", "draft", "graph", "photograph",
}

WORD_RE = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)*")


@dataclass
class WordResult:
    word: str
    ipa: str | None
    source: str  # "cmudict" | "g2p" | "unknown"


@dataclass
class IpaResult:
    ipa: str
    words: list[WordResult] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    approximate: bool = False


@lru_cache(maxsize=1)
def _cmu() -> dict[str, list[list[str]]]:
    import cmudict

    return cmudict.dict()


@lru_cache(maxsize=1)
def _g2p():
    try:
        from g2p_en import G2p  # type: ignore

        return G2p()
    except Exception:  # paquete no instalado o datos de NLTK ausentes
        return None


def lookup(word: str) -> tuple[list[str] | None, str]:
    w = word.lower().replace("’", "'")
    prons = _cmu().get(w)
    if prons:
        return prons[0], "cmudict"
    g2p = _g2p()
    if g2p is not None:
        try:
            phones = [p for p in g2p(w) if re.match(r"^[A-Z]+[012]?$", p)]
            if phones:
                return phones, "g2p"
        except Exception:
            pass
    return None, "unknown"


def _split(phone: str) -> tuple[str, int | None]:
    m = re.match(r"^([A-Z]+)([012])?$", phone)
    if not m:
        return phone, None
    return m.group(1), (int(m.group(2)) if m.group(2) else None)


def _vowel_ipa(base: str, stress: int, accent: str, *, word: str, final: bool, next_base: str | None, prev_base: str | None) -> str:
    uk = accent == "en-GB"
    if base == "AH":
        return "ʌ" if stress else "ə"
    if base == "ER":
        if uk:
            return "ɜː" if stress else "ə"
        return "ɝ" if stress else "ɚ"
    if base == "IY":
        if stress == 0 and not final:
            return "ɪ"
        return "iː" if uk and stress else "i"
    if base == "UW":
        return "uː" if uk else "u"
    if base == "OW":
        return "əʊ" if uk else "oʊ"
    if base == "AO":
        return "ɔː" if uk else "ɔ"
    if base == "AA":
        if not uk:
            return "ɑ"
        if next_base == "R":
            return "ɑː"
        if prev_base == "W" or "o" in word.lower():
            return "ɒ"
        return "ɑː"
    if base == "AE":
        if uk and stress and word.lower().replace("’", "'") in BATH_WORDS:
            return "ɑː"
        return "æ"
    return {
        "AW": "aʊ", "AY": "aɪ", "EH": "e" if uk else "ɛ", "EY": "eɪ",
        "IH": "ɪ", "OY": "ɔɪ", "UH": "ʊ",
    }[base]


def phones_to_ipa(phones: list[str], accent: str = "en-US", word: str = "") -> str:
    parsed = [_split(p) for p in phones]
    bases = [b for b, _ in parsed]
    vowel_idx = [i for i, b in enumerate(bases) if b in VOWELS]
    uk = accent == "en-GB"

    segs: list[str] = []
    for i, (base, stress) in enumerate(parsed):
        prev_base = bases[i - 1] if i > 0 else None
        next_base = bases[i + 1] if i + 1 < len(bases) else None
        if base in VOWELS:
            final = i == len(parsed) - 1
            segs.append(_vowel_ipa(base, stress or 0, accent, word=word, final=final, next_base=next_base, prev_base=prev_base))
        elif base == "R" and uk:
            # No rótico: la r sólo suena antes de vocal.
            segs.append("r" if next_base in VOWELS else "")
        else:
            segs.append(CONSONANTS_IPA.get(base, base.lower()))

    # Marcas de acento: primaria siempre (si hay más de una sílaba), secundaria sólo antes de la primaria.
    marks: dict[int, str] = {}
    if len(vowel_idx) > 1:
        primary_seen = False
        for n, v in enumerate(vowel_idx):
            stress = parsed[v][1]
            if stress not in (1, 2):
                continue
            if stress == 2 and primary_seen:
                continue
            if stress == 1:
                primary_seen = True
            prev_v = vowel_idx[n - 1] if n > 0 else -1
            k = v
            while k - 1 > prev_v and tuple(bases[k - 1 : v]) in VALID_ONSETS:
                k -= 1
            if n == 0:
                k = 0
            marks[k] = "ˈ" if stress == 1 else "ˌ"

    out = []
    for i, seg in enumerate(segs):
        if i in marks:
            out.append(marks[i])
        out.append(seg)
    return "".join(out)


def transcribe(text: str, accent: str = "en-US") -> IpaResult:
    if accent not in ("en-US", "en-GB"):
        raise ValueError("accent must be en-US or en-GB")
    words: list[WordResult] = []
    for m in WORD_RE.finditer(text):
        token = m.group(0)
        phones, source = lookup(token)
        ipa = phones_to_ipa(phones, accent, token) if phones else None
        words.append(WordResult(word=token, ipa=ipa, source=source if ipa else "unknown"))
    unknown = [w.word for w in words if w.ipa is None]
    joined = " ".join(w.ipa if w.ipa else f"‹{w.word}›" for w in words)
    approximate = accent == "en-GB" or any(w.source == "g2p" for w in words)
    return IpaResult(ipa=joined, words=words, unknown=unknown, approximate=approximate)
