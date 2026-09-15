//! The documented set of media types and charsets v1 accepts.
//!
//! Both stages use this: the fetcher to refuse a response body it has no reason
//! to store, and the inspector to refuse one it cannot decode unambiguously.
//! Anything outside the list is `unsupported_content`, never a best effort.

use crate::{ContractError, Result};

/// A parsed `Content-Type`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MediaType {
    /// Lowercased `type/subtype` with no parameters.
    pub essence: String,
    /// Lowercased charset parameter, when the response gave one.
    pub charset: Option<String>,
}

/// Text media types v1 handles. Everything else - images, PDFs, archives, fonts,
/// anything binary - is refused rather than processed.
pub const SUPPORTED_MEDIA_TYPES: &[&str] = &[
    "text/html",
    "application/xhtml+xml",
    "text/plain",
    "text/markdown",
    "text/csv",
    "text/xml",
    "application/xml",
    "application/json",
];

/// Charsets the inspector can decode without guessing.
pub const SUPPORTED_CHARSETS: &[&str] = &[
    "utf-8",
    "utf8",
    "us-ascii",
    "ascii",
    "iso-8859-1",
    "latin1",
    "windows-1252",
    "utf-16",
    "utf-16le",
    "utf-16be",
];

impl MediaType {
    /// Parse a `Content-Type` header value.
    ///
    /// A header that is absent, empty, or malformed is an error: guessing the
    /// type of a response is how a fetcher ends up decoding something it was
    /// never meant to handle.
    pub fn parse(value: &str) -> Result<Self> {
        let mut parts = value.split(';');
        let essence = parts.next().unwrap_or_default().trim().to_ascii_lowercase();
        if essence.is_empty()
            || essence.split('/').count() != 2
            || essence.split('/').any(str::is_empty)
        {
            return Err(ContractError::new("content type is not a media type"));
        }
        let mut charset = None;
        for parameter in parts {
            let (name, raw) = match parameter.split_once('=') {
                Some(pair) => pair,
                None => continue,
            };
            if name.trim().eq_ignore_ascii_case("charset") {
                let cleaned = raw.trim().trim_matches('"').trim().to_ascii_lowercase();
                if cleaned.is_empty() {
                    return Err(ContractError::new("content type has an empty charset"));
                }
                charset = Some(cleaned);
            }
        }
        Ok(Self { essence, charset })
    }

    pub fn is_supported(&self) -> bool {
        SUPPORTED_MEDIA_TYPES.contains(&self.essence.as_str())
            && self
                .charset
                .as_deref()
                .is_none_or(|charset| SUPPORTED_CHARSETS.contains(&charset))
    }

    pub fn is_html(&self) -> bool {
        matches!(self.essence.as_str(), "text/html" | "application/xhtml+xml")
    }

    /// The header form, normalized. This is what a result reports, so it is
    /// rebuilt from parsed parts rather than echoed from the response.
    pub fn header_value(&self) -> String {
        match &self.charset {
            Some(charset) => format!("{}; charset={charset}", self.essence),
            None => self.essence.clone(),
        }
    }
}

/// Content codings the inspector can undo under its own limits.
pub fn is_supported_content_encoding(value: &str) -> bool {
    matches!(
        value.trim().to_ascii_lowercase().as_str(),
        "" | "identity" | "gzip" | "deflate"
    )
}
