//! Conversion fidelity, the fallback chain, and the quality checks that pick
//! between converters.

use std::path::{Path, PathBuf};

use hearthai_web_fetch_contracts::ConversionMethod;
use hearthai_web_fetch_inspector::conversion::{self, markdown_to_text};
use hearthai_web_fetch_inspector::quality::{Quality, SourceShape, assess};

fn corpus(name: &str) -> String {
    let path: PathBuf = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/corpus").join(name);
    std::fs::read_to_string(path).unwrap()
}

fn native(name: &str) -> String {
    conversion::convert(ConversionMethod::Native, &corpus(name)).unwrap().markdown
}

#[test]
fn cleanup_keeps_the_document_and_drops_the_chrome() {
    let markdown = native("documentation.html");

    assert!(markdown.contains("# Configuring the store"), "{markdown}");
    assert!(markdown.contains("## Options"));
    assert!(markdown.contains("persistence.existingClaim"), "the table content was lost");
    assert!(markdown.contains('|'), "the table structure was lost");
    assert!(markdown.contains("helm upgrade hearthai"), "the code block content was lost");
    assert!(markdown.contains("```") || markdown.contains("    helm"), "the code block structure was lost");
    assert!(markdown.contains("Run it once."), "the list was lost");
    assert!(markdown.contains("restore runbook"), "a useful link was lost");

    for chrome in ["Troubleshoot", "window.analytics", "color:red", "Copyright"] {
        assert!(!markdown.contains(chrome), "cleanup kept {chrome}:\n{markdown}");
    }
}

#[test]
fn every_converter_is_handed_the_same_already_downloaded_html() {
    // Nothing in this module takes a URL, so there is nothing for a converter
    // to re-request. What the test can check is that all three produce output
    // from the same input string and none of them needs anything else.
    let html = corpus("article.html");
    for method in conversion::chain() {
        let candidate = conversion::convert(method, &html).unwrap();
        assert_eq!(candidate.method, method);
        assert!(candidate.markdown.contains("one writer"), "{method:?} lost the content");
    }
}

#[test]
fn a_short_page_is_not_discarded_for_being_short() {
    let html = corpus("short-page.html");
    let candidate = conversion::convert(ConversionMethod::Native, &html).unwrap();
    assert_eq!(candidate.markdown.trim(), "The service is up.");
    assert_eq!(assess(&candidate.markdown, &SourceShape::of(&html)), Quality::Usable);
}

#[test]
fn a_navigation_only_page_is_recognised_as_poor() {
    let html = corpus("navigation-only.html");
    let candidate = conversion::convert(ConversionMethod::Basic, &html).unwrap();
    assert_eq!(assess(&candidate.markdown, &SourceShape::of(&html)), Quality::NavigationOnly);
}

#[test]
fn output_that_lost_the_structure_of_its_source_is_poor() {
    let source = SourceShape::of("<h1>Title</h1><table><tr><td>x</td></tr></table><pre>code</pre>");
    assert_eq!(assess("just a sentence with no structure at all", &source), Quality::LostStructure);
    assert_eq!(assess("", &source), Quality::Empty);
    assert_eq!(assess("# Title\n\n| x |\n\n`code`", &source), Quality::Usable);
}

#[test]
fn malformed_html_still_converts() {
    let html = corpus("malformed.html");
    let candidate = conversion::convert(ConversionMethod::Native, &html).unwrap();
    assert!(candidate.markdown.contains("Unclosed paragraph"), "{}", candidate.markdown);
    assert!(candidate.markdown.contains("A heading anyway"));
}

#[test]
fn extraction_drops_the_surrounding_page_without_losing_the_article() {
    let html = corpus("article.html");
    let extracted = conversion::convert(ConversionMethod::Extracted, &html).unwrap().markdown;
    assert!(extracted.contains("constraint of the storage format"));
    assert!(!extracted.contains("Archive"), "extraction kept the navigation:\n{extracted}");
}

#[test]
fn basic_conversion_keeps_what_the_others_trim() {
    let html = corpus("documentation.html");
    let basic = conversion::convert(ConversionMethod::Basic, &html).unwrap().markdown;
    assert!(basic.contains("Troubleshoot"), "basic conversion is supposed to keep the navigation");
    // Even here, script and style text is never document content.
    assert!(!basic.contains("window.analytics"));
    assert!(!basic.contains("color:red"));
}

#[test]
fn the_chain_is_fixed_and_native_comes_first() {
    assert_eq!(
        conversion::chain(),
        [ConversionMethod::Native, ConversionMethod::Extracted, ConversionMethod::Basic]
    );
}

#[test]
fn the_html_only_methods_are_not_part_of_the_chain() {
    for method in [ConversionMethod::Source, ConversionMethod::Passthrough] {
        assert!(conversion::convert(method, "<p>x</p>").is_none());
    }
}

#[test]
fn plain_text_keeps_the_words_and_drops_the_markers() {
    let text = markdown_to_text(
        "# Title\n\nSome **bold** and a [label](https://example.com/target).\n\n- one\n- two",
    );
    assert!(text.contains("Title"));
    assert!(text.contains("Some bold and a label."), "{text}");
    assert!(!text.contains("https://example.com/target"), "plain text advertised a URL:\n{text}");
    assert!(!text.contains('#') && !text.contains('*'));
}

#[test]
fn plain_text_leaves_code_blocks_alone() {
    let text = markdown_to_text("Run:\n\n```\nhelm upgrade --set a.b=*\n```\n");
    assert!(text.contains("helm upgrade --set a.b=*"), "{text}");
}
