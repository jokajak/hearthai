//! Local conversion, native cleanup first, with a bounded local fallback chain.
//!
//! Every converter is handed the same already-downloaded HTML. Nothing in here
//! fetches an alternate page, calls a remote reader, shells out, or asks a model
//! what a page means: conversion is ordinary parser code, and its only outputs
//! are text and a fixed method identifier.

use dom_smoothie::{Config, Readability};
use html_to_markdown_rs::{
    ConversionOptions, OutputFormat, PreprocessingOptions, PreprocessingPreset, convert,
};

use hearthai_webfetch_contracts::web_fetch::{ConversionMethod, Format};

/// One converter's output, before it has been inspected or judged.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Candidate {
    pub content: String,
    pub method: ConversionMethod,
}

/// Why a candidate is not good enough to return, when it is not.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Quality {
    Usable,
    Poor(&'static str),
}

/// Structure the source document has, used to notice when cleanup threw the
/// useful part of a page away.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SourceSignals {
    pub has_headings: bool,
    pub has_tables: bool,
    pub has_code: bool,
}

impl SourceSignals {
    pub fn of(html: &str) -> Self {
        let lowered = html.to_ascii_lowercase();
        let has_headings = ["<h1", "<h2", "<h3", "<h4"]
            .iter()
            .any(|tag| lowered.contains(tag));
        Self {
            has_headings,
            has_tables: lowered.contains("<table"),
            has_code: lowered.contains("<pre") || lowered.contains("<code"),
        }
    }
}

/// One way of turning the downloaded HTML into returnable text.
///
/// Each converter is a strategy with a fixed identifier and no state; adding one
/// means writing an implementation and putting it in [`CHAIN`], not extending a
/// switch in two places.
pub trait Converter: Sync {
    /// What a result built from this converter reports.
    fn method(&self) -> ConversionMethod;

    /// Convert, or say it could not. `None` is an ordinary conversion failure,
    /// which is the only thing that may lead to trying the next converter.
    fn convert(&self, source: &str, format: Format) -> Option<Candidate>;
}

/// Native conversion with content cleanup: navigation, forms, script and style
/// boilerplate out; headings, lists, code, tables and links kept.
pub struct NativeCleanup;

impl Converter for NativeCleanup {
    fn method(&self) -> ConversionMethod {
        ConversionMethod::Native
    }

    fn convert(&self, source: &str, format: Format) -> Option<Candidate> {
        candidate(
            self,
            to_markdown(source, format, PreprocessingPreset::Standard, true)?,
        )
    }
}

/// Main-content extraction on the same HTML, then plain conversion of what it
/// selected.
pub struct MainContentExtraction;

impl Converter for MainContentExtraction {
    fn method(&self) -> ConversionMethod {
        ConversionMethod::Extracted
    }

    fn convert(&self, source: &str, format: Format) -> Option<Candidate> {
        let article = extract_main_content(source)?;
        candidate(
            self,
            to_markdown(&article, format, PreprocessingPreset::Minimal, false)?,
        )
    }
}

/// Conversion without aggressive boilerplate removal, for pages an article
/// extractor would damage - short pages, reference tables, indexes.
pub struct PlainConversion;

impl Converter for PlainConversion {
    fn method(&self) -> ConversionMethod {
        ConversionMethod::Basic
    }

    fn convert(&self, source: &str, format: Format) -> Option<Candidate> {
        candidate(
            self,
            to_markdown(source, format, PreprocessingPreset::Minimal, false)?,
        )
    }
}

fn candidate(converter: &dyn Converter, content: String) -> Option<Candidate> {
    Some(Candidate {
        content,
        method: converter.method(),
    })
}

/// The fixed fallback order. It is operator-owned: a caller picks the output
/// format and nothing else.
pub const CHAIN: [&dyn Converter; 3] = [&NativeCleanup, &MainContentExtraction, &PlainConversion];

