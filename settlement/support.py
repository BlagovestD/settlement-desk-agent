"""Conservative extraction and independent checks for support-note leads."""

import re


MAX_NOTES = 8
MAX_THREAD_CHARS = 4800
CONFIRMATION = re.compile(
    r"\b(?:confirmed|verified|final|correct|actual)\s+(?:(?:bank|settlement)\s+)?"
    r"(?:trace|reference)\s*(?::|=|is\b)\s*([\w-]+)", re.IGNORECASE | re.ASCII)
WARNING = re.compile(r"\b(?:not|unverified|wrong|void|duplicate|cancelled|ignore|different|pending)\b", re.I)


def thread(bank):
    notes = sorted(bank.get("support_notes", []), key=lambda row: row["posted_at"])
    if len(notes) > MAX_NOTES or sum(len(row["text"]) for row in notes) > MAX_THREAD_CHARS:
        return []
    return notes


def mentions(text, trace):
    return re.search(rf"(?<![\w-]){re.escape(trace)}(?![\w-])", text) is not None


def verify_proposal(bank, trace, note_ids):
    """Check a lead against source records. Its interpretation remains subject to review."""
    notes = {row["id"]: row for row in thread(bank)}
    if (not isinstance(trace, str) or not trace or not isinstance(note_ids, list)
            or not 1 <= len(note_ids) <= 4 or any(not isinstance(key, str) or key not in notes for key in note_ids)
            or len(set(note_ids)) != len(note_ids)):
        return None
    matches = [row for row in bank["candidates"] if row["trace"] == trace]
    if len(matches) != 1 or matches[0]["id"] in bank["claimed_elsewhere"]:
        return None
    if not any(mentions(notes[key]["text"], trace) for key in note_ids):
        literal_sources = [key for key, row in notes.items() if mentions(row["text"], trace)]
        if len(literal_sources) != 1 or len(note_ids) == 4:
            return None
        note_ids = sorted([*note_ids, *literal_sources], key=lambda key: notes[key]["posted_at"])
    citations = [{"note_id": key, "quote": notes[key]["text"]} for key in note_ids]
    return {"trace": trace, "candidate_id": matches[0]["id"], "citations": citations}


def rules_proposal(bank):
    """Match a single observed reference with an explicit confirmation and clean context."""
    notes = thread(bank)
    mentioned = {row["trace"] for row in bank["candidates"] if row["trace"] and
                 any(mentions(note["text"], row["trace"]) for note in notes)}
    if len(mentioned) != 1 or any(WARNING.search(note["text"]) for note in notes):
        return None
    trace = next(iter(mentioned))
    for row in notes:
        if any(match[1] == trace for match in CONFIRMATION.finditer(row["text"])):
            return verify_proposal(bank, trace, [row["id"]])
    return None
