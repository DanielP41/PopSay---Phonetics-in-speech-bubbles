import pytest
from app.ipa import phones_to_ipa, transcribe


@pytest.mark.parametrize(
    ("text", "accent", "expected"),
    [
        ("approach", "en-US", "əˈproʊtʃ"),
        ("approach", "en-GB", "əˈprəʊtʃ"),
        ("validate", "en-US", "ˈvælədeɪt"),
        ("required", "en-US", "rɪˈkwaɪɚd"),
        ("required", "en-GB", "rɪˈkwaɪəd"),
        ("reviews", "en-GB", "rɪˈvjuːz"),
        ("water", "en-US", "ˈwɔtɚ"),
        ("water", "en-GB", "ˈwɔːtə"),
        ("car", "en-GB", "kɑː"),
        ("hot", "en-US", "hɑt"),
        ("hot", "en-GB", "hɒt"),
        ("after", "en-GB", "ˈɑːftə"),
        ("happy", "en-US", "ˈhæpi"),
        ("strategy", "en-US", "ˈstrætədʒi"),
        ("computer", "en-US", "kəmˈpjutɚ"),
    ],
)
def test_word(text, accent, expected):
    assert transcribe(text, accent).ipa == expected


def test_one_syllable_has_no_stress_mark():
    assert "ˈ" not in transcribe("cat", "en-US").ipa


def test_secondary_stress_only_before_primary():
    assert transcribe("understand", "en-US").ipa == "ˌʌndɚˈstænd"
    assert "ˌ" not in transcribe("validate", "en-US").ipa


def test_onset_maximization_puts_stress_before_cluster():
    # S T R es un ataque válido: la marca va antes de "str", no en el medio.
    assert phones_to_ipa(["D", "IH0", "S", "T", "R", "OY1"], "en-US") == "dɪˈstrɔɪ"


def test_sentence_and_punctuation():
    r = transcribe("Required reviews, validate approach!", "en-US")
    assert r.ipa == "rɪˈkwaɪɚd rɪˈvjuz ˈvælədeɪt əˈproʊtʃ"
    assert [w.word for w in r.words] == ["Required", "reviews", "validate", "approach"]
    assert r.approximate is False


def test_contraction_and_curly_apostrophe():
    assert transcribe("don’t", "en-US").ipa == transcribe("don't", "en-US").ipa
    assert transcribe("don't", "en-US").words[0].source == "cmudict"


def test_unknown_word_is_marked():
    r = transcribe("hello zxqvbn", "en-US")
    assert r.unknown == ["zxqvbn"] or r.words[1].source == "g2p"
    if r.unknown:
        assert "‹zxqvbn›" in r.ipa


def test_uk_is_flagged_as_approximate():
    assert transcribe("hello", "en-GB").approximate is True


def test_bad_accent():
    with pytest.raises(ValueError):
        transcribe("hello", "es-AR")
