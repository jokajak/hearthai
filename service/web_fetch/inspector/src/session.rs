//! The inspection lifecycle, as states rather than bookkeeping.
//!
//! A response moves `Received -> Decoded -> Normalized -> Inspected -> data`,
//! and each transition consumes the previous state. That is the whole point:
//! [`Inspected::release`] is the only way to obtain a [`WebFetchData`], it can
//! only be called on a value that [`Normalized::convert`] produced, and so on
//! back to the authenticated handoff. Skipping a required pass is not something
//! a future change can do by forgetting a flag - there is no flag, and the type
//! for the next step does not exist until the previous one has run.

use std::time::{Duration, Instant};

use time::OffsetDateTime;
use time::format_description::well_known::Rfc3339;

use hearthai_webfetch_contracts::artifact::FetchArtifact;
use hearthai_webfetch_contracts::envelope::{ErrorCode, InspectionStatus};
use hearthai_webfetch_contracts::handoff::Handed;
use hearthai_webfetch_contracts::limits::MAX_CONTENT_CHARS;
use hearthai_webfetch_contracts::media::MediaType;
use hearthai_webfetch_contracts::web_fetch::{ConversionMethod, Format, WebFetchData};

use crate::convert::{Candidate, Converter, Quality, SourceSignals, quality};
use crate::decode::{decode_charset, decode_content_encoding};
use crate::detect::{Detector, InspectionFailure, Verdict};
use crate::normalize::{decode_entities, deobfuscate};

/// Why the pipeline stopped, and what the inspection had concluded when it did.
#[derive(Debug, Clone)]
pub struct Stop {
    pub code: ErrorCode,
    pub status: InspectionStatus,
    pub matched: Vec<String>,
}

impl Stop {
    pub fn not_run(code: ErrorCode) -> Self {
        Self {
            code,
            status: InspectionStatus::NotRun,
            matched: Vec::new(),
        }
    }

    pub fn failed(code: ErrorCode) -> Self {
        Self {
            code,
            status: InspectionStatus::Failed,
            matched: Vec::new(),
        }
    }

    fn rejected(matched: Vec<String>) -> Self {
        Self {
            code: ErrorCode::ContentRejected,
            status: InspectionStatus::Match,
            matched,
        }
    }
}

impl From<InspectionFailure> for Stop {
    fn from(_: InspectionFailure) -> Self {
        Self::failed(ErrorCode::InspectionFailed)
    }
}

/// What the run recorded about itself. Counts, codes and digests only.
#[derive(Debug, Default, Clone)]
pub struct Telemetry {
    pub http_status: Option<u16>,
    pub redirects: Option<u32>,
    pub requested_url_sha256: Option<String>,
    pub wire_bytes: Option<usize>,
    pub decoded_bytes: Option<usize>,
    pub content_chars: Option<usize>,
    pub conversion_method: Option<ConversionMethod>,
}

/// The scanner, the budget and the record, carried through every state.
pub struct Inspection<'bundle> {
    detector: Detector<'bundle>,
    started: Instant,
    deadline: Duration,
    telemetry: Telemetry,
}

impl<'bundle> Inspection<'bundle> {
    pub fn new(detector: Detector<'bundle>, deadline: Duration) -> Self {
        Self {
            detector,
            started: Instant::now(),
            deadline,
            telemetry: Telemetry::default(),
        }
    }

    pub fn telemetry(&self) -> &Telemetry {
        &self.telemetry
    }

    pub fn scans(&self) -> usize {
        self.detector.scans()
    }

    pub fn elapsed(&self) -> Duration {
        self.started.elapsed()
    }

    /// Scan one input. Every required pass goes through here, and a match or a
    /// scanner failure ends the response rather than being reported upward as a
    /// value someone could ignore.
    fn scan(&mut self, data: &[u8]) -> Result<(), Stop> {
        match self.detector.scan(data)? {
            Verdict::NoMatch => Ok(()),
            Verdict::Match(rules) => Err(Stop::rejected(rules)),
        }
    }

    fn check_deadline(&self, status: InspectionStatus) -> Result<(), Stop> {
        if self.started.elapsed() >= self.deadline {
            return Err(Stop {
                code: ErrorCode::DeadlineExceeded,
                status,
                matched: Vec::new(),
            });
        }
        Ok(())
    }
}

