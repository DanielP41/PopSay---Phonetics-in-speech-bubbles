import json

import pytest

from popsay import paths, textutil, winsys
from popsay.ipa import IpaDictionary
from popsay.settings import Settings


@pytest.fixture(scope="module")
def ipa():
    return IpaDictionary(paths.find(paths.IPA_FILE), fallback=lambda w, a: "ɡˈɪbɚɪʃ")


def test_ipa_dictionary_us_uk(ipa):
    assert ipa.transcribe("Required reviews validate approach", "en-US").ipa == "rɪˈkwaɪɚd rɪˈvjuz ˈvælədeɪt əˈproʊtʃ"
    r = ipa.transcribe("Required reviews validate approach", "en-GB")
    assert r.ipa == "rɪˈkwaɪəd rɪˈvjuːz ˈvælədeɪt əˈprəʊtʃ"
    assert r.approximate


def test_ipa_matches_backend(ipa):
    """El diccionario empaquetado debe dar lo mismo que el backend (misma fuente)."""
    import sys

    sys.path.insert(0, str(paths.app_dir() / "backend"))
    from app.ipa import transcribe

    for text in ["water car hot after", "understand computer strategy", "beautiful happy fire"]:
        for acc in ("en-US", "en-GB"):
            assert ipa.transcribe(text, acc).ipa == transcribe(text, acc).ipa


def test_ipa_possessive_and_fallback(ipa):
    assert ipa.word("cat's", "en-US")[0] == "kæts"
    assert ipa.word("dog's", "en-US")[0] == "dɔɡz"
    ph, guessed = ipa.word("zxqvbn", "en-US")
    assert guessed and ph == "ɡˈɪbɚɪʃ"


def test_ipa_without_fallback_marks_unknown():
    d = IpaDictionary(paths.find(paths.IPA_FILE))
    r = d.transcribe("hello zxqvbn", "en-US")
    assert r.unknown == ["zxqvbn"] and "‹zxqvbn›" in r.ipa


def test_textutil_matches_extension_rules():
    assert textutil.normalize("  a​\n b ") == "a b"
    assert textutil.is_likely_english("Required reviews validate approach")
    assert not textutil.is_likely_english("Las revisiones obligatorias validan el enfoque")
    assert textutil.truncate("one two three four five", 12) == ("one two", True)
    assert [textutil.count_syllables(w) for w in ("cat", "approach", "validate", "beautiful")] == [1, 2, 3, 3]


def test_timeline_with_weights():
    tl = textutil.build_timeline(["aa", "bbbb."], [2, 4])
    assert tl[0][0] == 0 and abs(tl[-1][1] - 1) < 1e-9
    assert textutil.index_at(tl, 0.1) == 0 and textutil.index_at(tl, 0.9) == 1
    assert textutil.index_at([], 0.5) == -1


def test_parse_hotkey():
    assert winsys.parse_hotkey("Alt+Shift+P") == (0x1 | 0x4, ord("P"))
    assert winsys.parse_hotkey("ctrl + F8") == (0x2, 0x77)
    with pytest.raises(ValueError):
        winsys.parse_hotkey("P")
    with pytest.raises(ValueError):
        winsys.parse_hotkey("Alt+Nope")


def test_settings_roundtrip_and_bad_values(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"accent": "es-AR", "speed": 1, "mouse_mode": "yes", "unknown": 3, "max_chars": 600}), encoding="utf-8")
    s = Settings.load(p)
    assert s.accent == "en-US"          # valor inválido corregido
    assert s.speed == 1.0               # int aceptado como float
    assert s.mouse_mode is True         # tipo incorrecto ignorado
    assert s.max_chars == 2000          # el viejo límite por defecto (600) pasa al nuevo
    s.accent = "en-GB"
    s.save()
    assert Settings.load(p).voice == "bf_emma"


