"""Normalize stored prose on its way OUT to an agent.

Requirement/design/task text is authored by agents over the skill and
agent-task endpoints, and different clients serialize newlines differently:
some send real line breaks, some send the two characters backslash-n inside
the JSON string. HARNOQ_INSTRUCTIONS asks for real breaks, but that is a soft
rule a client can ignore — and several designs are already stored with literal
\\n because openwebui/codex did exactly that. A coder then receives its binding
spec as one unbroken line.

This is a READ-SIDE repair only. Stored data is left exactly as written (the
UI reads it verbatim, so a write-time rewrite would be mutating the author's
content), and the escapes are turned into the breaks they were meant to be
only when the text is handed to an agent. Never call this before a write.
"""

# Only the literal two-character backslash-n. A scan of every text column
# found this is the ONLY stray escape in prose fields (\t and \r: zero
# occurrences), while \u and \\ appear only inside JSON-serialized columns
# (rejected_solutions, request_summary) where they are the legitimate output
# of json.dumps(ensure_ascii=True) — 2600+ \u encoding real Chinese that
# json.loads restores on read. A general un-escaper would destroy those, so
# this stays deliberately narrow: one sequence, prose only.
_LITERAL_NEWLINE = "\\n"


def normalize_agent_text(value):
    """Recursively turn the literal ``\\n`` escape into a real line break.

    Walks dicts and lists so a whole response envelope can be passed in. Only
    strings are touched; numbers, booleans and None pass through unchanged.
    """
    if isinstance(value, str):
        return value.replace(_LITERAL_NEWLINE, "\n")
    if isinstance(value, dict):
        return {key: normalize_agent_text(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize_agent_text(item) for item in value]
    return value
