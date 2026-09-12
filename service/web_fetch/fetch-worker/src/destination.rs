//! Which addresses the fetcher is willing to connect to.
//!
//! Two layers, and both must agree. The first is a fixed classification of
//! addresses that are never a public web destination - loopback, private,
//! link-local (which is where the cloud metadata endpoint lives), and their
//! IPv6 spellings, including the mapped and tunnelled forms that look public
//! until you unwrap them. The second is the deployment's own cluster, service
//! and node ranges, which only home-ops knows.
//!
//! The second layer has no safe default, so there is no default. A policy that
//! is missing, empty, or malformed leaves the capability unavailable rather
//! than quietly enforcing half of it.

use std::net::{IpAddr, Ipv4Addr, Ipv6Addr};

use serde_json::Value;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PolicyError {
    Missing,
    Empty,
    Malformed,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Cidr {
    base: IpAddr,
    prefix: u8,
}

impl Cidr {
    pub fn parse(value: &str) -> Result<Self, PolicyError> {
        let (address, prefix) = value.split_once('/').ok_or(PolicyError::Malformed)?;
        let base: IpAddr = address.parse().map_err(|_| PolicyError::Malformed)?;
        let prefix: u8 = prefix.parse().map_err(|_| PolicyError::Malformed)?;
        let width = if base.is_ipv4() { 32 } else { 128 };
        if prefix > width {
            return Err(PolicyError::Malformed);
        }
        Ok(Self { base, prefix })
    }

    pub fn contains(&self, address: IpAddr) -> bool {
        match (self.base, address) {
            (IpAddr::V4(base), IpAddr::V4(candidate)) => {
                matches_prefix(&base.octets(), &candidate.octets(), self.prefix)
            }
            (IpAddr::V6(base), IpAddr::V6(candidate)) => {
                matches_prefix(&base.octets(), &candidate.octets(), self.prefix)
            }
            // A v4 range and a v6 address are only comparable once the v6 one
            // has been unwrapped, which `DestinationPolicy` does before asking.
            _ => false,
        }
    }
}

fn matches_prefix(base: &[u8], candidate: &[u8], prefix: u8) -> bool {
    let whole = usize::from(prefix / 8);
    let remainder = prefix % 8;
    if base[..whole] != candidate[..whole] {
        return false;
    }
    if remainder == 0 {
        return true;
    }
    let mask = 0xffu8 << (8 - remainder);
    base[whole] & mask == candidate[whole] & mask
}

#[derive(Debug, Clone)]
pub struct DestinationPolicy {
    cluster_denied: Vec<Cidr>,
}

impl DestinationPolicy {
    /// Parses the deployment-supplied policy document. Every failure mode -
    /// absent file, empty list, one unparseable entry - produces an error, and
    /// the caller is expected to stay unready rather than fetch anything.
    pub fn from_json(document: Option<&str>) -> Result<Self, PolicyError> {
        let document = document.ok_or(PolicyError::Missing)?;
        let value: Value = serde_json::from_str(document).map_err(|_| PolicyError::Malformed)?;
        let object = value.as_object().ok_or(PolicyError::Malformed)?;
        if object.keys().any(|key| key != "cluster_denied_cidrs") {
            return Err(PolicyError::Malformed);
        }
        let entries =
            object.get("cluster_denied_cidrs").and_then(Value::as_array).ok_or(PolicyError::Malformed)?;
        if entries.is_empty() {
            return Err(PolicyError::Empty);
        }
        let cluster_denied = entries
            .iter()
            .map(|entry| entry.as_str().ok_or(PolicyError::Malformed).and_then(Cidr::parse))
            .collect::<Result<Vec<_>, _>>()?;
        Ok(Self { cluster_denied })
    }

    /// Replaces the active policy only if the replacement is wholly valid, so a
    /// bad update leaves the previous one enforcing instead of leaving a gap.
    pub fn replace(&mut self, document: Option<&str>) -> Result<(), PolicyError> {
        let candidate = Self::from_json(document)?;
        *self = candidate;
        Ok(())
    }

    pub fn permits(&self, address: IpAddr) -> bool {
        let address = unwrap_embedded_v4(address);
        if !is_public(address) {
            return false;
        }
        !self.cluster_denied.iter().any(|range| range.contains(address))
    }

    /// A name is usable only if *every* answer is permitted. One private answer
    /// mixed into an otherwise public set is the shape of a rebinding attempt,
    /// and picking a good address out of the set would just mean the next
    /// lookup gets the other one.
    pub fn permits_all(&self, addresses: &[IpAddr]) -> bool {
        !addresses.is_empty() && addresses.iter().all(|address| self.permits(*address))
    }
}

/// `::ffff:10.0.0.1`, `::10.0.0.1` and `2002:0a00:0001::` all reach the same
/// host as `10.0.0.1`. Classify what the packet will actually reach.
fn unwrap_embedded_v4(address: IpAddr) -> IpAddr {
    let IpAddr::V6(v6) = address else {
        return address;
    };
    if let Some(mapped) = v6.to_ipv4_mapped() {
        return IpAddr::V4(mapped);
    }
    let segments = v6.segments();
    if segments[0] == 0x2002 {
        // 6to4 embeds the v4 address it tunnels to in the next 32 bits.
        return IpAddr::V4(Ipv4Addr::new(
            (segments[1] >> 8) as u8,
            (segments[1] & 0xff) as u8,
            (segments[2] >> 8) as u8,
            (segments[2] & 0xff) as u8,
        ));
    }
    if let Some(compatible) = v6.to_ipv4()
        && segments[..6].iter().all(|segment| *segment == 0)
    {
        return IpAddr::V4(compatible);
    }
    address
}

fn is_public(address: IpAddr) -> bool {
    match address {
        IpAddr::V4(v4) => is_public_v4(v4),
        IpAddr::V6(v6) => is_public_v6(v6),
    }
}

fn is_public_v4(address: Ipv4Addr) -> bool {
    let [a, b, c, _] = address.octets();
    let shared = a == 100 && (64..128).contains(&b);
    let benchmarking = a == 198 && (b == 18 || b == 19);
    let documentation = (a == 192 && b == 0 && c == 2)
        || (a == 198 && b == 51 && c == 100)
        || (a == 203 && b == 0 && c == 113);
    let protocol_assignment = a == 192 && b == 0 && c == 0;
    let relay_anycast = a == 192 && b == 88 && c == 99;
    !(address.is_private()
        || address.is_loopback()
        || address.is_link_local()
        || address.is_broadcast()
        || address.is_multicast()
        || address.is_unspecified()
        || a == 0
        || a >= 240
        || shared
        || benchmarking
        || documentation
        || protocol_assignment
        || relay_anycast)
}

fn is_public_v6(address: Ipv6Addr) -> bool {
    let segments = address.segments();
    let unique_local = segments[0] & 0xfe00 == 0xfc00;
    let link_local = segments[0] & 0xffc0 == 0xfe80;
    let documentation = segments[0] == 0x2001 && segments[1] == 0x0db8;
    let discard = segments[0] == 0x0100 && segments[1..4].iter().all(|segment| *segment == 0);
    // 2001::/23 is IETF protocol assignments: Teredo, ORCHID and friends, none
    // of which is a web origin.
    let protocol_assignment = segments[0] == 0x2001 && segments[1] < 0x0200;
    !(address.is_loopback()
        || address.is_unspecified()
        || address.is_multicast()
        || unique_local
        || link_local
        || documentation
        || discard
        || protocol_assignment)
}
