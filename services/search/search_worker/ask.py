"""Answers grounded in the chunks the search found, with every citation checked.

The model may answer only from the numbered excerpts and must say when they are not enough. A citation
number that matches no excerpt is reported, never trusted: an invented number is the first sign of an
invented answer. With no excerpts at all the model is not called.
"""
import re

SYSTEM = """You answer questions using ONLY the numbered excerpts from the user's notes: [1], [2], ...
Rules:
- Answer in the same language as the question, short and direct (usually 2-6 sentences; a list for steps).
- After each statement put, in square brackets, the number of the excerpt it comes from, e.g. [2] or [1][3].
- Use only the numbers of excerpts you were given. Never invent facts, dates, figures or names.
- If the excerpts do not answer the question, say plainly that the notes do not contain the answer
  (for example "I can't find this in the notes") and, if something comes close, say what it is.
- Write dates as they appear in the notes."""

NOT_FOUND = "I can't find anything about this in the notes."


def build_prompt(question, sources):
    parts = []
    for i, s in enumerate(sources, 1):
        where = " › ".join(s.get("heads") or [])
        parts.append(f"[{i}] {s['file']}:{s['line']}" + (f" — {where}" if where else "") + "\n" + s["text"].strip())
    return "Excerpts from the notes:\n\n" + "\n\n".join(parts) + f"\n\nQuestion: {question}"


def citations(text, n_sources):
    """Cited numbers in order of first appearance, and the ones that match no excerpt."""
    seen, bad = [], []
    for grp in re.findall(r"\[(\d+(?:\s*[,;]\s*\d+)*)\]", text):
        for k in re.split(r"\s*[,;]\s*", grp):
            k = int(k)
            target = seen if 1 <= k <= n_sources else bad
            if k not in target:
                target.append(k)
    return seen, bad


def result(answer, sources, model, secs, truncated=False):
    cited, invalid = citations(answer, len(sources))
    refs = [{"n": k, "file": sources[k - 1]["file"], "line": sources[k - 1]["line"]} for k in cited]
    return {"answer": answer, "cited": refs, "invalid": invalid, "sources": len(sources), "model": model,
            "secs": secs, "truncated": truncated, "grounded": bool(refs) and not invalid}
