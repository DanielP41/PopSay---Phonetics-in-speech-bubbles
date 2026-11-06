"""Sílabas para el resaltado fino: en el texto escrito (Pyphen) y en la transcripción IPA (núcleos vocálicos).

El tiempo de cada palabra se reparte entre sus sílabas IPA según la cantidad de fonemas; la sílaba escrita
que se ilumina es la que corresponde proporcionalmente (las cantidades no siempre coinciden).
"""

from __future__ import annotations

import re
from functools import lru_cache

# Vocales y diptongos del IPA que usa el diccionario (US y UK) y espeak-ng.
_DIPH = ["aɪ", "aʊ", "eɪ", "oʊ", "əʊ", "ɔɪ", "ɪə", "eə", "ʊə"]
_VOWELS = set("aeiouæɑɒɔəɚɛɜɝɪʊʌɐᵻy")
_STRESS = "ˈˌ"
# Ataques consonánticos de dos sonidos admitidos en inglés (el resto va a la coda anterior).
_ONSETS2 = {"pl", "pr", "bl", "br", "tr", "dr", "kr", "kl", "ɡr", "ɡl", "fr", "fl", "θr", "ʃr", "sp", "st", "sk",
            "sm", "sn", "sl", "sw", "tw", "dw", "kw", "pj", "bj", "fj", "vj", "kj", "mj", "hj", "ɡj", "nj", "lj"}


@lru_cache(maxsize=1)
def _pyphen():
    try:
        import pyphen

        return pyphen.Pyphen(lang="en_US", left=1, right=2)
    except Exception:  # sin Pyphen: heurística por grupos vocálicos
        return None


def _heuristic(word: str) -> list[str]:
    """Corta entre grupos vocálicos: una consonante va a la sílaba siguiente; con dos o más, la última."""
    groups = [m.span() for m in re.finditer(r"[aeiouy]+", word, re.I)]
    if len(groups) <= 1:
        return [word]
    if word.lower().endswith("e") and len(groups) > 1 and groups[-1][0] == len(word) - 1:
        groups = groups[:-1]  # "e" muda final (make, bridge)
    cuts = []
    for (_, a_end), (b_start, _) in zip(groups, groups[1:]):
        n = b_start - a_end
        cuts.append(a_end if n <= 1 else b_start - 1)
    pieces, prev = [], 0
    for c in cuts:
        pieces.append(word[prev:c]); prev = c
    pieces.append(word[prev:])
    return [x for x in pieces if x] or [word]


def _has_vowel(piece: str) -> bool:
    """¿Tiene núcleo? Vocal, o una "y" que no está al principio (my, gym)."""
    return bool(re.search(r"[aeiou]", piece, re.I) or re.search(r"(?<!^)y", piece, re.I))


def _merge_voiceless(pieces: list[str]) -> list[str]:
    """Une los cortes sin vocal a la sílaba vecina (Pyphen corta "your" como y-our)."""
    out: list[str] = []
    for s in pieces:
        if out and not _has_vowel(out[-1]):
            out[-1] += s
        elif out and not _has_vowel(s):
            out[-1] += s
        else:
            out.append(s)
    return out


def ortho(word: str) -> list[str]:
    """Sílabas de una palabra escrita; unidas devuelven exactamente la palabra (con su puntuación)."""
    m = re.match(r"^([^A-Za-z]*)(.*?)([^A-Za-z]*)$", word)
    lead, core, tail = m.groups() if m else ("", word, "")
    if not core:
        return [word]
    out: list[str] = []
    for i, piece in enumerate(core.split("-")):
        if not piece:
            continue
        d = _pyphen()
        sy = d.inserted(piece).split("-") if d else _heuristic(piece)
        sy = _merge_voiceless([s for s in sy if s]) or [piece]
        if len(sy) == 1:
            sy = _heuristic(piece)  # palabras que el diccionario de guionado no corta (radar, data)
        if i > 0 or core.startswith("-"):
            out.append("-")  # el guion se pega a la sílaba anterior más abajo
        out.extend(sy)
    merged: list[str] = []
    for s in out:
        if s == "-" and merged:
            merged[-1] += "-"
        else:
            merged.append(s)
    merged[0] = lead + merged[0]
    merged[-1] = merged[-1] + tail
    return merged if "".join(merged) == word else [word]


def _units(ipa: str) -> list[tuple[str, str]]:
    """Separa el IPA en unidades (texto, tipo): 'V' vocal/diptongo (con ː), 'C' consonante, 'S' acento."""
    out, i = [], 0
    while i < len(ipa):
        two = ipa[i:i + 2]
        if two in _DIPH:
            u, kind = two, "V"
            i += 2
        elif ipa[i] in _STRESS:
            u, kind = ipa[i], "S"
            i += 1
        elif ipa[i] in _VOWELS:
            u, kind = ipa[i], "V"
            i += 1
        else:
            u, kind = ipa[i], "C"
            i += 1
            if ipa[i:i + 1] == "ʃ" and u == "t" or ipa[i:i + 1] == "ʒ" and u == "d":  # tʃ, dʒ
                u += ipa[i]
                i += 1
        while i < len(ipa) and ipa[i] in "ːˑ̃":
            u += ipa[i]
            i += 1
        out.append((u, kind))
    return out


def ipa(word_ipa: str) -> list[str]:
    """Sílabas de la transcripción de UNA palabra (unidas devuelven el texto original)."""
    units = _units(word_ipa)
    nuclei = [k for k, (_, t) in enumerate(units) if t == "V"]
    if len(nuclei) <= 1:
        return [word_ipa]
    starts = [0]
    for a, b in zip(nuclei, nuclei[1:]):
        between = list(range(a + 1, b))
        stress = [k for k in between if units[k][1] == "S"]
        if stress:  # la marca de acento ya indica dónde empieza la sílaba
            starts.append(stress[0])
            continue
        cons = [k for k in between if units[k][1] == "C"]
        if not cons:
            starts.append(b)
        elif len(cons) == 1:
            starts.append(cons[0])
        else:
            pair = units[cons[-2]][0] + units[cons[-1]][0]
            starts.append(cons[-2] if pair in _ONSETS2 else cons[-1])
    starts.append(len(units))
    return ["".join(u for u, _ in units[s:e]) for s, e in zip(starts, starts[1:]) if e > s]


def weights(ipa_syllables: list[str]) -> list[float]:
    """Peso de cada sílaba (cantidad de sonidos) para repartir el tiempo de la palabra."""
    return [max(1.0, float(sum(1 for _, t in _units(s) if t != "S"))) for s in ipa_syllables]


def active(fraction: float, ipa_syllables: list[str]) -> int:
    """Índice de la sílaba IPA que suena en `fraction` (0..1) de la palabra."""
    w = weights(ipa_syllables)
    total, acc = sum(w), 0.0
    for k, x in enumerate(w):
        acc += x
        if fraction < acc / total:
            return k
    return len(w) - 1


def map_index(k: int, n_from: int, n_to: int) -> int:
    """Lleva el índice k de una lista de n_from sílabas a otra de n_to (proporcional)."""
    if n_from <= 1 or n_to <= 1:
        return 0 if n_to <= 1 else min(n_to - 1, k)
    return min(n_to - 1, int(k * n_to / n_from))
