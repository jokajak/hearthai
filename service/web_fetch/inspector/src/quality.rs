//! Deciding whether a converted candidate is worth returning.
//!
//! Deterministic, and deliberately not a length threshold. An article extractor
//! that discards a short README, a changelog entry or a reference table has not
//! improved the result, so the only things that count as failure here are: no
//! output at all, output that is just the page's navigation, and output that
//! lost structure the source clearly had.

pub struct SourceShape {
    has_headings: bool,
    has_table: bool,
    has_code: bool,
}

impl SourceShape {
    pub fn of(html: &str) -> Self {
        let lowered = html.to_ascii_lowercase();
        Self {
            has_headings: ["<h1", "<h2", "<h3", "<h4", "<h5", "<h6"].iter().any(|tag| lowered.contains(tag)),
            has_table: lowered.contains("<table"),
            has_code: lowered.contains("<pre") || lowered.contains("<code"),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Quality {
    Usable,
    Empty,
    NavigationOnly,
    LostStructure,
}

impl Quality {
    pub fn is_usable(self) -> bool {
        self == Quality::Usable
    }
}

pub fn assess(markdown: &str, source: &SourceShape) -> Quality {
    let trimmed = markdown.trim();
    if trimmed.is_empty() {
        return Quality::Empty;
    }

    let lines: Vec<&str> = trimmed.lines().map(str::trim).filter(|line| !line.is_empty()).collect();
    let link_lines = lines.iter().filter(|line| is_link_line(line)).count();
    let words = trimmed.split_whitespace().count();
    // A wall of link-only lines with almost no prose is a menu, whatever its
    // length. Prose alongside the links is a page with a menu in it, which is
    // fine.
    if !lines.is_empty() && link_lines * 5 >= lines.len() * 4 && words < 60 {
        return Quality::NavigationOnly;
    }

    if source.has_headings && !trimmed.lines().any(|line| line.trim_start().starts_with('#')) {
        return Quality::LostStructure;
    }
    if source.has_table && !trimmed.contains('|') {
        return Quality::LostStructure;
    }
    if source.has_code && !trimmed.contains('`') {
        return Quality::LostStructure;
    }
    Quality::Usable
}

/// A list item or bare line that is nothing but one markdown link.
fn is_link_line(line: &str) -> bool {
    let body = line.trim_start_matches(['-', '*', '+', ' ']).trim();
    let Some(rest) = body.strip_prefix('[') else {
        return false;
    };
    let Some(close) = rest.find("](") else {
        return false;
    };
    rest[close..].ends_with(')') && !rest[..close].is_empty()
}