/// An authenticated response of a media type this build handles. Nothing has
/// looked at its bytes yet.
pub struct Received<'session, 'bundle> {
    inspection: &'session mut Inspection<'bundle>,
    artifact: Box<FetchArtifact>,
    media: MediaType,
    body: Vec<u8>,
}

impl<'session, 'bundle> Received<'session, 'bundle> {
    /// Take what the handoff produced, or turn the fetch stage's own failure
    /// into this run's result without inspecting anything.
    pub fn open(
        inspection: &'session mut Inspection<'bundle>,
        handed: Handed,
    ) -> Result<Self, Stop> {
        let (artifact, body) = match handed {
            Handed::Failed(code) => return Err(Stop::not_run(code)),
            Handed::Fetched { artifact, body } => (artifact, body),
        };
        inspection.telemetry.http_status = Some(artifact.http_status);
        inspection.telemetry.redirects = Some(artifact.redirects);
        inspection.telemetry.requested_url_sha256 = Some(artifact.requested_url_sha256.clone());
        inspection.telemetry.wire_bytes = Some(body.len());

        let declared = artifact
            .content_type
            .clone()
            .ok_or(Stop::not_run(ErrorCode::UnsupportedContent))?;
        let media = MediaType::parse(&declared)
            .map_err(|_| Stop::not_run(ErrorCode::UnsupportedContent))?;
        if !media.is_supported() {
            return Err(Stop::not_run(ErrorCode::UnsupportedContent));
        }
        Ok(Self {
            inspection,
            artifact,
            media,
            body,
        })
    }

    /// Scan the bytes as they arrived and the response metadata that will be
    /// reported back, then decode.
    ///
    /// The metadata is scanned because it is remote data too: a final URL and a
    /// media type come from the source as surely as the body does.
    pub fn scan_wire(self) -> Result<Decoded<'session, 'bundle>, Stop> {
        let Self {
            inspection,
            artifact,
            media,
            body,
        } = self;
        inspection.check_deadline(InspectionStatus::NotRun)?;
        inspection.scan(&body)?;
        inspection.scan(artifact.final_url.as_bytes())?;
        inspection.scan(media.header_value().as_bytes())?;

        let decoded_bytes = decode_content_encoding(&body, artifact.content_encoding.as_deref())
            .map_err(Stop::not_run)?;
        let text = decode_charset(&decoded_bytes, &media).map_err(Stop::not_run)?;
        inspection.telemetry.decoded_bytes = Some(decoded_bytes.len());
        Ok(Decoded {
            inspection,
            artifact,
            media,
            text,
        })
    }
}

/// The response decoded to text, unchanged and unexamined.
pub struct Decoded<'session, 'bundle> {
    inspection: &'session mut Inspection<'bundle>,
    artifact: Box<FetchArtifact>,
    media: MediaType,
    text: String,
}

impl<'session, 'bundle> Decoded<'session, 'bundle> {
    /// Scan the complete decoded body and the two inspection-only views of it.
    ///
    /// Nothing is removed first: comments, script bodies and hidden markup are
    /// all still present. The normalized forms are scanned and discarded; they
    /// never become the returned content.
    pub fn scan_text(self) -> Result<Normalized<'session, 'bundle>, Stop> {
        let Self {
            inspection,
            artifact,
            media,
            text,
        } = self;
        inspection.check_deadline(InspectionStatus::Failed)?;
        inspection.scan(text.as_bytes())?;

        let entities = decode_entities(&text);
        let folded = deobfuscate(&entities);
        inspection.scan(entities.as_bytes())?;
        inspection.scan(folded.as_bytes())?;
        Ok(Normalized {
            inspection,
            artifact,
            media,
            text,
        })
    }
}

/// Every form of the response has been scanned. Conversion may begin.
pub struct Normalized<'session, 'bundle> {
    inspection: &'session mut Inspection<'bundle>,
    artifact: Box<FetchArtifact>,
    media: MediaType,
    text: String,
}

