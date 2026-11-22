"""Markdown files → chunks that carry their own context.

A chunk is one or more paragraphs, up to about CHUNK_CHARS characters, cut at headings first. Each chunk
remembers the file title and the chain of headings above it, so a chunk under "Next step" still says
what it is the next step of.
"""
import os
import re

CHUNK_CHARS = 1200
SKIP_DIRS = {".git", "node_modules", "out", "__pycache__", ".obsidian", ".trash"}
HEADING = re.compile(r"^(#{1,6})\s+(.*)")


def list_files(root):
    """Every .md file under root, sorted, skipping hidden and tool folders."""
    out = []
    for dp, dn, fn in os.walk(root):
        dn[:] = sorted(d for d in dn if d not in SKIP_DIRS and not d.startswith("."))
        out += [os.path.join(dp, f) for f in sorted(fn) if f.endswith(".md")]
    return sorted(out)


def split_frontmatter(lines):
    """Front matter (--- … ---) becomes its own short first chunk: status fields matter."""
    if lines and lines[0].strip() == "---":
        for i in range(1, min(len(lines), 40)):
            if lines[i].strip() == "---":
                return lines[1:i], i + 1
    return [], 0


def title_of(path, text):
    m = re.search(r"^#\s+(.+)$", text, re.M)
    return m.group(1).strip() if m else os.path.splitext(os.path.basename(path))[0]


def chunks_of(text, title):
    lines = text.splitlines()
    fm, start = split_frontmatter(lines)
    out = []
    if fm:
        out.append({"line": 1, "heads": [], "text": "\n".join(fm)})
    heads = []
    buf, buf_line = [], start + 1

    def flush():
        body = "\n".join(buf).strip()
        if body:
            out.append({"line": buf_line, "heads": list(heads), "text": body})

    para, para_line, size = [], start + 1, 0
    for i, ln in enumerate(lines[start:], start=start + 1):
        m = HEADING.match(ln)
        if m:
            buf += para
            para = []
            flush()
            buf, size = [], 0
            lvl = len(m.group(1))
            heads[:] = heads[:lvl - 1] + [m.group(2).strip()]
            buf_line = i + 1
            continue
        if ln.strip() == "":
            if para:
                if size + sum(len(x) for x in para) > CHUNK_CHARS and buf:
                    flush()
                    buf, size, buf_line = [], 0, para_line
                if not buf:
                    buf_line = para_line
                buf += para + [""]
                size += sum(len(x) for x in para)
                para = []
            continue
        if not para:
            para_line = i
        para.append(ln)
    if para:
        if size + sum(len(x) for x in para) > CHUNK_CHARS and buf:
            flush()
            buf, buf_line = [], para_line
        if not buf:
            buf_line = para_line
        buf += para
    flush()

    # a single paragraph over twice the size is cut at line boundaries
    final = []
    for c in out:
        if len(c["text"]) <= 2 * CHUNK_CHARS:
            final.append(c)
            continue
        cur, cur_line, n = [], c["line"], 0
        for j, ln in enumerate(c["text"].splitlines()):
            if n + len(ln) > CHUNK_CHARS and cur:
                final.append({"line": cur_line, "heads": c["heads"], "text": "\n".join(cur)})
                cur, cur_line, n = [], c["line"] + j, 0
            cur.append(ln)
            n += len(ln)
        if cur:
            final.append({"line": cur_line, "heads": c["heads"], "text": "\n".join(cur)})
    for c in final:
        c["title"] = title
    return final


def embed_text(c):
    """What the embedding model reads: file title › headings, then the text."""
    return " › ".join([c["title"]] + c["heads"]) + "\n" + c["text"]
