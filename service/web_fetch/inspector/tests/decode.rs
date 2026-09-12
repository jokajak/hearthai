//! Decoding: what is accepted, what is refused, and what is refused loudly.

use hearthai_web_fetch_inspector::InspectionError;
use hearthai_web_fetch_inspector::decode::{decode_charset, decode_content_encoding};

fn gzip(bytes: &[u8]) -> Vec<u8> {
    use std::io::Write;
    let mut encoder = flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::best());
    encoder.write_all(bytes).unwrap();
    encoder.finish().unwrap()
}

fn zlib(bytes: &[u8]) -> Vec<u8> {
    use std::io::Write;
    let mut encoder = flate2::write::ZlibEncoder::new(Vec::new(), flate2::Compression::best());
    encoder.write_all(bytes).unwrap();
    encoder.finish().unwrap()
}

#[test]
fn supported_content_encodings_round_trip() {
    let body = b"<h1>Example</h1>".repeat(64);
    assert_eq!(decode_content_encoding(None, &body, 1 << 20).unwrap(), body);
    assert_eq!(decode_content_encoding(Some("identity"), &body, 1 << 20).unwrap(), body);
    assert_eq!(decode_content_encoding(Some("gzip"), &gzip(&body), 1 << 20).unwrap(), body);
    assert_eq!(decode_content_encoding(Some("GZIP"), &gzip(&body), 1 << 20).unwrap(), body);
    assert_eq!(decode_content_encoding(Some("deflate"), &zlib(&body), 1 << 20).unwrap(), body);
}

#[test]
fn an_unsupported_content_encoding_is_refused_rather_than_passed_through() {
    let body = b"body";
    assert_eq!(
        decode_content_encoding(Some("br"), body, 1 << 20).unwrap_err(),
        InspectionError::UnsupportedContent
    );
    assert_eq!(
        decode_content_encoding(Some("compress"), body, 1 << 20).unwrap_err(),
        InspectionError::UnsupportedContent
    );
}

#[test]
fn a_decompression_bomb_stops_at_the_ceiling() {
    // 16 MiB of zeroes compresses to a few KiB. The ceiling has to be enforced
    // while inflating, or this is 16 MiB in memory before anyone checks.
    let bomb = gzip(&vec![0u8; 16 * 1024 * 1024]);
    assert!(bomb.len() < 64 * 1024, "the fixture is not actually a bomb");
    assert_eq!(
        decode_content_encoding(Some("gzip"), &bomb, 1024 * 1024).unwrap_err(),
        InspectionError::ResponseLimit
    );
}

#[test]
fn a_truncated_compressed_body_is_not_decoded_to_a_prefix() {
    let complete = gzip(b"a complete and coherent response body");
    let truncated = &complete[..complete.len() - 6];
    assert_eq!(
        decode_content_encoding(Some("gzip"), truncated, 1 << 20).unwrap_err(),
        InspectionError::UnsupportedContent
    );
}

#[test]
fn an_identity_body_over_the_ceiling_is_refused_too() {
    assert_eq!(
        decode_content_encoding(None, &vec![b'a'; 2048], 1024).unwrap_err(),
        InspectionError::ResponseLimit
    );
}

#[test]
fn supported_charsets_decode() {
    assert_eq!(decode_charset("héllo".as_bytes(), Some("utf-8")).unwrap(), "héllo");
    assert_eq!(decode_charset("héllo".as_bytes(), None).unwrap(), "héllo");
    assert_eq!(decode_charset(&[0xe9, 0x74, 0xe9], Some("iso-8859-1")).unwrap(), "été");
    assert_eq!(decode_charset(&[0xe9], Some("windows-1252")).unwrap(), "é");
    assert_eq!(decode_charset(b"plain", Some("us-ascii")).unwrap(), "plain");
}

#[test]
fn a_byte_order_mark_is_honoured_and_not_returned_as_content() {
    let utf8_bom = [&[0xef, 0xbb, 0xbf][..], "text".as_bytes()].concat();
    assert_eq!(decode_charset(&utf8_bom, Some("utf-8")).unwrap(), "text");
    let utf16_le = [&[0xff, 0xfe][..], &[b'h', 0, b'i', 0][..]].concat();
    assert_eq!(decode_charset(&utf16_le, Some("utf-16")).unwrap(), "hi");
}

#[test]
fn a_bom_that_contradicts_the_declared_charset_is_ambiguous_and_refused() {
    let utf16_le = [&[0xff, 0xfe][..], &[b'h', 0, b'i', 0][..]].concat();
    assert_eq!(decode_charset(&utf16_le, Some("utf-8")).unwrap_err(), InspectionError::UnsupportedContent);
}

#[test]
fn malformed_bytes_in_a_multibyte_charset_are_refused() {
    // A lone continuation byte is not UTF-8. Decoding it to U+FFFD would mean
    // the scanner and the reader see different text.
    assert_eq!(
        decode_charset(&[0x68, 0x80, 0x69], Some("utf-8")).unwrap_err(),
        InspectionError::UnsupportedContent
    );
    assert_eq!(decode_charset(&[0x68, 0x80, 0x69], None).unwrap_err(), InspectionError::UnsupportedContent);
}

#[test]
fn an_unsupported_charset_is_refused_rather_than_guessed() {
    for label in ["shift_jis", "euc-jp", "gb18030", "koi8-r", "made-up", ""] {
        assert_eq!(
            decode_charset(b"bytes", Some(label)).unwrap_err(),
            InspectionError::UnsupportedContent,
            "{label}"
        );
    }
}
