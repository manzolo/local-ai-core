"""Text ready for XTTS: what the voice must say instead of what is written, in short pieces.

Lessons learned by ear: XTTS says "dot" for a final full stop and stumbles on quotes; numbers read as
digits come out wrong; a long sentence drifts, so pieces stay short (MAX_CHARS) and are cut at commas when
a sentence is too long. English and Italian numbers are spelled out; other languages keep their digits.
"""
import re

MAX_CHARS = 220

_EN_UNITS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
             "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_EN_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_IT_UNITS = ["zero", "uno", "due", "tre", "quattro", "cinque", "sei", "sette", "otto", "nove", "dieci", "undici",
             "dodici", "tredici", "quattordici", "quindici", "sedici", "diciassette", "diciotto", "diciannove"]
_IT_TENS = ["", "", "venti", "trenta", "quaranta", "cinquanta", "sessanta", "settanta", "ottanta", "novanta"]


def number_en(n):
    if n < 20:
        return _EN_UNITS[n]
    if n < 100:
        t, u = divmod(n, 10)
        return _EN_TENS[t] + (f"-{_EN_UNITS[u]}" if u else "")
    if n < 1000:
        h, r = divmod(n, 100)
        return f"{_EN_UNITS[h]} hundred" + (f" and {number_en(r)}" if r else "")
    if n < 1_000_000:
        k, r = divmod(n, 1000)
        return f"{number_en(k)} thousand" + ((" and " if r < 100 else " ") + number_en(r) if r else "")
    m, r = divmod(n, 1_000_000)
    return f"{number_en(m)} million" + (f" {number_en(r)}" if r else "")


def number_it(n):
    if n < 20:
        return _IT_UNITS[n]
    if n < 100:
        t, u = divmod(n, 10)
        w = _IT_TENS[t]
        if u in (1, 8):
            w = w[:-1]  # ventuno, ventotto
        return w + (_IT_UNITS[u] if u else "")
    if n < 1000:
        h, r = divmod(n, 100)
        w = "cento" if h == 1 else _IT_UNITS[h] + "cento"
        if r and r // 10 == 8:
            w = w[:-1]  # centottanta
        return w + (number_it(r) if r else "")
    if n < 1_000_000:
        k, r = divmod(n, 1000)
        w = "mille" if k == 1 else number_it(k) + "mila"
        return w + (number_it(r) if r else "")
    m, r = divmod(n, 1_000_000)
    w = "un milione" if m == 1 else number_it(m) + " milioni"
    return w + (" " + number_it(r) if r else "")


def year_en(n):
    """1984 → nineteen eighty-four, 2026 → twenty twenty-six, 2005 → two thousand and five."""
    hi, lo = divmod(n, 100)
    if 2000 <= n < 2010:
        return number_en(n)
    return f"{number_en(hi)} {number_en(lo) if lo >= 10 else ('hundred' if lo == 0 else 'oh ' + number_en(lo))}"


def spell(text, lang):
    """Times, decimals, years and integers in words, for English and Italian; digit strings that look like
    codes (a leading zero, more than seven digits) are read digit by digit."""
    if lang not in ("en", "it"):
        return text
    num = number_en if lang == "en" else number_it

    def digits(s):
        return " ".join(num(int(c)) for c in s)

    def time(m):
        h, mi = int(m.group(1)), int(m.group(2))
        if lang == "it":
            return num(h) + (" e " + num(mi) if mi else "")
        return num(h) + (" o'clock" if mi == 0 else (" oh " + num(mi) if mi < 10 else " " + num(mi)))

    text = re.sub(r"\b(\d{1,2}):(\d{2})\b", time, text)
    sep = " point " if lang == "en" else " virgola "
    text = re.sub(r"\b(\d+)[.,](\d+)\b",
                  lambda m: num(int(m.group(1))) + sep + (num(int(m.group(2))) if not m.group(2).startswith("0")
                                                          else digits(m.group(2))), text)

    def integer(m):
        s = m.group(0)
        if (s.startswith("0") and len(s) > 1) or len(s) > 7:
            return digits(s)
        n = int(s)
        if lang == "en" and len(s) == 4 and 1100 <= n <= 2099:
            return year_en(n)
        return num(n)
    return re.sub(r"\b\d+\b", integer, text)


def sentences(text):
    text = re.sub(r"[«»\"“”„]", "", text)
    parts = re.split(r"(?<=[.!?…])\s+", text.strip())
    clean = [re.sub(r"(\.\.\.|…|\.)+$", "", p).strip() for p in parts]
    return [p for p in clean if re.search(r"\w", p)]


def pieces(text, lang="en", max_chars=MAX_CHARS):
    """→ [(piece, pause_after_seconds)]: a short pause between pieces of a sentence, a longer one between
    sentences and a longer still between paragraphs."""
    out = []
    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    for pi, para in enumerate(paragraphs):
        sents = sentences(spell(" ".join(para.split()), lang))
        for si, s in enumerate(sents):
            chunks = [s]
            if len(s) > max_chars:  # too long for one breath: cut at commas, then at spaces
                chunks, cur = [], ""
                for part in re.split(r"(?<=[,;:])\s+", s):
                    if cur and len(cur) + len(part) + 1 > max_chars:
                        chunks.append(cur)
                        cur = part
                    else:
                        cur = f"{cur} {part}".strip()
                    while len(cur) > max_chars:
                        cut = cur.rfind(" ", 0, max_chars)
                        cut = cut if cut > 0 else max_chars
                        chunks.append(cur[:cut])
                        cur = cur[cut:].strip()
                if cur:
                    chunks.append(cur)
            for ci, c in enumerate(chunks):
                last_chunk = ci == len(chunks) - 1
                last_sentence = si == len(sents) - 1
                pause = (0.25 if not last_chunk else 0.6 if not last_sentence else 1.1)
                out.append((re.sub(r"[,;:]$", "", c).strip(), pause))
        if pi == len(paragraphs) - 1 and out:
            out[-1] = (out[-1][0], 0.0)
    return [(c, p) for c, p in out if re.search(r"\w", c)]
