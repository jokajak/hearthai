"""① Deterministic input scrub.

Two rule classes, split by the question *would a legitimate publisher ever do
this?*

  strip-class   common in benign pages, removed quietly
  reject-class  essentially never legitimate, and one hit discards the source

Rejecting rather than cleaning is the point. Stripping gives an attacker many
attempts in one page: plant twenty payloads, nineteen are removed, one novel
technique survives, and the page still flows through. Rejection turns that OR
into an AND — every payload must evade detection at once.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from html.parser import HTMLParser

# Content of these elements is never page text.
SKIP_CONTENT_TAGS = frozenset({"script", "style", "noscript", "template", "svg", "head"})

# Never increment nesting depth: they have no closing tag to wait for.
VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
})

# Unicode Tag block: encodes invisible ASCII. No legitimate use in page text.
TAG_BLOCK = re.compile(r"[\U000E0000-\U000E007F]")
# Explicit bidi overrides and isolates.
BIDI_CONTROLS = re.compile(r"[‪-‮⁦-⁩]")
# Zero-width characters wedged between two ASCII alphanumerics. ZWJ and ZWNJ are
# legitimate in Indic scripts and emoji sequences, so the ASCII-both-sides
# requirement is what keeps this from rejecting the ordinary web.
IN_WORD_ZERO_WIDTH = re.compile(r"(?<=[A-Za-z0-9])[​-‍﻿](?=[A-Za-z0-9])")
# Any right-to-left script; presence makes bidi controls plausible rather than hostile.
RTL_SCRIPT = re.compile(r"[֐-ࣿיִ-﷿ﹰ-﻿]")

_MAX_PASSES = 4

# Hidden by structure — benign and extremely common.
_STRUCTURAL_HIDDEN = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*hidden", re.IGNORECASE
)
# Hidden by means that *can* indicate an attacker rendering text a human cannot
# read — but which also collide with real, common markup. Measured collisions:
#
#   position:absolute;left:-9999px   the classic screen-reader-only pattern
#   opacity:0;transition:opacity .3s a CSS fade-in's starting state
#   opacity:0                        a lazy-loaded image placeholder
#   font-size:0                      the inline-block whitespace-removal hack
#
# So this is NOT the "no legitimate publisher would do this" class, whatever an
# earlier draft of the plan claimed. It is a policy dial: see ScrubPolicy.
_CSS_HIDDEN_SUBTREE = re.compile(
    r"opacity\s*:\s*0(?!\.[1-9])"
    r"|text-indent\s*:\s*-\d{3,}"
    r"|(?:left|top|right|bottom)\s*:\s*-\d{4,}",
    re.IGNORECASE,
)
# font-size is inherited but a child can override it, and the inline-block
# whitespace hack does exactly that: font-size:0 on a container whose children
# reset it. Dropping that subtree would delete the page's real content, so this
# suppresses only the element's OWN text, matching how a browser renders it.
_CSS_HIDDEN_OWN_TEXT = re.compile(r"font-size\s*:\s*0(?![.1-9])", re.IGNORECASE)
# An opacity of zero that is plainly the start of an animation is not an attack.
_ANIMATED = re.compile(r"transition|animation", re.IGNORECASE)


class RejectRule(StrEnum):
    UNICODE_TAG_BLOCK = "unicode_tag_block"
    BIDI_OVERRIDE = "bidi_override"
    IN_WORD_ZERO_WIDTH = "in_word_zero_width"
    CSS_HIDDEN = "css_hidden"
    UNSTABLE = "unstable"


@dataclass(frozen=True, slots=True)
class ScrubPolicy:
    """How aggressively CSS-hidden text is treated.

    The Unicode rules always reject: the Tag block, bidi overrides in a
    non-RTL document, and zero-width characters wedged between ASCII letters
    have no legitimate use, and rejecting on them costs nothing real.

    CSS-hidden text is the judgement call. Rejecting gives the property that
    makes rejection worth having — an attacker who sprays several techniques is
    caught by whichever lands first, so every payload must evade detection at
    once — at the cost of discarding pages that use off-screen accessibility
    text or CSS fade-ins. Stripping keeps those pages while giving an attacker
    one free attempt per technique the detector does not know.

    Default is to reject, because a discarded page costs an answer and an
    admitted one costs more. Watch the source-rejection metric: a rate that
    makes the tool useless is the signal to flip this, not a reason to weaken
    the Unicode rules.
    """

    css_hidden_rejects: bool = True


DEFAULT_POLICY = ScrubPolicy()


class SourceRejected(Exception):
    """The source is discarded whole. Never repaired, never partially used."""

    def __init__(self, rule: RejectRule) -> None:
        super().__init__(str(rule))
        self.rule = rule


class _Extractor(HTMLParser):
    def __init__(self, policy: ScrubPolicy) -> None:
        super().__init__(convert_charrefs=True)
        self.policy = policy
        self.chunks: list[str] = []
        self._skip_depth = 0
        self._depth = 0
        self._text_suppressed_at: set[int] = set()

    @staticmethod
    def _read_attrs(attrs) -> tuple[str, bool]:
        style = ""
        hidden = False
        for name, value in attrs:
            lowered = name.lower()
            if lowered == "style" and value:
                style = value
            elif lowered == "hidden":
                hidden = True
            elif lowered == "aria-hidden" and (value or "").lower() == "true":
                hidden = True
        return style, hidden

    def _css_signal(self, style: str) -> tuple[bool, bool]:
        """(hides a subtree, hides only its own text)."""
        if not style:
            return False, False
        if _ANIMATED.search(style):
            # opacity:0 alongside a transition is a fade-in's start, not hiding.
            return False, bool(_CSS_HIDDEN_OWN_TEXT.search(style))
        return bool(_CSS_HIDDEN_SUBTREE.search(style)), bool(_CSS_HIDDEN_OWN_TEXT.search(style))

    def handle_starttag(self, tag: str, attrs) -> None:
        void = tag in VOID_TAGS
        if self._skip_depth:
            if not void:
                self._skip_depth += 1
                self._depth += 1
            return
        style, hidden = self._read_attrs(attrs)
        hides_subtree, hides_own_text = self._css_signal(style)
        if (hides_subtree or hides_own_text) and self.policy.css_hidden_rejects:
            raise SourceRejected(RejectRule.CSS_HIDDEN)
        if not void:
            self._depth += 1
        if tag in SKIP_CONTENT_TAGS or hidden or hides_subtree or (
            style and _STRUCTURAL_HIDDEN.search(style)
        ):
            if not void:
                self._skip_depth = 1
            return
        if hides_own_text and not void:
            self._text_suppressed_at.add(self._depth)

    def handle_startendtag(self, tag: str, attrs) -> None:
        # Self-closing: inspect attributes, but never open a depth to close.
        if self._skip_depth:
            return
        style, _ = self._read_attrs(attrs)
        hides_subtree, hides_own_text = self._css_signal(style)
        if (hides_subtree or hides_own_text) and self.policy.css_hidden_rejects:
            raise SourceRejected(RejectRule.CSS_HIDDEN)

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID_TAGS:
            return
        self._text_suppressed_at.discard(self._depth)
        if self._skip_depth:
            self._skip_depth -= 1
        if self._depth:
            self._depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth or self._depth in self._text_suppressed_at:
            return
        self.chunks.append(data)

    def handle_comment(self, data: str) -> None:
        # Dropped, never read. A comment is invisible to a reader and is one of
        # the oldest places to hide an instruction.
        return

    # Attribute-borne text (alt, title, placeholder, data-*) and <meta> content
    # are never collected: handle_data is the only path into `chunks`.


def _extract(html: str, policy: ScrubPolicy) -> str:
    parser = _Extractor(policy)
    try:
        parser.feed(html)
        parser.close()
    except SourceRejected:
        raise
    except Exception:
        # A source we cannot parse is a source we cannot vouch for.
        raise SourceRejected(RejectRule.UNSTABLE)
    return "".join(parser.chunks)


def _detect(text: str) -> None:
    if TAG_BLOCK.search(text):
        raise SourceRejected(RejectRule.UNICODE_TAG_BLOCK)
    if BIDI_CONTROLS.search(text) and not RTL_SCRIPT.search(text):
        raise SourceRejected(RejectRule.BIDI_OVERRIDE)
    if IN_WORD_ZERO_WIDTH.search(text):
        raise SourceRejected(RejectRule.IN_WORD_ZERO_WIDTH)


def _normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = "".join(ch for ch in text if ch == "\n" or ch == "\t" or ord(ch) >= 0x20)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*", "\n\n", text)
    return text.strip()


def scrub(html: str, policy: ScrubPolicy = DEFAULT_POLICY) -> str:
    """Extract readable text, or raise SourceRejected.

    Detection runs after every transform pass, not only on the original input.
    That is what catches an evasion which only becomes visible once tags have
    been removed — `ev<span>​</span>il` is not an in-word zero-width until
    the span is gone — and normalisation sequences that NFKC synthesises.
    """
    text = _extract(html, policy)
    for _ in range(_MAX_PASSES):
        _detect(text)
        nxt = _normalise(text)
        if nxt == text:
            return text
        text = nxt
    # Still moving after several passes: pathological, and not worth reasoning
    # about further.
    raise SourceRejected(RejectRule.UNSTABLE)
