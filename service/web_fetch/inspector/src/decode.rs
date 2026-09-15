//! Content decoding, under limits, without guessing.
//!
//! Two things make decoding a security step rather than a formality: a
//! compressed body can be far larger than the bytes that arrived, and a body
//! whose charset is wrong or malformed decodes into text that is not what was
//! scanned or what a reader would see. Both are refusals here.

use std::io::Read;

use encoding_rs::Encoding;
use flate2::read::{GzDecoder, ZlibDecoder};

use hearthai_webfetch_contracts::envelope::ErrorCode;
use hearthai_webfetch_contracts::limits::{MAX_DECODE_EXPANSION, MAX_DECODED_BYTES};
use hearthai_webfetch_contracts::media::MediaType;

/// Undo the content coding, bounded by absolute size and by expansion ratio.
///
/// The ratio is what catches a decompression bomb: a small body that expands
/// enormously is refused as a limit failure rather than filling memory first.
pub fn decode_content_encoding(
    body: &[u8],
    content_encoding: Option<&str>,
) -> Result<Vec<u8>, ErrorCode> {
    let coding = content_encoding
        .unwrap_or("identity")
        .trim()
        .to_ascii_lowercase();
    match coding.as_str() {
        "" | "identity" => {
            if body.len() > MAX_DECODED_BYTES {
                return Err(ErrorCode::ResponseLimitExceeded);
            }
            Ok(body.to_vec())
        }
        "gzip" => bounded_read(GzDecoder::new(body), body.len()),
        "deflate" => bounded_read(ZlibDecoder::new(body), body.len()),
        _ => Err(ErrorCode::UnsupportedContent),
    }
}

fn bounded_read(reader: impl Read, wire_bytes: usize) -> Result<Vec<u8>, ErrorCode> {
    // The ceiling is whichever bound bites first: the absolute decoded limit, or
    // this body's own expansion allowance.
    let ceiling = MAX_DECODED_BYTES.min(wire_bytes.saturating_mul(MAX_DECODE_EXPANSION).max(4096));
    let mut decoded = Vec::new();
    let read = reader
        .take(ceiling as u64 + 1)
        .read_to_end(&mut decoded)
        .map_err(|_| ErrorCode::UnsupportedContent)?;
    if read > ceiling {
        return Err(ErrorCode::ResponseLimitExceeded);
    }
    Ok(decoded)
}

/// Decode bytes to text using the response's charset.
///
/// No replacement characters and no sniffing beyond a byte-order mark: an
/// undeclared charset is treated as UTF-8 and must actually be UTF-8.
pub fn decode_charset(bytes: &[u8], media: &MediaType) -> Result<String, ErrorCode> {
    if let Some((encoding, offset)) = Encoding::for_bom(bytes) {
        let (text, malformed) = encoding.decode_without_bom_handling(&bytes[offset..]);
        if malformed {
            return Err(ErrorCode::UnsupportedContent);
        }
        return Ok(text.into_owned());
    }
    let label = media.charset.as_deref().unwrap_or("utf-8");
    let encoding = Encoding::for_label(label.as_bytes()).ok_or(ErrorCode::UnsupportedContent)?;
    encoding
        .decode_without_bom_handling_and_without_replacement(bytes)
        .map(std::borrow::Cow::into_owned)
        .ok_or(ErrorCode::UnsupportedContent)
}

#[cfg(test)]
mod tests {
    use std::io::Write;

    use flate2::Compression;
    use flate2::write::GzEncoder;

    use super::*;

    fn gzip(payload: &[u8]) -> Vec<u8> {
        let mut encoder = GzEncoder::new(Vec::new(), Compression::best());
        encoder.write_all(payload).expect("compress");
        encoder.finish().expect("finish")
    }

    fn html() -> MediaType {
        MediaType::parse("text/html; charset=utf-8").expect("media type")
    }

    #[test]
    fn identity_and_gzip_bodies_decode_to_the_same_text() {
        let payload = b"<html><body>hello</body></html>";
        assert_eq!(
            decode_content_encoding(payload, None).expect("identity"),
            payload.to_vec()
        );
        assert_eq!(
            decode_content_encoding(&gzip(payload), Some("gzip")).expect("gzip"),
            payload.to_vec()
        );
    }

    #[test]
    fn a_decompression_bomb_is_a_limit_failure_not_a_large_page() {
        let bomb = gzip(&vec![b'a'; 8 * 1024 * 1024]);
        assert!(bomb.len() < 64 * 1024, "fixture is not actually a bomb");
        assert_eq!(
            decode_content_encoding(&bomb, Some("gzip")),
            Err(ErrorCode::ResponseLimitExceeded)
        );
    }

    #[test]
    fn a_broken_compressed_body_is_refused() {
        assert_eq!(
            decode_content_encoding(b"not actually gzip", Some("gzip")),
            Err(ErrorCode::UnsupportedContent)
        );
    }

    #[test]
    fn an_unknown_content_coding_is_refused() {
        assert_eq!(
            decode_content_encoding(b"payload", Some("br")),
            Err(ErrorCode::UnsupportedContent)
        );
    }

    #[test]
    fn declared_charsets_are_honoured_and_malformed_bytes_are_refused() {
        let latin1 = MediaType::parse("text/html; charset=iso-8859-1").expect("media type");
        assert_eq!(decode_charset(&[0xe9], &latin1).expect("latin-1"), "é");

        // The same byte is not valid UTF-8, and nothing here replaces it with
        // U+FFFD and carries on.
        assert_eq!(
            decode_charset(&[0xe9], &html()),
            Err(ErrorCode::UnsupportedContent)
        );
    }

    #[test]
    fn a_byte_order_mark_wins_over_an_absent_declaration() {
        let utf16 = MediaType::parse("text/plain").expect("media type");
        let bytes = [0xff, 0xfe, b'h', 0x00, b'i', 0x00];
        assert_eq!(decode_charset(&bytes, &utf16).expect("utf-16"), "hi");
    }
}
