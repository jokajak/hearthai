//! The offline stage, end to end, and the result gate at the end of it.
//!
//! The ordering is the contract. Nothing is released until every required scan
//! has finished without a match, and the thing released is the exact text those
//! scans covered. The failure paths all converge on a fixed code, so a caller
//! learns that a response was withheld and nothing about what was in it.
//!
//! Detection never repairs: there is no path here that deletes a matching
//! paragraph and returns the rest, and no path that answers a match by trying
//! another converter.

use std::path::Path;

use hearthai_web_fetch_contracts::{
    ConversionMethod, ErrorCode, Inspection, OutputFormat, WebFetchData, WebFetchEnvelope, is_html_media_type,
};
use hearthai_web_fetch_worker::artifact::{self, ResponseArtifact};

use crate::conversion::{self, Candidate};
use crate::decode::{decode_charset, decode_content_encoding};
use crate::detect::{Detector, DetectorLimits, ScanBudget, Verdict};
use crate::normalize::{entity_decoded, unicode_folded};
use crate::quality::{SourceShape, assess};
use crate::{InspectionError, InspectionLimits};

/// What the inspector produces: a complete envelope, and nothing else.
///
/// There is no variant carrying a body alongside a failure, and no "preview"
/// the caller could read before the gate ran.
pub type Outcome = WebFetchEnvelope;

pub struct Inspector<'a> {
    pub detector: &'a dyn Detector,
    pub limits: InspectionLimits,
    pub detector_limits: DetectorLimits,
}

