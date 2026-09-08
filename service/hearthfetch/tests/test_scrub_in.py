import pytest

from hearthfetch.scrub_in import RejectRule, SourceRejected, scrub

from hearthfetch.scrub_in import ScrubPolicy

from corpus import (
    CSS_COLLISION_CORPUS,
    CSS_HIDDEN_CORPUS,
    EVASION_CORPUS,
    FALSE_POSITIVE_CORPUS,
    PAYLOAD,
    REJECT_CORPUS,
    STRIP_CORPUS,
)

LENIENT = ScrubPolicy(css_hidden_rejects=False)


@pytest.mark.parametrize("name", sorted(STRIP_CORPUS))
def test_strip_class_removes_payload_and_keeps_the_page(name):
    text = scrub(STRIP_CORPUS[name])
    assert PAYLOAD not in text
    # Both directions: a scrubber that eats real content is a regression.
    assert "Real text." in text


@pytest.mark.parametrize("name", sorted(REJECT_CORPUS))
def test_reject_class_discards_the_whole_source(name):
    with pytest.raises(SourceRejected):
        scrub(REJECT_CORPUS[name])


@pytest.mark.parametrize("name", sorted(EVASION_CORPUS))
def test_evasion_is_caught_because_detection_runs_after_every_pass(name):
    """A payload only visible once tags are stripped is still a payload."""
    with pytest.raises(SourceRejected):
        scrub(EVASION_CORPUS[name])


@pytest.mark.parametrize("name", sorted(FALSE_POSITIVE_CORPUS))
def test_ordinary_pages_survive(name):
    """The corpus that decides whether this ships."""
    text = scrub(FALSE_POSITIVE_CORPUS[name])
    assert text.strip()


@pytest.mark.parametrize("name", sorted(CSS_HIDDEN_CORPUS))
def test_css_hidden_rejects_under_the_default_policy(name):
    with pytest.raises(SourceRejected) as caught:
        scrub(CSS_HIDDEN_CORPUS[name])
    assert caught.value.rule is RejectRule.CSS_HIDDEN


@pytest.mark.parametrize("name", sorted(CSS_HIDDEN_CORPUS))
def test_css_hidden_still_never_reaches_the_model_when_only_stripped(name):
    """The lenient policy keeps the page but must not keep the payload."""
    text = scrub(CSS_HIDDEN_CORPUS[name], LENIENT)
    assert PAYLOAD not in text
    assert "Real text." in text


@pytest.mark.parametrize("name", sorted(CSS_COLLISION_CORPUS))
def test_the_price_of_the_aggressive_policy_is_measured_not_assumed(name):
    """These are legitimate pages. The default policy discards them.

    Documented deliberately: screen-reader-only text and CSS fade-ins are real
    markup, so `css_hidden_rejects` trades availability for the property that
    every payload must evade detection at once. The lenient policy keeps them.
    """
    with pytest.raises(SourceRejected):
        scrub(CSS_COLLISION_CORPUS[name])
    assert "Main content." in scrub(CSS_COLLISION_CORPUS[name], LENIENT)


def test_an_animation_starting_at_zero_opacity_is_not_treated_as_hiding():
    """Cheap discriminator that recovers the most common collision outright."""
    html = "<p>Main content.</p><div style='opacity:0;transition:opacity .3s'>Fades in</div>"
    text = scrub(html)
    assert "Main content." in text and "Fades in" in text


def test_rejection_names_a_rule_for_operators_but_carries_no_content():
    with pytest.raises(SourceRejected) as caught:
        scrub(REJECT_CORPUS["unicode_tag_block"])
    assert caught.value.rule is RejectRule.UNICODE_TAG_BLOCK
    assert PAYLOAD not in str(caught.value)


def test_whitespace_is_collapsed_without_losing_paragraphs():
    text = scrub("<p>One</p>\n\n\n   <p>Two</p>")
    assert "One" in text and "Two" in text
    assert "   " not in text


def test_empty_and_trivial_documents_do_not_explode():
    assert scrub("") == ""
    assert scrub("<html><body></body></html>") == ""


def test_deeply_nested_hidden_subtree_is_fully_dropped():
    html = "<p>Keep.</p><div style='display:none'>" + "<div>" * 50 + PAYLOAD + "</div>" * 50 + "</div><p>Also keep.</p>"
    text = scrub(html)
    assert PAYLOAD not in text
    assert "Keep." in text and "Also keep." in text
