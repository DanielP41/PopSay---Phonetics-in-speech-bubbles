"""Lógica de texto pura (equivalente a extension/src/lib/text.js)."""

from __future__ import annotations

import re
from dataclasses import dataclass

_INVISIBLE = re.compile("[­​-‍﻿]")
_SPANISH = {"el", "la", "los", "las", "que", "de", "del", "y", "en", "un", "una", "es", "por", "con", "para", "pero", "muy", "como"}


@dataclass(frozen=True)
class Word:
    text: str
    start: int
    end: int


def normalize(text: str | None) -> str:
    return " ".join(_INVISIBLE.sub("", text or "").split())


def is_likely_english(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    ascii_letters = sum(1 for c in letters if "a" <= c.lower() <= "z")
    if ascii_letters / len(letters) < 0.85:
        return False
    if re.search(r"[ñ¿¡]", text, re.I) or len(re.findall(r"[áéíóú]", text, re.I)) >= 2:
        return False
    if re.search(r"[áéíóú]", text, re.I) and len(letters) > 12:
        return False
    words = re.findall(r"[a-z']+", text.lower())
    if len(words) >= 3 and sum(w in _SPANISH for w in words) / len(words) > 0.25:
        return False
    return True


def truncate(text: str, max_chars: int) -> tuple[str, bool]:
    if not max_chars or len(text) <= max_chars:
        return text, False
    cut = text[:max_chars]
    space = cut.rfind(" ")
    if space > 0:
        cut = cut[:space]
    return cut.strip(), True


def split_words(text: str) -> list[Word]:
    return [Word(m.group(0), m.start(), m.end()) for m in re.finditer(r"\S+", text)]


def count_syllables(word: str) -> int:
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        digits = re.sub(r"\D", "", word)
        return max(1, len(digits)) if digits else 0
    if len(w) <= 3:
        return 1
    w = re.sub(r"(?:[^laeiouy]es|ed|[^laeiouy]e)$", "", w)
    w = re.sub(r"^y", "", w)
    return max(1, len(re.findall(r"[aeiouy]+", w)))


def build_timeline(words: list[str], weights: list[float] | None = None) -> list[tuple[float, float]]:
    """Fracción [desde, hasta) del audio para cada palabra.

    `weights` (p. ej. cantidad de fonemas por palabra) reemplaza a las sílabas cuando está.
    """
    out_w = []
    for i, w in enumerate(words):
        base = weights[i] if weights else (count_syllables(w) or 0.5)
        if re.search(r"[,;:]$", w):
            base += 0.8 if not weights else 3
        if re.search(r"[.!?][\"')\]]*$", w):
            base += 1.4 if not weights else 5
        out_w.append(max(base, 0.5))
    total = sum(out_w) or 1.0
    acc = 0.0
    tl = []
    for w in out_w:
        start = acc / total
        acc += w
        tl.append((start, acc / total))
    return tl


def index_at(timeline: list[tuple[float, float]], progress: float) -> int:
    if not timeline:
        return -1
    if progress <= 0:
        return 0
    for i, (_, end) in enumerate(timeline):
        if progress < end:
            return i
    return len(timeline) - 1


def chunk_text(text: str, max_len: int = 200, first_max: int = 70) -> list[tuple[str, int]]:
    """Parte el texto en fragmentos (texto, offset) para sintetizar por partes.

    El primero es corto (first_max) para que empiece a sonar enseguida; los demás llegan mientras suena.
    Corta en fin de oración, si no en coma/punto y coma/dos puntos, si no en un espacio.
    """
    out: list[tuple[str, int]] = []
    pos, n = 0, len(text)
    while pos < n:
        while pos < n and text[pos] == " ":
            pos += 1
        if pos >= n:
            break
        limit = first_max if not out else max_len
        if out:  # después del primero: de a una oración, así la siguiente se genera mientras suena la actual
            m = re.search(r"[.!?][\"')\]]*\s", text[pos + 25:pos + limit])
            if m:
                cut = 25 + m.end() - 1
                out.append((text[pos:pos + cut].rstrip(), pos))
                pos += cut
                continue
        if n - pos <= limit:
            out.append((text[pos:], pos))
            break
        window = text[pos:pos + limit]
        cut = -1
        for m in re.finditer(r"[.!?][\"')\]]*\s", window):
            cut = m.end() - 1
        floor = limit * (0.12 if not out else 0.3)  # el primero acepta cortar en una coma temprana
        if cut < floor:
            comma = max(window.rfind(", "), window.rfind("; "), window.rfind(": "))
            if comma > floor:
                cut = comma + 1
        if cut < floor:
            space = window.rfind(" ")
            cut = space if space > 0 else limit
        out.append((text[pos:pos + cut].rstrip(), pos))
        pos += cut
    return out


def position_at(timeline: list[tuple[float, float]], progress: float) -> tuple[int, float]:
    """(palabra que suena, cuánto de esa palabra ya sonó 0..1): para el resaltado por sílabas."""
    i = index_at(timeline, progress)
    if i < 0:
        return -1, 0.0
    a, b = timeline[i]
    return i, min(1.0, max(0.0, (progress - a) / (b - a))) if b > a else 0.0

_CONJ = {"and", "but", "or", "because", "when", "while", "if", "that", "which", "who", "where", "so",
         "although", "though", "since", "until", "unless", "before", "after", "whether", "whereas"}
_PREP = {"to", "for", "with", "in", "on", "at", "of", "by", "from", "about", "into", "through", "without",
         "than", "like", "during", "between", "under", "over"}
_SENT_END = re.compile(r"[.!?][\"')\]]*\s")
_CLAUSE = re.compile(r"[,;:]\s")


def _cut(text: str, pos: int, limit: int, prefer_from: int, stretch: int) -> int:
    """Largo del próximo fragmento desde `pos`: termina en fin de oración, si no en coma, si no en un espacio.

    Se buscan cortes de puntuación entre `prefer_from` y `limit`; si no hay, se estira hasta `stretch`
    antes de resignarse a cortar en un espacio (un corte a mitad de frase suena como un final de oración).
    """
    rest = len(text) - pos
    if rest <= limit:
        return rest
    for hi in (limit, min(stretch, rest)):
        window = text[pos:pos + hi]
        for pat in (_SENT_END, _CLAUSE):
            cuts = [m.end() - 1 for m in pat.finditer(window) if m.end() - 1 >= prefer_from]
            if cuts:
                return cuts[-1] if hi == limit else cuts[0]
        if hi >= rest:
            return rest
    wide = text[pos:pos + max(limit, min(stretch, rest))]
    for words in (_CONJ, _PREP):  # sin puntuación: cortar donde empieza una frase ("… thing | when you …")
        cuts = [m.start() for m in re.finditer(r" (\w+)", wide)
                if m.start() >= prefer_from and m.group(1).lower() in words and m.end() < len(wide)]
        inside = [c for c in cuts if c <= limit]
        if inside or cuts:
            return inside[-1] if inside else cuts[0]
    space = text[pos:pos + limit].rfind(" ")
    return space if space > 0 else limit


def plan_chunks(text: str, rtf: float = 0.7, speed: float = 1.0, first_max: int = 70,
                max_len: int = 400) -> list[tuple[str, int]]:
    """Parte el texto en fragmentos (texto, offset) para sintetizar mientras suena, sin huecos.

    `rtf` es lo que tarda el motor en generar un segundo de audio en esta compu. El primer fragmento es
    corto para empezar enseguida; cada fragmento siguiente se dimensiona para que esté listo antes de que
    se termine lo que ya está sonando. Con los mismos rtf y velocidad el corte es siempre igual (caché).
    """
    cps_low, cps_high = 15.0 * speed, 19.0 * speed  # caracteres por segundo de audio (cota baja / alta)
    rtf = max(0.2, rtf)
    out: list[tuple[str, int]] = []
    pos, n, ahead = 0, len(text), 0.0
    while pos < n:
        while pos < n and text[pos] == " ":
            pos += 1
        if pos >= n:
            break
        if not out:
            limit, prefer, stretch = first_max, first_max // 2, int(first_max * 1.4)
        else:
            limit = int(min(max_len, max(30, ahead * 0.8 * cps_low / rtf)))
            prefer, stretch = int(limit * 0.3), limit
        k = _cut(text, pos, limit, prefer, stretch)
        piece = text[pos:pos + k].rstrip()
        if piece:
            audio = len(piece) / cps_high
            ahead = audio if not out else ahead - rtf * audio + audio
            out.append((piece, pos))
        pos += max(k, 1)
    return out
