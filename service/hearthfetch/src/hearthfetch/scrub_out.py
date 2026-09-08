"""③ Deterministic output scrub — the control.

The quarantined model authors prose; this service authors every URL. Nothing
URL-shaped leaves here, so a page cannot smuggle a destination into the
privileged context and out through a renderer.

Fail closed. If anything URL-shaped survives stripping, the document is dropped
rather than repaired: repair invites bypass, dropping does not.

Plain text only. Whitelisting is easier to get right than blacklisting markdown
syntaxes, a distillation needs no formatting, and markdown is not the only sink
a renderer offers.

What is complete here, and what is best-effort, stated plainly because the
difference matters more than the code:

  complete     schemed URLs, scheme-relative URLs, markdown link and image
               syntax, reference definitions, angle autolinks, raw HTML tags,
               data:/javascript:/file:/mailto:, and any dotted host carrying a
               path — the forms a model can actually turn into a request
  best-effort  a path-less bare hostname on an unlisted TLD, e.g. `evil.zz`.
               Completeness there needs the full IANA list, which then rejects
               `deploy.sh` and `notes.md`. Left best-effort deliberately: the
               classifier in stage four is the layer that sees prose naming a
               destination, and dropping every distillation that mentions a
               filename would cost more than it buys.
"""

from __future__ import annotations

import re
import unicodedata
from enum import StrEnum

MAX_CONTENT_CHARS = 8_000
_MAX_PASSES = 4

# --- stripping ---------------------------------------------------------------
_MD_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_REF_DEF = re.compile(r"^\s*\[[^\]]*\]:\s*\S+.*$", re.MULTILINE)
_MD_REF_USE = re.compile(r"\[([^\]]*)\]\[[^\]]*\]")
_ANGLE_AUTOLINK = re.compile(r"<[a-zA-Z][a-zA-Z0-9+.-]*:[^>\s]*>")
_HTML_TAG = re.compile(r"</?[a-zA-Z][^>]*>")
_SCHEME_URL = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*:(?://)?[^\s<>\"')\]]+")
_SCHEME_RELATIVE = re.compile(r"(?<![\w:])//[A-Za-z0-9]\S*")
_WWW_HOST = re.compile(r"\bwww\.\S+", re.IGNORECASE)
# Deliberately conservative. TLDs that collide with common file extensions —
# sh, py, rs, md, ai, so, is, it, me, in, no, do — are excluded: dropping a
# distillation because it mentioned `deploy.sh` is a worse trade than missing a
# bare hostname that carries no scheme and no path.
_TLDS = (
    "com|org|net|edu|gov|mil|int|info|biz|dev|app|xyz|online|site|store|tech|"
    "cloud|link|click|top|live|news|blog|wiki|zone|space|website|host|press|io|"
    "uk|de|fr|jp|cn|ru|br|au|ca|nl|se|ch|es|it|eu|us|co|nz|in|za|mx|pl|tr|kr"
)
# A dotted label sequence followed by a path separator is a URL whatever the
# TLD, and nothing else in prose looks like it — filenames do not carry a
# trailing path. This rule does the real work; the TLD list below only covers
# path-less bare hosts, where a complete answer would need the full IANA list
# and would then collide with `deploy.sh`, `notes.md`, `archive.zip`.
_HOST_WITH_PATH = re.compile(r"\b[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9-]+)+/\S*", re.IGNORECASE)
_BARE_HOST = re.compile(rf"\b[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9-]+)*\.(?:{_TLDS})\b", re.IGNORECASE)

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_INVISIBLE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff\U000E0000-\U000E007F]")

# --- the assertion -----------------------------------------------------------
# What must not survive. Broader than the strippers on purpose: the strippers
# are best-effort, this is the gate.
_URL_SHAPED = re.compile(
    r"[a-zA-Z][a-zA-Z0-9+.-]*://"
    r"|\b(?:data|javascript|vbscript|file|mailto)\s*:"
    r"|(?<![\w:])//[A-Za-z0-9]"
    rf"|\b[a-z0-9-]+\.(?:{_TLDS})\b"
    r"|\b[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9-]+)+/"
    r"|\]\(|!\[",
    re.IGNORECASE,
)


class DropReason(StrEnum):
    URL_SURVIVED = "url_survived"
    UNSTABLE = "unstable"
    EMPTY = "empty"


class DistillationDropped(Exception):
    """The distillation is discarded. Never repaired, never partially returned."""

    def __init__(self, reason: DropReason) -> None:
        super().__init__(str(reason))
        self.reason = reason


def _strip_once(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = _INVISIBLE.sub("", text)
    text = _CONTROL.sub("", text)
    text = _MD_REF_DEF.sub("", text)
    text = _MD_IMAGE.sub(r"\1", text)
    text = _MD_LINK.sub(r"\1", text)
    text = _MD_REF_USE.sub(r"\1", text)
    text = _ANGLE_AUTOLINK.sub("", text)
    text = _HTML_TAG.sub("", text)
    text = _SCHEME_URL.sub("", text)
    text = _SCHEME_RELATIVE.sub("", text)
    text = _WWW_HOST.sub("", text)
    text = _HOST_WITH_PATH.sub("", text)
    text = _BARE_HOST.sub("", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*", "\n\n", text)
    return text.strip()


def scrub(text: str) -> str:
    """Return plain prose safe to hand back, or raise DistillationDropped.

    Stripping runs to a fixed point. `htt<b>p</b>s://` and zero-width-split URLs
    are the same strip-once evasion in different costumes: the first pass removes
    the wrapper, and only a second pass sees the URL underneath. Needing that
    second pass is fine; still carrying a URL after them is not.
    """
    for _ in range(_MAX_PASSES):
        nxt = _strip_once(text)
        if nxt == text:
            break
        text = nxt
    else:
        raise DistillationDropped(DropReason.UNSTABLE)

    if _URL_SHAPED.search(text):
        raise DistillationDropped(DropReason.URL_SURVIVED)
    if not text.strip():
        raise DistillationDropped(DropReason.EMPTY)
    return text[:MAX_CONTENT_CHARS]
