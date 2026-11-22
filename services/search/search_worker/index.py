"""The semantic index: chunks in a JSON file, their vectors in a float32 file next to it.

  <base>.json   chunks (file, line, headings, text), per-file sha1, model, dimension, sha256 of the vectors
  <base>.f32    normalized vectors, one after the other

Updates are incremental: only files whose sha1 changed are embedded again. An index whose vectors and
chunks do not add up is refused, never searched: a search over misaligned vectors would return
plausible-looking nonsense without saying so. Counting is not enough: the two files are replaced one
after the other, and a save cut in between leaves new vectors next to old chunks, often as many as before.
So the JSON carries the sha256 of the vectors it was written with, and a pair that does not match is
refused.
"""
import array
import hashlib
import json
import os
import re

from .chunks import chunks_of, embed_text, list_files, title_of

INDEX_VERSION = 1
BATCH = 16
WORD = re.compile(r"[\wàèéìòù]{4,}", re.I)


class IndexError_(RuntimeError):
    """The index on disk is unreadable or inconsistent: it must be rebuilt."""


def load(base):
    meta_p, vec_p = base + ".json", base + ".f32"
    if not os.path.exists(meta_p) or not os.path.exists(vec_p):
        return None
    try:
        with open(meta_p, encoding="utf-8") as f:
            meta = json.load(f)
        vec = array.array("f")
        with open(vec_p, "rb") as f:
            vec.frombytes(f.read())
    except (OSError, ValueError) as e:
        raise IndexError_(f"index unreadable ({e}): rebuild it") from None
    dim = meta.get("dim", 0)
    if meta.get("version") != INDEX_VERSION or not dim or len(vec) != dim * len(meta.get("chunks", [])):
        raise IndexError_("index is inconsistent (vectors and chunks do not match): rebuild it")
    # an index written before the checksum existed has none: update() adds it at the next pass
    if "vec_sha256" in meta and meta["vec_sha256"] != vec_sha256(vec):
        raise IndexError_("index is inconsistent (the vectors are not the ones saved with these chunks, "
                          "as after an interrupted save): rebuild it")
    return meta, vec


def vec_sha256(vec):
    return hashlib.sha256(vec.tobytes()).hexdigest()


def save(base, meta, vec):
    os.makedirs(os.path.dirname(base) or ".", exist_ok=True)
    meta["vec_sha256"] = vec_sha256(vec)
    for p, data in ((base + ".f32", vec.tobytes()),
                    (base + ".json", json.dumps(meta, ensure_ascii=False).encode())):
        tmp = p + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, p)


def files_of(roots):
    """(relative name, path) for every .md under the roots. roots: [(prefix, folder)], a missing folder is
    skipped; a prefix keeps two folders apart in the index ("library/invoice.md")."""
    out = []
    for prefix, root in roots:
        if not os.path.isdir(root):
            continue
        for p in list_files(root):
            rel = os.path.relpath(p, root)
            out.append((f"{prefix}/{rel}" if prefix else rel, p))
    return out


async def update(ollama, roots, base, model, rebuild=False, progress=None):
    """Brings the index in line with the files under the roots ([(prefix, folder)], or one folder).
    progress(done, total) is awaited per batch. Returns (meta, vec, stats)."""
    if isinstance(roots, str):
        roots = [("", roots)]
    old = None if rebuild else load(base)
    if old and old[0].get("model") != model:
        old = None
    old_meta, old_vec = old if old else ({"files": {}, "chunks": [], "dim": 0}, array.array("f"))
    dim = old_meta.get("dim", 0)

    files = files_of(roots)
    by_file = {}
    for i, c in enumerate(old_meta["chunks"]):
        by_file.setdefault(c["file"], []).append(i)

    new_chunks, new_vec, todo, file_info = [], array.array("f"), [], {}
    kept = changed = 0
    for rel, p in files:
        try:
            with open(p, "rb") as f:
                raw = f.read()
        except OSError:
            continue
        sha = hashlib.sha1(raw).hexdigest()
        file_info[rel] = sha
        if old_meta["files"].get(rel) == sha and rel in by_file:
            for i in by_file[rel]:
                new_chunks.append(old_meta["chunks"][i])
                new_vec.extend(old_vec[i * dim:(i + 1) * dim])
            kept += 1
            continue
        changed += 1
        text = raw.decode("utf-8", errors="replace")
        for c in chunks_of(text, title_of(p, text)):
            c["file"] = rel
            todo.append(c)
    removed = len(set(old_meta["files"]) - set(file_info))

    done = 0
    for k in range(0, len(todo), BATCH):
        part = todo[k:k + BATCH]
        vecs = await ollama.embed(model, [embed_text(c) for c in part])
        if not dim:
            dim = len(vecs[0])
        for c, v in zip(part, vecs, strict=True):
            if len(v) != dim:
                raise IndexError_(f"the model changed dimension ({len(v)} instead of {dim}): rebuild")
            new_chunks.append(c)
            new_vec.extend(v)
        done += len(part)
        if progress:
            await progress(done, len(todo))

    if todo or removed or not old or kept != len(old_meta["files"]) or "vec_sha256" not in old_meta:
        meta = {"version": INDEX_VERSION, "model": model, "dim": dim, "files": file_info, "chunks": new_chunks}
        save(base, meta, new_vec)
    else:
        meta = old_meta
    stats = {"files": len(file_info), "chunks": len(new_chunks), "changed": changed, "removed": removed,
             "kept": kept}
    return meta, new_vec, stats


async def query(ollama, meta, vec, q, n=8, min_score=0.35):
    """The best chunks for q, at most two per file (more files beat the same file repeated)."""
    qv = (await ollama.embed(meta["model"], [q]))[0]
    dim = meta["dim"]
    words = {w.lower() for w in WORD.findall(q)}
    scored = []
    for i, c in enumerate(meta["chunks"]):
        v = vec[i * dim:(i + 1) * dim]
        s = sum(a * b for a, b in zip(qv, v, strict=True))
        if words:  # a small push to chunks holding the exact words (names, codes)
            body = (c["text"] + " " + c["title"]).lower()
            s += 0.05 * sum(1 for w in words if w in body) / len(words)
        scored.append((s, i))
    scored.sort(reverse=True)
    res, per_file = [], {}
    for s, i in scored:
        if s < min_score:
            break
        c = meta["chunks"][i]
        if per_file.get(c["file"], 0) >= 2:
            continue
        per_file[c["file"]] = per_file.get(c["file"], 0) + 1
        res.append({"score": round(s, 3), "file": c["file"], "line": c["line"], "heads": c["heads"],
                    "title": c["title"], "text": c["text"]})
        if len(res) >= n:
            break
    return res
