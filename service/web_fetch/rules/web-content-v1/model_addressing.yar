/*
 * Content that speaks to the model reading the page rather than to a reader.
 *
 * These phrasings are rare in ordinary prose and common in pages written to be
 * scraped by an agent.
 */
rule hearthai_model_addressing
{
    meta:
        description = "Page text addressed to an AI assistant with an imperative"
        policy = "web-content-v1"

    strings:
        $assistant_directive = /(^|[.!?)\]>]\s{0,4})(attention|note|important)?[:,\s-]{0,4}(ai\s+(assistant|agent|model)|language\s+model|chat\s?gpt|claude|assistant)\s*[,:]\s*(you\s+(must|should|will|need\s+to)|please\s+(ignore|send|fetch|visit|execute|run)|do\s+not\s+(tell|mention|inform))/ nocase
        $role_reassign = /(^|[.!?)\]>]\s{0,4})you\s+are\s+(now\s+)?(a\s+|an\s+)?(dan|developer\s+mode|jailbroken|unrestricted|in\s+developer\s+mode)/ nocase
        $hidden_note = /<!--[^>]{0,200}(ai\s+(assistant|agent)|language\s+model)[^>]{0,200}(must|should|ignore|instruct)/ nocase

    condition:
        any of them
}
