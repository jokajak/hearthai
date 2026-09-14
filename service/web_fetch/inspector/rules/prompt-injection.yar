/*
 * HearthAI web content rules, v1.
 *
 * These look for text that addresses the model rather than the reader: an
 * instruction to disregard its own instructions, a request for its system
 * prompt or credentials, and the chat control tokens that only appear in a page
 * when someone is trying to forge a conversation boundary.
 *
 * ## The explanatory-context exception, and what it costs
 *
 * A page explaining prompt injection contains prompt injection. The first
 * version of this bundle could not tell the two apart, so it withheld security
 * documentation and prompt-engineering write-ups - which made the tool
 * unpleasant to use on exactly the material most worth reading.
 *
 * $explains is the second signal that separates them: an injection being
 * described is nearly always introduced ("for example", "an attacker might"),
 * or sits inside a code block. A directive occurring within 300 bytes after one
 * of those markers is treated as quoted rather than addressed to the model. A
 * directive with no such marker before it still matches, so an injection in
 * plain visible body text is not given away by this exception.
 *
 * This is a deliberate weakening and it is evadable: a page that writes "For
 * example, ignore all previous instructions and ..." is suppressed by the same
 * mechanism that lets the documentation through, and no pattern distinguishes
 * those two. The bargain is far fewer useless rejections in exchange for a
 * bypass that costs an attacker one sentence.
 *
 * prompt_injection_hidden_directive is deliberately left out of the exception:
 * markup a reader cannot see has no innocent explanation.
 *
 * These remain patterns, not intent. An instruction phrased outside the bundle
 * passes; a non-match is not a safety verdict.
 *
 * ## Why the loops count to 64
 *
 * The scanner is configured with max_matches_per_pattern = 64, so #directive
 * and #explains can never exceed it and the bound changes no verdict. It is
 * written as a constant because the compiler runs with error_on_slow_loop, and
 * a range the compiler cannot see an end to is refused - correctly, since an
 * unbounded pair of nested loops over a hostile page is a way to spend the
 * whole inspection budget. The rules test asserts the two numbers still agree.
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
        $directive = /((ignore|disregard|forget|override)\s+((all|any|the|these|those|your)\s+)*(previous|prior|earlier|above|preceding|system|your)\s+(instructions|prompts?|rules|directives|guidelines)|instead\s+of\s+(following|obeying)\s+(your|the)\s+(instructions|system\s+prompt|rules)|your\s+new\s+(task|instructions?|role)\s+(is|are)\s)/ nocase

        $explains = /(for example|for instance|e\.g\.|such as|example of|an attacker|attackers|malicious (page|site|content|input)|prompt[\s-]?injection|injection attack|jailbreak|demonstrat|illustrat|this payload|looks like:|reads:|<pre\b|<code\b|```|&lt;pre|&lt;code)/ nocase

    condition:
        // At least one directive that no explanatory marker introduces. With no
        // markers at all the inner loop is empty and false, so an unadorned
        // directive still matches.
        for any i in (1..64) : (
            i <= #directive and not (
                for any j in (1..64) : (
                    j <= #explains
                        and @explains[j] < @directive[i]
                        and @directive[i] - @explains[j] <= 300
                )
            )
        )
}

rule prompt_injection_prompt_disclosure
{
    meta:
        description = "Text asking a model to reveal its prompt, rules, or credentials"
        severity    = "reject"

    strings:
        $directive = /((reveal|print|repeat|output|show|disclose|dump)\s+(me\s+)?(your|the)\s+(full\s+|complete\s+|entire\s+|initial\s+|original\s+)?(system\s+prompt|instructions|prompt|rules|configuration)|repeat\s+everything\s+above\s+(verbatim|exactly)|(send|post|exfiltrate|forward|transmit|upload)\s+(the\s+|your\s+|all\s+)?(api[\s_-]?keys?|secrets?|credentials?|access[\s_-]?tokens?|environment\s+variables))/ nocase

        $explains = /(for example|for instance|e\.g\.|such as|example of|an attacker|attackers|malicious (page|site|content|input)|prompt[\s-]?injection|injection attack|jailbreak|demonstrat|illustrat|this payload|looks like:|reads:|<pre\b|<code\b|```|&lt;pre|&lt;code)/ nocase

    condition:
        for any i in (1..64) : (
            i <= #directive and not (
                for any j in (1..64) : (
                    j <= #explains
                        and @explains[j] < @directive[i]
                        and @directive[i] - @explains[j] <= 300
                )
            )
        )
}

rule prompt_injection_chat_control_tokens
{
    meta:
        description = "Chat-template control tokens embedded in fetched page content"
        severity    = "reject"

    strings:
        $directive = /(<\|im_start\|>|<\|im_end\|>|<\|endoftext\|>|<\|start_header_id\|>|<\|begin_of_text\|>|\x5BINST\x5D|\x5B\/INST\x5D)/ nocase

        // Documentation about chat templates is the whole false-positive class
        // here, so the vocabulary of that documentation joins the markers.
        $explains = /(for example|for instance|e\.g\.|such as|example of|an attacker|attackers|malicious (page|site|content|input)|prompt[\s-]?injection|injection attack|jailbreak|demonstrat|illustrat|this payload|looks like:|reads:|<pre\b|<code\b|```|&lt;pre|&lt;code|chat template|tokeni[sz]er|special tokens?|control tokens?)/ nocase

    condition:
        for any i in (1..64) : (
            i <= #directive and not (
                for any j in (1..64) : (
                    j <= #explains
                        and @explains[j] < @directive[i]
                        and @directive[i] - @explains[j] <= 300
                )
            )
        )
}

rule prompt_injection_hidden_directive
{
    meta:
        description = "An instruction addressed to an AI assistant inside markup a reader cannot see"
        severity    = "reject"
        note        = "No explanatory-context exception; hidden markup has no innocent explanation"

    strings:
        $hidden_style = /style\s*=\s*["'][^"']*(display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0)/ nocase
        $aria_hidden  = /aria-hidden\s*=\s*["']true["']/ nocase
        $addressed    = /\b(ai\s+assistant|language\s+model|chatbot|claude|chatgpt|copilot)\s*[,:]?\s*(you\s+(must|should|will|need\s+to)|please\s+(ignore|do|send|run|execute))/ nocase

    condition:
        $addressed and ($hidden_style or $aria_hidden)
}
