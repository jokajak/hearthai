//! Turning wire bytes into text, or refusing to.
//!
//! Both steps here are places where "be liberal in what you accept" is the
//! wrong instinct. A body that decompresses to something enormous, or a byte
//! sequence that is not valid in the charset it claims, is not a page with a
//! small problem: it is input whose meaning the scanner and the converter would
//! have to guess at, and two components guessing differently is how a rule
//! matches the text nobody returns and misses the text somebody does.

use std::io::Read;

use encoding_rs::{Encoding, UTF_8, WINDOWS_1252};
use flate2::read::{DeflateDecoder, GzDecoder, ZlibDecoder};

use crate::InspectionError;

/// Charsets v1 decodes. The list is short because each entry is a promise that
/// the bytes mean one thing.
pub const SUPPORTED_CHARSETS: [&str; 10] = [
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

/// Undoes `content-encoding` under a ceiling.
///
/// The ceiling is checked while inflating, not after, so a bomb is stopped by
/// the read rather than by a length check on something already in memory.
pub fn decode_content_encoding(
    encoding: Option<&str>,
    wire: &[u8],
    max_decoded_bytes: usize,
) -> Result<Vec<u8>, InspectionError> {
    let label = encoding.map(|value| value.trim().to_ascii_lowercase());
    match label.as_deref() {
        None | Some("") | Some("identity") => {
            if wire.len() > max_decoded_bytes {
                return Err(InspectionError::ResponseLimit);
            }
            Ok(wire.to_vec())
        }
        Some("gzip") | Some("x-gzip") => bounded(GzDecoder::new(wire), max_decoded_bytes),
        // Both spellings are called "deflate" in the wild: zlib-wrapped, which
        // is what the RFC says, and raw, which is what some servers send.
        Some("deflate") => bounded(ZlibDecoder::new(wire), max_decoded_bytes).or_else(|error| match error {
            InspectionError::ResponseLimit => Err(error),
            _ => bounded(DeflateDecoder::new(wire), max_decoded_bytes),
        }),
        Some(_) => Err(InspectionError::UnsupportedContent),
    }
}

fn bounded(reader: impl Read, max_decoded_bytes: usize) -> Result<Vec<u8>, InspectionError> {
    let mut buffer = Vec::new();
    reader
        .take(max_decoded_bytes as u64 + 1)
        .read_to_end(&mut buffer)
        .map_err(|_| InspectionError::UnsupportedContent)?;
    if buffer.len() > max_decoded_bytes {
        return Err(InspectionError::ResponseLimit);
    }
    Ok(buffer)
}

/// Decodes the body to text, refusing anything that does not decode cleanly.
///
/// An undeclared charset means UTF-8 and nothing else. Falling back to a
/// single-byte encoding when UTF-8 fails would make every malformed body
/// decodable, which is the same as not checking.
pub fn decode_charset(bytes: &[u8], declared: Option<&str>) -> Result<String, InspectionError> {
    if let Some(label) = declared
        && !SUPPORTED_CHARSETS.contains(&label.trim().to_ascii_lowercase().as_str())
    {
        return Err(InspectionError::UnsupportedContent);
    }
    let encoding = match declared {
        Some(label) => {
            Encoding::for_label(label.trim().as_bytes()).ok_or(InspectionError::UnsupportedContent)?
        }
        None => UTF_8,
    };
    if !matches!(encoding.name(), "UTF-8" | "windows-1252" | "UTF-16LE" | "UTF-16BE") {
        return Err(InspectionError::UnsupportedContent);
    }
    // A BOM that disagrees with the declared charset is exactly the ambiguity
    // this refuses to resolve silently.
    if let Some((sniffed, _)) = Encoding::for_bom(bytes)
        && sniffed != encoding
        && declared.is_some()
    {
        return Err(InspectionError::UnsupportedContent);
    }
    let (text, had_errors) = match Encoding::for_bom(bytes) {
        Some((sniffed, offset)) => {
            let (decoded, _, had_errors) = sniffed.decode(&bytes[offset..]);
            (decoded, had_errors)
        }
        None => {
            let (decoded, had_errors) = encoding.decode_without_bom_handling(bytes);
            (decoded, had_errors)
        }
    };
    // windows-1252 maps every byte, so `had_errors` can only fire for the
    // multi-byte encodings; that is the case worth refusing.
    if had_errors && encoding != WINDOWS_1252 {
        return Err(InspectionError::UnsupportedContent);
    }
    Ok(text.into_owned())
}
