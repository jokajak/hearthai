//! Starting ceilings from the design. They are budget numbers, not benchmarks:
//! the profile owns them and the model never sees or sets them.

use std::time::Duration;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct FetchLimits {
    pub max_redirects: u8,
    pub max_header_bytes: usize,
    /// Bytes as they arrive on the wire, before any content-encoding is undone.
    /// The decoded ceiling is the inspector's, because that is where decoding
    /// happens and where a compression bomb would land.
    pub max_wire_body_bytes: usize,
    pub network_time: Duration,
}

impl Default for FetchLimits {
    fn default() -> Self {
        Self {
            max_redirects: 3,
            max_header_bytes: 32 * 1024,
            max_wire_body_bytes: 1024 * 1024,
            network_time: Duration::from_secs(15),
        }
    }
}
