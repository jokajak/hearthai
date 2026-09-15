//! Inspection-only views of the response.
//!
//! These exist so a pattern that is plainly present to a reader is also present
//! to the scanner: `&#105;gnore`, or text padded with zero-width joiners, or
//! fullwidth characters. They are scanned and then discarded - the returned
//! content is never one of these forms, and this is not recursive decoding.

/// Decode HTML character references, both named (a small common set) and
/// numeric. Unknown references are left exactly as they are.
pub fn decode_entities(text: &str) -> String {
    let mut output = String::with_capacity(text.len());
    let mut rest = text;
    while let Some(start) = rest.find('&') {
        output.push_str(&rest[..start]);
        let tail = &rest[start..];
        match tail.find(';').filter(|end| *end <= 12) {
            Some(end) => {
                let reference = &tail[1..end];
                match resolve_entity(reference) {
                    Some(character) => output.push(character),
                    None => output.push_str(&tail[..=end]),
                }
                rest = &tail[end + 1..];
            }
            None => {
                output.push('&');
                rest = &tail[1..];
            }
        }
    }
    output.push_str(rest);
    output
}

fn resolve_entity(reference: &str) -> Option<char> {
    if let Some(digits) = reference
        .strip_prefix("#x")
        .or_else(|| reference.strip_prefix("#X"))
    {
        return u32::from_str_radix(digits, 16)
            .ok()
            .and_then(char::from_u32);
    }
    if let Some(digits) = reference.strip_prefix('#') {
        return digits.parse::<u32>().ok().and_then(char::from_u32);
    }
    match reference {
        "amp" => Some('&'),
        "lt" => Some('<'),
        "gt" => Some('>'),
        "quot" => Some('"'),
        "apos" => Some('\''),
        "nbsp" => Some(' '),
        "hellip" => Some('.'),
        _ => None,
    }
}

/// Fold away the characters that hide a phrase from a literal pattern without
/// hiding it from a reader.
pub fn deobfuscate(text: &str) -> String {
    text.chars()
        .filter_map(|character| match character {
            // Zero-width and formatting characters.
            '\u{00ad}' | '\u{200b}'..='\u{200f}' | '\u{2060}'..='\u{2064}' | '\u{feff}' => None,
            // Fullwidth forms of printable ASCII.
            '\u{ff01}'..='\u{ff5e}' => char::from_u32(character as u32 - 0xfee0),
            // Unicode spaces and separators.
            '\u{00a0}'
            | '\u{1680}'
            | '\u{2000}'..='\u{200a}'
            | '\u{202f}'
            | '\u{205f}'
            | '\u{3000}' => Some(' '),
            // Quotation marks and dashes that regularly stand in for ASCII.
            '\u{2018}' | '\u{2019}' | '\u{201b}' | '\u{2032}' => Some('\''),
            '\u{201c}' | '\u{201d}' | '\u{201f}' | '\u{2033}' => Some('"'),
            '\u{2010}'..='\u{2015}' | '\u{2212}' => Some('-'),
            other => Some(other),
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn numeric_and_named_references_decode() {
        assert_eq!(
            decode_entities("&#105;gnore &#x70;revious"),
            "ignore previous"
        );
        assert_eq!(decode_entities("a &amp; b &lt;tag&gt;"), "a & b <tag>");
    }

    #[test]
    fn unknown_or_unterminated_references_are_left_alone() {
        assert_eq!(
            decode_entities("&unknownthing; &amp"),
            "&unknownthing; &amp"
        );
        assert_eq!(decode_entities("100% & rising"), "100% & rising");
    }

    #[test]
    fn invisible_padding_and_fullwidth_forms_fold_to_the_plain_phrase() {
        assert_eq!(deobfuscate("ig\u{200b}no\u{feff}re"), "ignore");
        assert_eq!(
            deobfuscate("\u{ff29}\u{ff47}\u{ff4e}\u{ff4f}\u{ff52}\u{ff45}"),
            "Ignore"
        );
        assert_eq!(deobfuscate("don\u{2019}t\u{00a0}stop"), "don't stop");
    }

    #[test]
    fn ordinary_text_survives_unchanged() {
        let text = "The quick brown fox — with a dash.";
        assert_eq!(deobfuscate(text), "The quick brown fox - with a dash.");
        assert_eq!(decode_entities(text), text);
    }
}
