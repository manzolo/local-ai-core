"""Builds the test audio of the transcriber and speakers services.

  make_audio.py OUT_TRANSCRIBER OUT_SPEAKERS

Two Piper voices trained on public-domain recordings (rhasspy/piper-voices, pinned revision):
  A = en_US-ljspeech-high (LJ Speech, public domain), B = en_US-norman-medium (LibriVox, public domain).
Out:
  transcriber/phrase.ogg            voice A says PHRASE
  speakers/dialogue.ogg + .json     six sentences, A B A B A B, 0.7 s of silence between; the .json holds the
                                    exact [start, end, text] of each sentence
  speakers/one-voice.ogg + .json    three sentences, all voice B
"""
import io
import json
import os
import subprocess
import sys
import urllib.request
import wave

REV = "e9c8a39444359388adaa94255becb37a5a2b8e51"
BASE = f"https://huggingface.co/rhasspy/piper-voices/resolve/{REV}/en/en_US"
VOICES = {"A": "ljspeech/high/en_US-ljspeech-high", "B": "norman/medium/en_US-norman-medium"}
PHRASE = "Seven blue boxes arrived at the station on Tuesday morning, and nobody opened them."
DIALOGUE = [
    ("A", "Good morning, I am calling about the broken window in the kitchen."),
    ("B", "Of course. When did you first notice the crack in the glass?"),
    ("A", "Last Sunday, after the storm, there was a long line across the corner."),
    ("B", "I can send someone on Thursday afternoon, if that works for you."),
    ("A", "Thursday is perfect, I will be at home all day."),
    ("B", "Great, the technician will call you thirty minutes before arriving."),
]
ONE_VOICE = [
    ("B", "The library opens at nine and closes at six on weekdays."),
    ("B", "On Saturday the reading room stays open until one in the afternoon."),
    ("B", "Books can be renewed online for another three weeks."),
]
GAP = 0.7
CACHE = "/tmp/voices"


def voice(key):
    from piper import PiperVoice
    os.makedirs(CACHE, exist_ok=True)
    name = VOICES[key]
    local = os.path.join(CACHE, os.path.basename(name))
    for ext in (".onnx", ".onnx.json"):
        if not os.path.exists(local + ext):
            urllib.request.urlretrieve(f"{BASE}/{name}{ext}", local + ext)
    return PiperVoice.load(local + ".onnx")


def say(v, text):
    """→ (raw 16-bit mono frames, sample rate)"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        v.synthesize_wav(text, w)
    buf.seek(0)
    with wave.open(buf) as r:
        return r.readframes(r.getnframes()), r.getframerate()


def to_ogg(frames, rate, out):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "s16le", "-ar", str(rate), "-ac", "1", "-i", "-",
                    "-c:a", "libopus", "-b:a", "32k", out], input=frames, check=True)


def montage(voices, lines, out):
    rate, frames, segs, t = None, b"", [], 0.0
    for key, text in lines:
        f, r = say(voices[key], text)
        rate = rate or r
        assert r == rate, "both voices must share a sample rate"
        dur = len(f) / 2 / rate
        segs.append([round(t, 2), round(t + dur, 2), text])
        silence = b"\x00\x00" * int(GAP * rate)
        frames += f + silence
        t += dur + GAP
    to_ogg(frames, rate, out + ".ogg")
    with open(out + ".json", "w") as fh:
        json.dump(segs, fh, indent=1)
    print(f"✓ {out}.ogg ({t:.1f} s, {len(segs)} sentences)")


def main():
    out_tr, out_sp = sys.argv[1], sys.argv[2]
    voices = {k: voice(k) for k in VOICES}
    f, r = say(voices["A"], PHRASE)
    to_ogg(f, r, os.path.join(out_tr, "phrase.ogg"))
    print(f"✓ {out_tr}/phrase.ogg")
    montage(voices, DIALOGUE, os.path.join(out_sp, "dialogue"))
    montage(voices, ONE_VOICE, os.path.join(out_sp, "one-voice"))


if __name__ == "__main__":
    main()
