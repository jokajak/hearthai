//! The offline stage of `web_fetch:v1`.
//!
//! This crate decodes, inspects and converts a response that the fetch stage
//! already downloaded. It runs in a pod with no Internet access and no tools:
//! everything it needs is the artifact on disk and the rule bundle on a
//! read-only mount. Nothing in here opens a socket, requests an alternate
//! version of a page, or calls a model - conversion is parser code, and
//! detection is a pattern engine.

pub mod conversion;
pub mod decode;
pub mod detect;
pub mod normalize;
pub mod pipeline;
pub mod quality;

pub use detect::{ActiveBundle, BundleError, Detector, DetectorLimits, RuleBundle, Verdict, YaraDetector};
pub use pipeline::{Inspector, Outcome};

/// Why the inspector released nothing.
///
/// `Matched` is kept distinct from `Failed` internally because they mean
/// different things operationally - one is the gate working, the other is the
/// gate not finishing - but both withhold the entire response.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InspectionError {
    Matched,
    Failed,
    UnsupportedContent,
    ResponseLimit,
    Storage,
}

/// Ceilings that belong to the offline stage.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct InspectionLimits {
    /// The body after content-encoding is undone.
    pub max_decoded_bytes: usize,
    /// How much of the text each inspection-only normalized form covers.
    pub max_normalized_bytes: usize,
    /// The returned content, matching the contract's own ceiling.
    pub max_content_bytes: usize,
}

impl Default for InspectionLimits {
    fn default() -> Self {
        Self {
            max_decoded_bytes: 1024 * 1024,
            max_normalized_bytes: 1024 * 1024,
            max_content_bytes: hearthai_web_fetch_contracts::MAX_CONTENT_BYTES,
        }
    }
}
