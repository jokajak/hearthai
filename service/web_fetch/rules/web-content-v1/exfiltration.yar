/*
 * Content that asks for secrets to be produced or sent somewhere.
 *
 * Aimed at the instruction, not at the mere mention of a credential: pages that
 * document environment variables or API keys are ordinary and must keep working.
 */
rule hearthai_secret_exfiltration
{
    meta:
        description = "Instruction to reveal or transmit credentials or prior context"
        policy = "web-content-v1"

    strings:
        $reveal = /(^|[.!?)\]>]\s{0,4})(print|reveal|output|repeat|show|disclose|summarize)\s+(me\s+)?(your|the|all)\s+(system\s+prompt|initial\s+instructions|api[_\s-]?key|secret[_\s-]?key|access[_\s-]?token|credentials|\.env(\s+file)?)/ nocase
        $send   = /(^|[.!?)\]>]\s{0,4})(send|post|upload|exfiltrate|transmit|forward)\s+(the\s+|your\s+|all\s+)?(conversation|history|context|api[_\s-]?key|credentials|secret|token|file|contents)\s+(to|at)\s+(https?:\/\/|[a-z0-9.-]+@)/ nocase

    condition:
        any of them
}
