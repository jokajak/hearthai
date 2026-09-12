//! Local HTML conversion, native first.
//!
//! Three converters, in a fixed operator-owned order. The model picks a format;
//! it does not pick a converter, and no converter reaches the network: each one
//! is handed the same already-downloaded HTML. Nothing here is an injection
//! detector - removing a `<script>` is cleanup, and the detection gate runs
//! over every candidate this module produces.

use hearthai_web_fetch_contracts::ConversionMethod;
use htmd::HtmlToMarkdown;

/// Boilerplate that is never document content. Dropped by the cleanup
/// converters; scripts and styles are dropped by all of them, because their
/// text is not something a reader asked for in any mode.
const BOILERPLATE_TAGS: [&str; 14] = [
    "script", "style", "noscript", "nav", "header", "footer", "aside", "form", "iframe", "svg", "canvas",
    "template", "button", "select",
];
const ALWAYS_SKIPPED_TAGS: [&str; 3] = ["script", "style", "noscript"];

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Candidate {
    pub method: ConversionMethod,
    pub markdown: String,
}

/// The fixed chain. Ordinary conversion failure or poor extraction moves to the
/// next entry; a detection hit or an inspection failure does not, and that
/// decision belongs to the pipeline, not here.
pub fn chain() -> [ConversionMethod; 3] {
    [ConversionMethod::Native, ConversionMethod::Extracted, ConversionMethod::Basic]
}

pub fn convert(method: ConversionMethod, html: &str) -> Option<Candidate> {
    let markdown = match method {
        ConversionMethod::Native => to_markdown(html, &BOILERPLATE_TAGS)?,
        ConversionMethod::Extracted => to_markdown(&extract_main_content(html)?, &ALWAYS_SKIPPED_TAGS)?,
        ConversionMethod::Basic => to_markdown(html, &ALWAYS_SKIPPED_TAGS)?,
        // Neither of these is part of the HTML chain: `Source` returns decoded
        // HTML untouched and `Passthrough` returns non-HTML text untouched.
        ConversionMethod::Source | ConversionMethod::Passthrough => return None,
    };
    Some(Candidate { method, markdown })
}

fn to_markdown(html: &str, skip: &[&str]) -> Option<String> {
    HtmlToMarkdown::builder()
        .skip_tags(skip.to_vec())
        .scripting_enabled(false)
        .build()
        .convert(html)
        .ok()
        .map(|markdown| markdown.trim().to_owned())
}

/// Main-content extraction over the same downloaded HTML.
///
/// Bounded by element count so a pathological document cannot spend the run's
/// budget here, and it never requests an alternate version of the page.
fn extract_main_content(html: &str) -> Option<String> {
    let config = dom_smoothie::Config {
        max_elements_to_parse: 20_000,
        // The default threshold discards short documents; the design says a
        // legitimately short page is not a failure, so the quality checks make
        // that call instead of the extractor.
        char_threshold: 1,
        ..Default::default()
    };
    let mut readability = dom_smoothie::Readability::new(html, None, Some(config)).ok()?;
    let article = readability.parse().ok()?;
    let content = article.content.to_string();
    (!content.trim().is_empty()).then_some(content)
}

/// Plain text from the same cleaned, converted document.
///
/// `text` is not a different pipeline: it is the markdown candidate with its
/// markers removed, so both formats get identical cleanup, identical fallback
/// and identical scanning.
pub fn markdown_to_text(markdown: &str) -> String {
    let mut lines = Vec::new();
    let mut in_code_fence = false;
    for line in markdown.lines() {
        let trimmed = line.trim_end();
        if trimmed.trim_start().starts_with("```") {
            in_code_fence = !in_code_fence;
            continue;
        }
        if in_code_fence {
            lines.push(trimmed.to_owned());
            continue;
        }
        lines.push(strip_inline_markers(trimmed));
    }
    lines.join("\n").trim().to_owned()
}

fn strip_inline_markers(line: &str) -> String {
    let without_prefix = line.trim_start_matches(['>', ' ']).trim_start_matches('#').trim_start();
    let mut output = String::with_capacity(without_prefix.len());
    let mut characters = without_prefix.chars().peekable();
    while let Some(character) = characters.next() {
        match character {
            // `[label](href)` keeps the label: the href is a URL the page chose,
            // and plain text is not a place to follow or advertise one.
            '[' => {
                let label: String = characters.by_ref().take_while(|next| *next != ']').collect();
                output.push_str(&label);
                if characters.peek() == Some(&'(') {
                    characters.next();
                    for next in characters.by_ref() {
                        if next == ')' {
                            break;
                        }
                    }
                }
            }
            '*' | '_' | '`' => {}
            other => output.push(other),
        }
    }
    output.trim_end().to_owned()
}
