"""The four stages in order.

Order is a security property, not an implementation detail:

  ① scrub_in    before a model reads the page
  ② distill     the boundary
  ③ scrub_out   deterministic, before anything crosses back
  ④ classify    probabilistic, on what stage ③ produced

Stage ④ runs on the scrubbed text rather than the raw distillation so that the
classifier sees exactly what would have crossed. Every rejection returns the
same typed outcome: the caller learns the source was rejected, never which stage
rejected it or why, because a reason is a bypass oracle.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from hearthfetch.classify import ClassifierConfig, Verdict, classify
from hearthfetch.config import Config
from hearthfetch.contracts import Distillation
from hearthfetch.distill import DistillationFailed, distill
from hearthfetch.llm import ChatModel
from hearthfetch.scrub_in import SourceRejected
from hearthfetch.scrub_in import scrub as scrub_in
from hearthfetch.scrub_out import DistillationDropped
from hearthfetch.scrub_out import scrub as scrub_out


class Stage(StrEnum):
    """Where a document was rejected. Operator-facing metrics only."""

    INPUT_SCRUB = "input_scrub"
    DISTILL = "distill"
    OUTPUT_SCRUB = "output_scrub"
    CLASSIFY = "classify"


class DocumentRejected(Exception):
    def __init__(self, stage: Stage, detail: str) -> None:
        super().__init__(f"{stage}: {detail}")
        self.stage = stage


@dataclass(frozen=True, slots=True)
class Pipeline:
    config: Config
    distiller_model: ChatModel
    classifier_model: ChatModel

    def process(self, html: str, question: str, title: str = "") -> Distillation:
        try:
            page_text = scrub_in(html, self.config.scrub_policy)
        except SourceRejected as exc:
            raise DocumentRejected(Stage.INPUT_SCRUB, exc.rule) from None

        try:
            raw = distill(
                page_text,
                question,
                model=self.distiller_model,
                config=self.config.distiller,
            )
        except DistillationFailed as exc:
            # Nothing is returned on this path but an exception. A fallback to
            # page_text here would remove the boundary while everything still
            # appeared to work.
            raise DocumentRejected(Stage.DISTILL, str(exc)) from None

        try:
            content = scrub_out(raw)
        except DistillationDropped as exc:
            raise DocumentRejected(Stage.OUTPUT_SCRUB, exc.reason) from None

        if classify(
            content, model=self.classifier_model, config=self.config.classifier
        ) is Verdict.SIDE_EFFECT:
            raise DocumentRejected(Stage.CLASSIFY, "side_effect")

        return Distillation(content=content, title=title)
