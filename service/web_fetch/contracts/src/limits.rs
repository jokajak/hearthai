//! Starting ceilings from the design. They are limits, not benchmarks: an
//! operator may lower them, and nothing in the pipeline treats reaching one as
//! a reason to return a prefix of the response.

/// Redirect hops followed after the first request.
pub const MAX_REDIRECTS: u32 = 3;
/// Total response header bytes accepted across all hops of one request.
pub const MAX_HEADER_BYTES: usize = 32 * 1024;
/// Wire bytes accepted for one response body, before any content decoding.
pub const MAX_WIRE_BYTES: usize = 1024 * 1024;
/// Bytes accepted after content decoding.
pub const MAX_DECODED_BYTES: usize = 1024 * 1024;
/// Largest expansion a content encoding may produce before it is treated as a
/// decompression bomb rather than a large page.
pub const MAX_DECODE_EXPANSION: usize = 64;
/// Total bytes fed to the scanner across every required pass of one response.
///
/// The design proposed 4 MiB. That is below what the required passes actually
/// cost: a 1 MiB body is scanned raw, decoded, entity-decoded and deobfuscated
/// before conversion has produced anything, which is 4 MiB on its own. Dropping
/// a pass to fit is the one thing this pipeline may not do, so the budget is
/// twice the proposal and a page near the body limit still completes.
pub const MAX_SCAN_INPUT_BYTES: usize = 8 * 1024 * 1024;
/// Characters of converted content returned to the caller.
///
/// Held at the decoded-body limit so that the body limit is the one that
/// actually decides what fits: a page inside the decoded limit should not be
/// refused for a second, smaller ceiling it was never measured against.
pub const MAX_CONTENT_CHARS: usize = 1024 * 1024;
/// Wall-clock seconds for the whole network stage.
pub const FETCH_DEADLINE_SECONDS: u64 = 15;
/// Wall-clock seconds for decode, scan and the whole conversion fallback chain.
pub const INSPECTION_DEADLINE_SECONDS: u64 = 5;
/// Characters accepted in a requested URL.
pub const MAX_URL_CHARS: usize = 2048;
/// Characters accepted in a response media type.
pub const MAX_CONTENT_TYPE_CHARS: usize = 200;
/// Matches reported by the scanner before it is treated as a failure.
pub const MAX_SCAN_MATCHES: usize = 32;
