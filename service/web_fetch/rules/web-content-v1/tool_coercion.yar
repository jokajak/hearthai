/*
 * Content that tries to drive the agent's next action.
 *
 * A fetched page may describe commands; what it may not do is tell the reader's
 * agent to run one, and the difference is the imperative addressed outward.
 */
rule hearthai_tool_coercion
{
    meta:
        description = "Instruction directing an agent to take an action outside the page"
        policy = "web-content-v1"

    strings:
        $run_command = /(^|[.!?)\]>]\s{0,4})(immediately\s+|now\s+)?(run|execute|eval)\s+(the\s+following\s+)?(command|script|code)\s+(without\s+(asking|confirmation|telling)|before\s+(replying|answering|continuing))/ nocase
        $silent      = /(^|[.!?)\]>]\s{0,4})do\s+not\s+(tell|inform|mention\s+to|notify)\s+(the\s+)?(user|human|operator)\s+(about|that|of)/ nocase
        $fetch_next  = /(^|[.!?)\]>]\s{0,4})(fetch|visit|open|browse\s+to)\s+(https?:\/\/\S{1,200})\s+(and|then)\s+(follow|execute|obey|do)\s+/ nocase

    condition:
        any of them
}
