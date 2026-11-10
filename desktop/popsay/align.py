"""Alinea las palabras del texto con los fonemas que el motor pronuncia de verdad (espeak-ng, en contexto).

Sirve para que el IPA mostrado coincida con la voz: elegir la variante correcta del diccionario
("read" /riːd/ o /rɛd/, "the" /ðə/ o /ði/) y transcribir lo que el diccionario no tiene (números, siglas).
Lógica pura, sin dependencias; extension/src/lib/text.js tiene la misma en JavaScript.
"""

from __future__ import annotations

import re

UNIT = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)*|[0-9$€£%&@#+=][0-9$€£%&@#+=.,:/]*")
CLAUSE_END = re.compile(r"[.!?;:,][\"')\]]*$")
MAX_CLAUSE_WORDS = 24

_VOWELS = set("aeiouæɑɒɔəɛɜɪʊʌ")
_MAP = [("ɚ", "ər"), ("ɝ", "ər"), ("ɜ", "ər"), ("ɹ", "r"), ("ᵻ", "ɪ"), ("ɐ", "ə"), ("ɾ", "t"), ("ʌ", "ə"),
        ("ɒ", "ɑ"), ("ɔ", "ɑ"), ("g", "ɡ"), ("i", "ɪ")]
_SOUND = re.compile("[a-zæðŋθ\u0250-\u02af]")  # letras IPA; deja afuera largos (ː) y acento secundario (ˌ)
_INF = float("inf")


def norm(ipa: str) -> str:
    """Forma gruesa común al diccionario y a espeak: sin largos ni acento secundario, y con el acento
    principal pegado a su vocal (el diccionario lo pone al inicio de la sílaba; espeak, antes de la vocal)."""
    s = re.sub(r"a(?![ɪʊ])", "æ", ipa)
    s = re.sub(r"e(?![ɪə])", "ɛ", s)
    for a, b in _MAP:
        s = s.replace(a, b)
    out, pending = [], False
    for ch in s:
        if ch == "ˈ":
            pending = True
        elif ch in _VOWELS:
            if pending:
                out.append("ˈ")
                pending = False
            out.append(ch)
        elif _SOUND.match(ch):
            out.append(ch)
    return "".join(out).replace("rr", "r")


def lev(a: str, b: str) -> int:
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def dist(variant: str, heard: str) -> int:
    """Distancia entre una variante del diccionario y lo que suena, ambas normalizadas. El diccionario no
    acentúa los monosílabos y espeak sí: sin marca en la variante, el acento no se compara."""
    return lev(variant, heard if "ˈ" in variant else heard.replace("ˈ", ""))


def clauses(words: list[str]) -> list[tuple[int, int]]:
    """Rangos [desde, hasta) de palabras que se fonemizan juntas (hasta un signo de puntuación)."""
    out, start = [], 0
    for i, w in enumerate(words):
        if CLAUSE_END.search(w) or i + 1 - start >= MAX_CLAUSE_WORDS:
            out.append((start, i + 1))
            start = i + 1
    if start < len(words):
        out.append((start, len(words)))
    return out


def tokens(phonemes: str) -> list[str]:
    """Palabras fonéticas de la salida de espeak, sin puntuación."""
    out = []
    for t in phonemes.split():
        t = t.strip(".,;:!?\"'()[]—–-…")
        if t:
            out.append(t)
    return out


def _split(exps: list[list[str]], s: str) -> tuple[float, list[str]]:
    """Reparte una palabra fonética que espeak pegó ("ʌvðɪ") entre las palabras escritas que la forman."""
    g, n = len(exps), len(s)
    best = [[_INF] * (n + 1) for _ in range(g + 1)]
    cut = [[0] * (n + 1) for _ in range(g + 1)]
    best[0][0] = 0.0
    for u in range(g):
        for p in range(n + 1):
            if best[u][p] == _INF:
                continue
            for q in range(p + 1, n + 1):
                c = best[u][p] + min(dist(v, s[p:q]) for v in exps[u])
                if c < best[u + 1][q]:
                    best[u + 1][q] = c
                    cut[u + 1][q] = p
    pieces, q = [], n
    for u in range(g, 0, -1):
        p = cut[u][q]
        pieces.append(s[p:q])
        q = p
    return best[g][n], pieces[::-1]


def align(expected: list[list[str] | None], toks: list[str]) -> list[tuple[str, str]]:
    """Para cada unidad del texto devuelve (fonemas normalizados, fonemas tal como los da espeak).

    `expected[i]` son las variantes normalizadas que el diccionario conoce para la unidad i, o None si no
    está (número, sigla, nombre): esa absorbe las palabras fonéticas que sobran entre sus vecinas.
    """
    n, m = len(expected), len(toks)
    nt = [norm(t) for t in toks]
    dp = [[_INF] * (m + 1) for _ in range(n + 1)]
    back: list[list[tuple | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    dp[0][0] = 0.0

    def relax(i2: int, j2: int, cost: float, step: tuple) -> None:
        if cost < dp[i2][j2]:
            dp[i2][j2] = cost
            back[i2][j2] = step

    for i in range(n + 1):
        for j in range(m + 1):
            base = dp[i][j]
            if base == _INF:
                continue
            if j < m:
                relax(i, j + 1, base + 3.0, (i, j, []))  # palabra fonética sin dueño (no debería pasar)
            if i == n:
                continue
            exp = expected[i]
            relax(i + 1, j, base + (4.0 if exp else 2.0), (i, j, [("", "")]))
            seg = ""
            for k in range(1, (3 if exp else 12) + 1):
                if j + k > m:
                    break
                seg += nt[j + k - 1]
                cost = min(dist(v, seg) for v in exp) + 0.3 * (k - 1) if exp else 1.0 + 0.01 * k
                relax(i + 1, j + k, base + cost, (i, j, [(seg, " ".join(toks[j:j + k]))]))
            if j < m and exp:
                for g in (2, 3):
                    group = expected[i:i + g]
                    if len(group) < g or not all(group):
                        break
                    cost, pieces = _split(group, nt[j])
                    relax(i + g, j + 1, base + cost + 0.2, (i, j, [(p, p) for p in pieces]))

    out: list[tuple[str, str]] = []
    i, j = n, m
    while (i, j) != (0, 0):
        pi, pj, segs = back[i][j]
        out[:0] = segs
        i, j = pi, pj
    return out


def pick(variants: list[str], heard: str) -> int:
    """Índice de la variante del diccionario más parecida a lo que suena (empate: la primera)."""
    if not heard or len(variants) < 2:
        return 0
    costs = [dist(norm(v), heard) for v in variants]
    return costs.index(min(costs))