fn to_markdown(
    html: &str,
    format: Format,
    preset: PreprocessingPreset,
    remove_navigation: bool,
) -> Option<String> {
    let options = ConversionOptions {
        output_format: match format {
            Format::Text => OutputFormat::Plain,
            // `html` never reaches conversion; it is returned as inert source.
            Format::Markdown | Format::Html => OutputFormat::Markdown,
        },
        preprocessing: PreprocessingOptions {
            enabled: true,
            preset,
            remove_navigation,
            remove_forms: remove_navigation,
        },
        // Metadata extraction is work this pipeline has no use for.
        extract_metadata: false,
        ..ConversionOptions::default()
    };
    convert(html, options).ok()?.content
}

fn extract_main_content(html: &str) -> Option<String> {
    let config = Config {
        max_elements_to_parse: 50_000,
        ..Config::default()
    };
    let mut readability = Readability::new(html, None, Some(config)).ok()?;
    let article = readability.parse().ok()?;
    let content = article.content.to_string();
    if content.trim().is_empty() {
        None
    } else {
        Some(content)
    }
}

/// Decide whether a candidate is worth returning.
///
/// Deliberately not a length threshold: a short page is a legitimate page. What
/// this looks for is output that is empty, output that is nothing but a link
/// menu, and output that lost structure the source demonstrably had.
pub fn quality(candidate: &Candidate, format: Format, signals: SourceSignals) -> Quality {
    let content = candidate.content.trim();
    if content.is_empty() {
        return Quality::Poor("empty output");
    }
    if !content.chars().any(char::is_alphanumeric) {
        return Quality::Poor("no readable text");
    }

    let lines: Vec<&str> = content
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .collect();
    let link_lines = lines.iter().filter(|line| is_link_item(line)).count();
    let prose: usize = lines
        .iter()
        .filter(|line| !is_link_item(line))
        .map(|line| line.len())
        .sum();
    if lines.len() >= 5 && link_lines * 10 >= lines.len() * 8 && prose < 200 {
        return Quality::Poor("navigation only");
    }

    // Structure markers only exist in Markdown output, so they are only checked
    // there. Plain text is judged on having text at all.
    if format == Format::Markdown {
        if signals.has_headings && !lines.iter().any(|line| line.starts_with('#')) {
            return Quality::Poor("headings lost");
        }
        if signals.has_tables && !lines.iter().any(|line| line.starts_with('|')) {
            return Quality::Poor("tables lost");
        }
    }
    Quality::Usable
}

fn is_link_item(line: &str) -> bool {
    let bullet = line.starts_with("- ") || line.starts_with("* ") || line.starts_with("+ ");
    (bullet || line.starts_with('[')) && line.contains("](")
}

#[cfg(test)]
mod tests {
    use super::*;

    const DOCS: &str = r#"<html><head><title>Config</title><style>.x{}</style></head>
    <body>
      <nav><ul><li><a href="/a">Alpha</a></li><li><a href="/b">Beta</a></li></ul></nav>
      <main>
        <h1>Configuration</h1>
        <p>Set the values below before starting the service.</p>
        <h2>Options</h2>
        <table><thead><tr><th>Name</th><th>Default</th></tr></thead>
        <tbody><tr><td>timeout</td><td>15s</td></tr><tr><td>retries</td><td>3</td></tr></tbody></table>
        <pre><code class="language-bash">service start --timeout 15s</code></pre>
        <ul><li>First item</li><li>Second item</li></ul>
        <p>See <a href="https://example.com/manual">the manual</a> for details.</p>
      </main>
      <footer><p>Copyright</p></footer>
    </body></html>"#;

    const SHORT: &str = "<html><body><h1>Status</h1><p>All systems normal.</p></body></html>";

    const NAV_ONLY: &str = r#"<html><body><nav><ul>
      <li><a href="/1">One</a></li><li><a href="/2">Two</a></li><li><a href="/3">Three</a></li>
      <li><a href="/4">Four</a></li><li><a href="/5">Five</a></li><li><a href="/6">Six</a></li>
    </ul></nav></body></html>"#;

