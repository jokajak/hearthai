//! Inspection-only views of the text.
//!
//! These exist so a detector sees `ignore previous instructions` when the page
//! wrote `&#105;gnore previous instructions` or slipped zero-width joiners
//! between the letters. They are scanned and then discarded: the returned
//! content is always the text the converter produced, never a normalized form.
//! Recursive decoding is explicitly out of scope for v1 - one pass, bounded.

/// Caps by bytes without splitting a character.
///
/// The cap is a resource bound, and a resource bound that panics on a
/// multi-byte character at the wrong offset is a way for a page to take the
/// inspector down.
fn bounded(text: &str, cap: usize) -> &str {
    if text.len() <= cap {
        return text;
    }
    let mut end = cap;
    while end > 0 && !text.is_char_boundary(end) {
        end -= 1;
    }
    &text[..end]
}

/// Undoes numeric and the common named HTML entities, once.
pub fn entity_decoded(text: &str, cap: usize) -> String {
    let source = bounded(text, cap);
    let mut output = String::with_capacity(source.len());
    let mut rest = source;
    while let Some(start) = rest.find('&') {
        output.push_str(&rest[..start]);
        let tail = &rest[start..];
        // An entity is short; anything longer is an ampersand in prose.
        let Some(end) = bounded(tail, 12).find(';') else {
            output.push('&');
            rest = &tail[1..];
            continue;
        };
        let entity = &tail[1..end];
        match decode_entity(entity) {
            Some(character) => output.push(character),
            None => output.push_str(&tail[..=end]),
        }
        rest = &tail[end + 1..];
    }
    output.push_str(rest);
    output
}

fn decode_entity(entity: &str) -> Option<char> {
    if let Some(digits) = entity.strip_prefix("#x").or_else(|| entity.strip_prefix("#X")) {
        return char::from_u32(u32::from_str_radix(digits, 16).ok()?);
    }
    if let Some(digits) = entity.strip_prefix('#') {
        return char::from_u32(digits.parse().ok()?);
    }
    match entity {
        "amp" => Some('&'),
        "lt" => Some('<'),
        "gt" => Some('>'),
        "quot" => Some('"'),
        "apos" => Some('\''),
        "nbsp" => Some(' '),
        "sol" => Some('/'),
        "colon" => Some(':'),
        "commat" => Some('@'),
        "period" => Some('.'),
        _ => None,
    }
}

/// Removes characters that are invisible to a reader and folds the fullwidth
/// forms onto ASCII, so obfuscation that survives entity decoding still lands
/// on the same bytes a rule is written against.
pub fn unicode_folded(text: &str, cap: usize) -> String {
    bounded(text, cap).chars().filter(|character| !is_invisible(*character)).map(fold_fullwidth).collect()
}

fn is_invisible(character: char) -> bool {
    matches!(character,
        '\u{00ad}'                 // soft hyphen
        | '\u{200b}'..='\u{200f}'  // zero width space through RTL mark
        | '\u{202a}'..='\u{202e}'  // bidi overrides
        | '\u{2060}'..='\u{2064}'  // word joiner and invisible operators
        | '\u{feff}'               // zero width no-break space
        | '\u{180e}'
    )
}

fn fold_fullwidth(character: char) -> char {
    match character {
        '\u{ff01}'..='\u{ff5e}' => char::from_u32(character as u32 - 0xfee0).unwrap_or(character),
        '\u{3000}' => ' ',
        other => other,
    }
}
