//! Small strict-object helpers, mirroring the control plane's `require_*` functions
//! so both implementations reject the same documents for the same reasons.

use serde_json::Value;

use crate::error::{ContractError, Result, invalid};

pub(crate) fn require_object<'a>(value: &'a Value, path: &str) -> Result<&'a serde_json::Map<String, Value>> {
    value.as_object().ok_or_else(|| ContractError::new(path, "must be an object"))
}

/// Rejects both missing and unknown keys. Optional keys are named separately, so
/// a field the schema does not know about is never silently tolerated.
pub(crate) fn require_exact_fields(
    value: &serde_json::Map<String, Value>,
    required: &[&str],
    optional: &[&str],
    path: &str,
) -> Result<()> {
    for key in required {
        if !value.contains_key(*key) {
            return invalid(format!("{path}.{key}"), "is required");
        }
    }
    for key in value.keys() {
        if !required.contains(&key.as_str()) && !optional.contains(&key.as_str()) {
            return invalid(path, "has an unknown field");
        }
    }
    Ok(())
}

pub(crate) fn require_string<'a>(value: &'a Value, path: &str, maximum: usize) -> Result<&'a str> {
    let text = value.as_str().ok_or_else(|| ContractError::new(path, "must be a string"))?;
    if text.is_empty() {
        return invalid(path, "must not be empty");
    }
    if text.len() > maximum {
        return invalid(path, "is longer than the contract allows");
    }
    Ok(text)
}

pub(crate) fn require_u32(value: &Value, path: &str) -> Result<u32> {
    match value {
        Value::Number(number) => number
            .as_u64()
            .filter(|candidate| *candidate <= u64::from(u32::MAX))
            .map(|candidate| candidate as u32)
            .ok_or_else(|| ContractError::new(path, "must be a non-negative integer")),
        _ => invalid(path, "must be a non-negative integer"),
    }
}

/// Matches `^[a-z][a-z0-9<extra>]{1,63}$` without pulling in a regex engine.
pub(crate) fn is_lower_identifier(value: &str, extra: char) -> bool {
    let mut characters = value.chars();
    match characters.next() {
        Some(first) if first.is_ascii_lowercase() => {}
        _ => return false,
    }
    let rest: Vec<char> = characters.collect();
    (1..=63).contains(&rest.len())
        && rest.iter().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || *c == extra)
}