    #[test]
    fn native_conversion_keeps_the_structure_of_a_documentation_page() {
        let candidate = NativeCleanup
            .convert(DOCS, Format::Markdown)
            .expect("converted");
        let content = &candidate.content;
        assert!(
            content.contains("# Configuration"),
            "headings lost:\n{content}"
        );
        assert!(
            content.contains("## Options"),
            "subheadings lost:\n{content}"
        );
        assert!(content.contains("| timeout"), "table lost:\n{content}");
        assert!(
            content.contains("service start --timeout 15s"),
            "code lost:\n{content}"
        );
        assert!(content.contains("First item"), "list lost:\n{content}");
        assert!(
            content.contains("](https://example.com/manual)"),
            "useful link lost:\n{content}"
        );
        // Cleanup removed the page furniture and the stylesheet.
        assert!(!content.contains(".x{}"), "style survived:\n{content}");
        assert_eq!(
            quality(&candidate, Format::Markdown, SourceSignals::of(DOCS)),
            Quality::Usable
        );
    }

    #[test]
    fn a_short_page_is_not_poor_quality_merely_for_being_short() {
        let candidate = NativeCleanup
            .convert(SHORT, Format::Markdown)
            .expect("converted");
        assert!(candidate.content.contains("All systems normal"));
        assert_eq!(
            quality(&candidate, Format::Markdown, SourceSignals::of(SHORT)),
            Quality::Usable
        );
    }

    #[test]
    fn a_navigation_only_result_is_poor_and_sends_the_chain_on() {
        let candidate = Candidate {
            content:
                "- [One](/1)\n- [Two](/2)\n- [Three](/3)\n- [Four](/4)\n- [Five](/5)\n- [Six](/6)"
                    .into(),
            method: ConversionMethod::Native,
        };
        assert_eq!(
            quality(&candidate, Format::Markdown, SourceSignals::of(NAV_ONLY)),
            Quality::Poor("navigation only")
        );
    }

    #[test]
    fn losing_a_table_the_source_had_is_poor_quality() {
        let candidate = Candidate {
            content: "# Configuration\n\nSome prose.".into(),
            method: ConversionMethod::Native,
        };
        assert_eq!(
            quality(&candidate, Format::Markdown, SourceSignals::of(DOCS)),
            Quality::Poor("tables lost")
        );
    }

    #[test]
    fn plain_text_output_carries_the_text_without_markup() {
        let candidate = NativeCleanup
            .convert(DOCS, Format::Text)
            .expect("converted");
        assert!(candidate.content.contains("Set the values below"));
        assert!(!candidate.content.contains("# Configuration"));
        assert_eq!(
            quality(&candidate, Format::Text, SourceSignals::of(DOCS)),
            Quality::Usable
        );
    }

    #[test]
    fn every_converter_in_the_chain_produces_something_for_an_article() {
        let article = r#"<html><body><header><nav><a href="/x">Home</a></nav></header>
        <article><h1>A Long Title</h1>
        <p>This paragraph is long enough that a main-content extractor recognises it as the body of
        the document rather than as page furniture, which is what the extractor needs to work.</p>
        <p>A second paragraph, also of a reasonable length, so that scoring has something to weigh
        against the navigation links in the header of this page.</p></article></body></html>"#;
        for converter in CHAIN {
            let method = converter.method();
            let candidate = converter
                .convert(article, Format::Markdown)
                .unwrap_or_else(|| panic!("{method:?} produced nothing"));
            assert!(
                candidate.content.contains("main-content extractor"),
                "{method:?} lost the body:\n{}",
                candidate.content
            );
            assert_eq!(candidate.method, method);
        }
    }

    #[test]
    fn the_chain_is_native_cleanup_then_extraction_then_plain_conversion() {
        let methods: Vec<ConversionMethod> =
            CHAIN.iter().map(|converter| converter.method()).collect();
        assert_eq!(
            methods,
            vec![
                ConversionMethod::Native,
                ConversionMethod::Extracted,
                ConversionMethod::Basic
            ]
        );
    }
}