impl<'session, 'bundle> Normalized<'session, 'bundle> {
    /// Produce the content to return, inspecting every candidate before its
    /// quality is considered.
    ///
    /// A match in any candidate rejects the response even when a later
    /// converter would have dropped the matching text, and only an ordinary
    /// conversion failure or poor output moves the chain along: a detection or
    /// an inspection failure ends it here.
    pub fn convert(self, chain: &[&dyn Converter]) -> Result<Inspected<'session, 'bundle>, Stop> {
        let Self {
            inspection,
            artifact,
            media,
            text,
        } = self;

        // Inert source and non-HTML text skip conversion, but not inspection.
        if artifact.format == Format::Html {
            return Self::inspected(inspection, artifact, media, text, ConversionMethod::Source);
        }
        if !media.is_html() {
            return Self::inspected(
                inspection,
                artifact,
                media,
                text,
                ConversionMethod::Passthrough,
            );
        }

        let signals = SourceSignals::of(&text);
        let mut fallback: Option<Candidate> = None;
        for converter in chain {
            // One deadline and one budget cover the whole chain.
            inspection.check_deadline(InspectionStatus::Failed)?;
            let Some(candidate) = converter.convert(&text, artifact.format) else {
                continue;
            };
            inspection.scan(candidate.content.as_bytes())?;
            if quality(&candidate, artifact.format, signals) == Quality::Usable {
                return Ok(Inspected {
                    inspection,
                    artifact,
                    media,
                    candidate,
                });
            }
            // Keep the first non-empty output in case nothing better appears.
            if fallback.is_none() && !candidate.content.trim().is_empty() {
                fallback = Some(candidate);
            }
        }
        // Every converter ran and none produced good output. Returning the best
        // of them is honest; there is nothing else to return.
        let candidate = fallback.ok_or_else(|| Stop::failed(ErrorCode::UnsupportedContent))?;
        Ok(Inspected {
            inspection,
            artifact,
            media,
            candidate,
        })
    }

    fn inspected(
        inspection: &'session mut Inspection<'bundle>,
        artifact: Box<FetchArtifact>,
        media: MediaType,
        content: String,
        method: ConversionMethod,
    ) -> Result<Inspected<'session, 'bundle>, Stop> {
        let candidate = Candidate { content, method };
        inspection.scan(candidate.content.as_bytes())?;
        Ok(Inspected {
            inspection,
            artifact,
            media,
            candidate,
        })
    }
}

/// Content that has been produced and scanned, but not yet released.
pub struct Inspected<'session, 'bundle> {
    inspection: &'session mut Inspection<'bundle>,
    artifact: Box<FetchArtifact>,
    media: MediaType,
    candidate: Candidate,
}

impl Inspected<'_, '_> {
    /// Build the result and scan the exact fields it will carry.
    ///
    /// This is the only constructor of a releasable payload in the inspector,
    /// and it runs the last required pass over the combined representation of
    /// what a caller would receive.
    pub fn release(self) -> Result<WebFetchData, Stop> {
        let Self {
            inspection,
            artifact,
            media,
            candidate,
        } = self;
        if candidate.content.chars().count() > MAX_CONTENT_CHARS {
            return Err(Stop::failed(ErrorCode::ResponseLimitExceeded));
        }
        // Reformatted from the parsed value rather than echoed, so the result
        // carries a timestamp this build produced.
        let retrieved_at = OffsetDateTime::parse(&artifact.retrieved_at, &Rfc3339)
            .map_err(|_| Stop::failed(ErrorCode::InternalError))?
            .format(&Rfc3339)
            .map_err(|_| Stop::failed(ErrorCode::InternalError))?;

        let data = WebFetchData {
            source_id: artifact.source_id.clone(),
            final_url: artifact.final_url.clone(),
            http_status: artifact.http_status,
            content_type: media.header_value(),
            retrieved_at,
            format: artifact.format,
            conversion_method: candidate.method,
            content: candidate.content,
        };
        let combined = format!(
            "{}\n{}\n{}",
            data.final_url, data.content_type, data.content
        );
        inspection.check_deadline(InspectionStatus::Failed)?;
        inspection.scan(combined.as_bytes())?;

        inspection.telemetry.content_chars = Some(data.content.chars().count());
        inspection.telemetry.conversion_method = Some(data.conversion_method);
        Ok(data)
    }
}
