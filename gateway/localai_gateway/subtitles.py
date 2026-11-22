"""Transcripts as plain text, SRT and WebVTT, with the speaker in front of each line when known."""


def stamp(sec, sep):
    """Seconds → 00:01:02,345 (SRT, sep ",") or 00:01:02.345 (WebVTT, sep ".")."""
    ms = int(round(max(sec, 0) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def line(seg):
    return f"Speaker {seg['speaker']}: {seg['text']}" if seg.get("speaker") else seg["text"]


def text(segs):
    """Plain text; with speakers, one paragraph per change of speaker."""
    if not any(s.get("speaker") for s in segs):
        return " ".join(s["text"] for s in segs).strip()
    out, cur = [], None
    for s in segs:
        if s["speaker"] != cur:
            out.append(f"Speaker {s['speaker']}: {s['text']}")
            cur = s["speaker"]
        else:
            out[-1] += " " + s["text"]
    return "\n\n".join(out)


def srt(segs):
    return "\n".join(f"{i}\n{stamp(s['start'], ',')} --> {stamp(s['end'], ',')}\n{line(s)}\n"
                     for i, s in enumerate(segs, 1))


def vtt(segs):
    return "WEBVTT\n\n" + "\n".join(f"{stamp(s['start'], '.')} --> {stamp(s['end'], '.')}\n{line(s)}\n" for s in segs)