impl Inspector<'_> {
    /// Reads the run's artifact, inspects it, and returns the envelope.
    ///
    /// `run_id` comes from the substrate. The artifact's own claim about which
    /// run it belongs to is checked against it rather than believed.
    pub fn inspect_run(&self, artifact_root: &Path, run_id: &str, format: OutputFormat) -> Outcome {
        let budget = ScanBudget::new(self.detector, self.detector_limits);
        match artifact::read_bound(artifact_root, run_id) {
            Ok((artifact, body)) => match self.inspect(&artifact, &body, format, &budget) {
                Ok(data) => WebFetchEnvelope::admitted(budget.policy_id(), data),
                Err(failure) => self.withhold(failure, &budget),
            },
            // A stale, altered, cross-run or missing artifact is not a response.
            Err(_) => WebFetchEnvelope::failed(ErrorCode::FetchFailed, Inspection::not_run())
                .expect("fetch_failed is not a rejection"),
        }
    }

    fn withhold(&self, failure: InspectionError, budget: &ScanBudget<'_>) -> Outcome {
        let policy_id = budget.policy_id().to_owned();
        match failure {
            InspectionError::Matched => WebFetchEnvelope::rejected(policy_id),
            InspectionError::Failed => {
                WebFetchEnvelope::failed(ErrorCode::InspectionFailed, Inspection::failed(Some(policy_id)))
                    .expect("inspection_failed is not a rejection")
            }
            InspectionError::UnsupportedContent => {
                WebFetchEnvelope::failed(ErrorCode::UnsupportedContent, Inspection::failed(Some(policy_id)))
                    .expect("unsupported_content is not a rejection")
            }
            InspectionError::ResponseLimit => WebFetchEnvelope::failed(
                ErrorCode::ResponseLimitExceeded,
                Inspection::failed(Some(policy_id)),
            )
            .expect("response_limit_exceeded is not a rejection"),
            InspectionError::Storage => {
                WebFetchEnvelope::failed(ErrorCode::FetchFailed, Inspection::not_run())
                    .expect("fetch_failed is not a rejection")
            }
        }
    }

    fn inspect(
        &self,
        artifact: &ResponseArtifact,
        wire: &[u8],
        format: OutputFormat,
        budget: &ScanBudget<'_>,
    ) -> Result<WebFetchData, InspectionError> {
        // 1. The bytes exactly as they arrived, before anything interprets them.
        check(budget.scan(wire))?;

        // 2. Content-encoding, under its own ceiling.
        let decoded = decode_content_encoding(
            artifact.content_encoding.as_deref(),
            wire,
            self.limits.max_decoded_bytes,
        )?;
        check(budget.scan(&decoded))?;

        // 3. Charset. The complete body, comments, scripts and hidden markup
        //    included: nothing has been removed at this point.
        let text = decode_charset(&decoded, artifact.charset.as_deref())?;
        check(budget.scan(text.as_bytes()))?;

        // 4. Bounded inspection-only forms. Scanned, then dropped.
        //
        //    Two forms, one pass each: entities undone, and then the invisible
        //    characters removed from that. The second subsumes folding on its
        //    own, and it is what catches the combination - an entity-encoded
        //    letter with a zero-width space after it - that either form alone
        //    would miss. This is not recursive decoding: each pass runs once.
        let entities = entity_decoded(&text, self.limits.max_normalized_bytes);
        check(budget.scan(entities.as_bytes()))?;
        check(budget.scan(unicode_folded(&entities, self.limits.max_normalized_bytes).as_bytes()))?;

        // 5. Conversion, with every candidate inspected before its quality is
        //    even considered.
        let (content, conversion_method) = self.render(&text, artifact, format, budget)?;

        // 6. The final returned strings, and their combined representation.
        let final_url = artifact.final_url.clone();
        check(budget.scan(final_url.as_bytes()))?;
        check(budget.scan(format!("{final_url}\n{content}").as_bytes()))?;

        if content.len() > self.limits.max_content_bytes {
            return Err(InspectionError::ResponseLimit);
        }

        Ok(WebFetchData {
            source_id: artifact.source_id.clone(),
            final_url,
            http_status: artifact.http_status,
            content_type: artifact.media_type.clone(),
            retrieved_at: artifact.retrieved_at.clone(),
            format,
            conversion_method,
            content,
        })
    }

    fn render(
        &self,
        text: &str,
        artifact: &ResponseArtifact,
        format: OutputFormat,
        budget: &ScanBudget<'_>,
    ) -> Result<(String, ConversionMethod), InspectionError> {
        if !is_html_media_type(&artifact.media_type) {
            // Non-HTML text is returned as it was decoded. No rewriting, and no
            // LLM anywhere in this path.
            return Ok((text.to_owned(), ConversionMethod::Passthrough));
        }
        if format == OutputFormat::Html {
            // Inert source: the format skips conversion, never scanning, and
            // the caller receives text rather than a rendered document.
            return Ok((text.to_owned(), ConversionMethod::Source));
        }

        let shape = SourceShape::of(text);
        let mut best: Option<Candidate> = None;
        for method in conversion::chain() {
            let Some(candidate) = conversion::convert(method, text) else {
                // An ordinary conversion failure. This is the only reason, along
                // with poor quality below, to try the next converter.
                continue;
            };
            let rendered = finish(&candidate, format);
            // Inspect before judging. A match in a candidate that would have
            // been discarded still rejects the response.
            check(budget.scan(rendered.as_bytes()))?;
            if assess(&candidate.markdown, &shape).is_usable() {
                return Ok((rendered, candidate.method));
            }
            if best.is_none() && !candidate.markdown.trim().is_empty() {
                best = Some(candidate.clone());
            }
            if candidate.method == ConversionMethod::Basic && !candidate.markdown.trim().is_empty() {
                // The last converter deliberately keeps what the others trimmed
                // away; an imperfect whole-body conversion beats returning
                // nothing for a page the extractors disagreed about.
                best = Some(candidate);
            }
        }
        match best {
            Some(candidate) => Ok((finish(&candidate, format), candidate.method)),
            // Every converter produced nothing usable. That is a page v1 cannot
            // render, not a page to return empty.
            None => Err(InspectionError::UnsupportedContent),
        }
    }
}

fn finish(candidate: &Candidate, format: OutputFormat) -> String {
    match format {
        OutputFormat::Text => conversion::markdown_to_text(&candidate.markdown),
        _ => candidate.markdown.clone(),
    }
}

/// A match ends the response. A failed scan ends it too, and neither is allowed
/// to fall through to another attempt.
fn check(verdict: Verdict) -> Result<(), InspectionError> {
    match verdict {
        Verdict::NoMatch => Ok(()),
        Verdict::Match => Err(InspectionError::Matched),
        Verdict::Error => Err(InspectionError::Failed),
    }
}
