/*
 * HearthAI web content rules, v1.
 *
 * These look for text that addresses the model rather than the reader: an
 * instruction to disregard its own instructions, a request for its system
 * prompt or credentials, and the chat control tokens that only appear in a page
 * when someone is trying to forge a conversation boundary.
 *
 * They are patterns, not intent. Technical documentation that quotes these
 * phrases will match, and that is a tuning problem to record rather than a
 * reason to let the model decide which matches count. Equally, an instruction
 * phrased in a way these rules do not cover will pass: a non-match is not a
 * safety verdict.
 *
 * The inspector scans the raw bytes, the decoded body, bounded normalized
 * forms that undo HTML entities and invisible characters, and every converted
 * candidate, so a match anywhere in that set rejects the whole response.
 */

rule prompt_injection_instruction_override
{
    meta:
        description = "Text directing a model to discard its instructions"
        severity    = "reject"

    strings:
        $override = /(ignore|disregard|forget|override)\s+((all|any|the|these|those|your)\s+)*(previous|prior|earlier|above|preceding|system|your)\s+(instructions|prompts?|rules|directives|guidelines)/ nocase
        $instead  = /instead\s+of\s+(following|obeying)\s+(your|the)\s+(instructions|system\s+prompt|rules)/ nocase
        $new_task = /(your\s+new\s+(task|instructions?|role)\s+(is|are)\b)/ nocase

    condition:
        any of them
}

rule prompt_injection_prompt_disclosure
{
    meta:
        description = "Text asking a model to reveal its prompt, rules, or credentials"
        severity    = "reject"

    strings:
        $reveal = /(reveal|print|repeat|output|show|disclose|dump)\s+(me\s+)?(your|the)\s+(full\s+|complete\s+|entire\s+|initial\s+|original\s+)?(system\s+prompt|instructions|prompt|rules|configuration)/ nocase
        $verbatim = /repeat\s+everything\s+above\s+(verbatim|exactly)/ nocase
        $secrets = /(send|post|exfiltrate|forward|transmit|upload)\s+(the\s+|your\s+|all\s+)?(api[\s_-]?keys?|secrets?|credentials?|access[\s_-]?tokens?|environment\s+variables)/ nocase

    condition:
        any of them
}

rule prompt_injection_chat_control_tokens
{
    meta:
        description = "Chat-template control tokens embedded in fetched page content"
        severity    = "reject"

    strings:
        $im_start   = "<|im_start|>" nocase
        $im_end     = "<|im_end|>" nocase
        $endoftext  = "<|endoftext|>" nocase
        $start_hdr  = "<|start_header_id|>" nocase
        $turn       = "<|begin_of_text|>" nocase
        $human      = /^\s*\x5B(INST|\/INST)\x5D/ nocase

    condition:
        any of them
}

rule prompt_injection_hidden_directive
{
    meta:
        description = "An instruction addressed to an AI assistant inside markup a reader cannot see"
        severity    = "reject"

    strings:
        $hidden_style = /style\s*=\s*["'][^"']*(display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0)/ nocase
        $aria_hidden  = /aria-hidden\s*=\s*["']true["']/ nocase
        $addressed    = /\b(ai\s+assistant|language\s+model|chatbot|claude|chatgpt|copilot)\s*[,:]?\s*(you\s+(must|should|will|need\s+to)|please\s+(ignore|do|send|run|execute))/ nocase

    condition:
        $addressed and ($hidden_style or $aria_hidden)
}
