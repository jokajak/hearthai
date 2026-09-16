//! The bounded fetch stage.
//!
//! This is the only part of webfetch with network egress, and the only thing it
//! is allowed to produce is an immutable artifact for the offline stage. It does
//! not convert, scan, decide, or return anything to a caller.

pub mod fetch;
pub mod policy;
pub mod resolve;
#[cfg(test)]
mod testserver;
