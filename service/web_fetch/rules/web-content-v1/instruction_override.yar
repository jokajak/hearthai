/*
 * Content that tells a model to discard its instructions.
 *
 * The leading boundary matters. "ignore previous instructions" inside a quoted
 * phrase - which is how documentation about prompt injection writes it - does
 * not match, while the same words as an imperative at the start of a line or
 * sentence do. That is a deliberate trade: see the false-positive notes in
 * docs/runbooks/webfetch.md.
 */
rule hearthai_instruction_override
{
    meta:
        description = "Imperative instruction to disregard prior instructions"
        policy = "web-content-v1"

    strings:
        $override = /(^|[.!?)\]>]\s{0,4})(now\s+)?(please\s+)?(ignore|disregard|forget|override)\s+(any\s+|all\s+|the\s+)?(of\s+)?(your\s+|the\s+)?(previous|prior|earlier|above|preceding|system|original|initial)\s+(\w+\s+){0,2}(instruction|instructions|prompt|prompts|rule|rules|direction|directions|guideline|guidelines)/ nocase
        $replace  = /(^|[.!?)\]>]\s{0,4})(your\s+new\s+(instructions|task)\s+(is|are)|from\s+now\s+on\s+you\s+(must|will|should)\s+(ignore|disregard|forget))/ nocase

    condition:
        any of them
}
