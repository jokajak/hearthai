/*
 * Chat-template and tool-call framing appearing inside fetched page text.
 *
 * A web page has no legitimate reason to contain a turn delimiter or a tool-call
 * envelope; documentation that discusses them normally shows them inside code
 * blocks, which is why this rule wants the raw delimiter forms.
 */
rule hearthai_chat_template_tokens
{
    meta:
        description = "Conversation-turn or tool-call framing in fetched content"
        policy = "web-content-v1"

    strings:
        $im_start     = "<|im_start|>"
        $im_end       = "<|im_end|>"
        $start_header = "<|start_header_id|>"
        $end_of_turn  = "<|eot_id|>"
        $inst_open    = "[INST]"
        $sys_close    = "</system>"
        $human_turn   = "\n\nHuman:"
        $antml        = "<invoke"

    condition:
        any of them
}