def test_settings_corrupt_file(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text("{nope", encoding="utf-8")
    assert Settings.load(p).hotkey == "Alt+Shift+P"


def test_voices_manifest_matches_npz():
    import numpy as np

    manifest = json.loads(paths.find(paths.VOICES_JSON).read_text(encoding="utf-8"))
    npz = np.load(paths.find(paths.VOICES_FILE))
    assert {v["id"] for v in manifest} == set(npz.files)
    assert {v["accent"] for v in manifest} == {"en-US", "en-GB"}


def test_chunk_text_first_short_then_sentences():
    t = ("Before merging, required reviews validate approach and catch regressions early. "
         "Small pull requests get faster feedback from the team, and everyone learns. Ok.")
    chunks = textutil.chunk_text(t)
    assert chunks[0][0] == "Before merging,"
    assert [c for c, _ in chunks[1:]] == ["required reviews validate approach and catch regressions early.",
                                         "Small pull requests get faster feedback from the team, and everyone learns.", "Ok."]
    for c, off in chunks:
        assert t[off:off + len(c)] == c
    assert textutil.chunk_text("approach") == [("approach", 0)]


def test_chunk_text_never_loses_text():
    for t in ["x" * 450, "a, " * 120, "Hi. " * 90, "One two three"]:
        t = t.strip()
        joined = "".join(c for c, _ in textutil.chunk_text(t)).replace(" ", "")
        assert joined == t.replace(" ", "")


def test_disk_cache_roundtrip(tmp_path):
    import numpy as np

    from popsay.engine import DiskCache, Speech

    c = DiskCache(tmp_path, max_mb=1)
    sp = Speech(np.sin(np.linspace(0, 100, 24000)).astype(np.float32) * 0.5, [2.0, 3.0])
    c.put("k", sp)
    got = c.get("k")
    assert got is not None and got.weights == [2.0, 3.0]
    assert np.abs(got.audio - sp.audio).max() < 1e-3
    assert c.get("otra") is None
    for i in range(40):  # se recorta por tamaño
        c.put(f"k{i}", sp)
    assert sum(f.stat().st_size for f in tmp_path.glob("*.npz")) <= 1024 * 1024


def test_settings_size_defaults(tmp_path):
    s = Settings.load(tmp_path / "settings.json")
    assert s.card_width == 364 and s.text_height == 0  # 0 = alto automático


def test_ipa_per_word_aligned_with_bubble_words(ipa):
    text = "Previously 14 years with the Argentine Navy, responsible"
    words = [w.text for w in textutil.split_words(text)]
    per, approx = ipa.transcribe_words(words, "en-US")
    assert len(per) == len(words)
    assert per[1] is None                      # "14" no tiene letras
    assert per[7] == "rɪˈspɑnsəbəl"            # "responsible"
    assert per[6] == ipa.word("Navy", "en-US")[0]  # la coma no molesta
    assert approx is False


def test_syllables_text_and_ipa():
    from popsay import syllables as S

    assert S.ortho("responsible") == ["re", "spon", "si", "ble"]
    assert S.ortho("radar") == ["ra", "dar"]                 # el guionado no la corta: heurística
    assert "".join(S.ortho("documentation,")) == "documentation,"
    assert S.ortho("14") == ["14"]
    assert S.ipa("rɪˈspɑnsəbəl") == ["rɪ", "ˈspɑn", "sə", "bəl"]
    assert S.ipa("ˈnævəˈɡeɪʃən") == ["ˈnæ", "və", "ˈɡeɪ", "ʃən"]
    assert S.ipa("ðə") == ["ðə"]
    assert "".join(S.ipa("əˈproʊtʃ")) == "əˈproʊtʃ"


def test_syllable_timing():
    from popsay import syllables as S

    syl = S.ipa("rɪˈspɑnsəbəl")
    assert [S.active(f, syl) for f in (0.05, 0.4, 0.65, 0.95)] == [0, 1, 2, 3]
    assert S.map_index(3, 4, 3) == 2 and S.map_index(0, 4, 1) == 0
    assert textutil.position_at([(0, 0.5), (0.5, 1)], 0.75) == (1, 0.5)


def test_ortho_no_syllable_without_vowel():
    from popsay import syllables
    assert syllables.ortho("your") == ["your"]
    assert all(syllables._has_vowel(p) for p in syllables.ortho("yesterday"))
    assert "".join(syllables.ortho("pronunciation")) == "pronunciation"
