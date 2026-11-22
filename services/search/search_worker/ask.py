"""Answers grounded in the chunks the search found, with every citation checked.

The model may answer only from the numbered excerpts and must say when they are not enough. A citation
number that matches no excerpt is reported, never trusted: an invented number is the first sign of an
invented answer. With no excerpts at all the model is not called.

A valid citation is not enough: the model can put a real number in brackets after a made-up amount. So
every figure in the answer must also appear in the excerpts it cites (or in the question); the ones that
do not are reported and the answer is not grounded. This checks figures, not meaning: a wrong sentence
without numbers still passes, which is why the page always shows the excerpts next to the answer.
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


def figures(text):
    """The numbers in a text, in one spelling: 1,800 = 1.800 = 1800, 180.00 = 180, 6,50 = 6.5, 07 = 7.
    A date gives its parts (14.03.2026 or 2026-03-14 → 14, 3, 2026)."""
    def whole(x):
        return x.lstrip("0") or "0"

    out = []
    for m in re.findall(r"\d+(?:[.,]\d+)*", text):
        seps = re.findall(r"[.,]", m)
        if len(set(seps)) == 2 and seps[-1] not in seps[:-1]:  # 1.800,00 or 1,800.00: the last one is the decimal
            head, frac = m.rsplit(seps[-1], 1)
            m = re.sub(r"[.,]", "", head) + "." + frac
        parts = re.split(r"[.,]", m)
        if len(parts) > 1 and all(len(x) == 3 for x in parts[1:]):  # thousands separators
            out.append(whole("".join(parts)))
        elif len(parts) == 2:  # a decimal, whichever separator
            frac = parts[1].rstrip("0")
            out.append(whole(parts[0]) + ("." + frac if frac else ""))
        else:
            out += [whole(x) for x in parts]
    return out


def unsupported_figures(answer, question, cited_sources):
    """Figures of the answer found neither in the excerpts it cites nor in the question, in order."""
    body = re.sub(r"\[\d+(?:\s*[,;]\s*\d+)*\]", " ", answer)  # the citations themselves
    body = re.sub(r"(?m)^\s*\d+[.)]\s", " ", body)  # the numbers of a numbered list
    known = set(figures(question))
    for s in cited_sources:
        known.update(figures(s["text"] + " " + " ".join(s.get("heads") or []) + " " + s.get("title", "")))
    bad = []
    for f in figures(body):
        if f not in known and f not in bad:
            bad.append(f)
    return bad


def result(answer, sources, model, secs, truncated=False, question=""):
    cited, invalid = citations(answer, len(sources))
    refs = [{"n": k, "file": sources[k - 1]["file"], "line": sources[k - 1]["line"]} for k in cited]
    unsupported = unsupported_figures(answer, question, [sources[k - 1] for k in cited])
    return {"answer": answer, "cited": refs, "invalid": invalid, "unsupported": unsupported,
            "sources": len(sources), "model": model, "secs": secs, "truncated": truncated,
            "grounded": bool(refs) and not invalid and not unsupported}
