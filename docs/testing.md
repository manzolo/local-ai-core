# Testing

[← back to the index](../README.md)

Every service is tested the same way: **known data → expected output**, plus at least one **error put in
on purpose** that the test must catch. A test that never fails proves nothing.

| Level | Command | Where | What it proves |
|---|---|---|---|
| unit and API | `make test` | CPU, CI | the gateway, the workers and the web page work against a fake Ollama |
| mutations | `make mutations` | CPU, CI | the tests catch real mistakes: each mutation breaks the code and must make the tests fail |
| privacy | `make grep-private` | CI | no home paths, LAN addresses, e-mails or keys in the repository |
| real GPU | `make collaudo` | your machine | the real models give the expected answers |

## The built-in tests

Each worker carries its own test, run by `make collaudo`, by `POST /v1/services/{name}/test` and by
**Services → Run test** in the web page. For search and ask, on three invented notes (a pellet stove, a
bathroom repair, a vegetable garden):

| Check | Expected |
|---|---|
| three questions with words different from the notes | each finds its note first |
| one note changed | the update reads 1 file again and keeps 2 |
| **introduced error**: an index truncated by 64 bytes | refused, never searched |
| a question the notes answer | the right figure (180 euros), citing the right note |
| a question the notes do not answer | says so, invents nothing |
| **introduced error**: an answer citing `[9]` with 6 excerpts | `[9]` flagged as invalid |

For documents, on an invented electricity bill printed to PDF and "scanned": the text PDF gives back its
title, table and amounts; a figure changed in the expected text makes the comparison fail; the scan is
read with OCR; with OCR off the scan is refused rather than "read"; a broken PDF is an error message.
See [Documents](services/documents.md#built-in-test).

For speech, on synthetic sentences spoken by two public-domain voices: a known sentence is transcribed
(similarity ≥ 0.85), a different expected text is reported as not matching, a file that is not audio is
an error; a dialogue of two alternating voices gives two speakers in the right order, one voice stays one
speaker. See [Transcribe and speakers](services/transcribe.md#built-in-tests).

## The mutations

`scripts/mutations.sh` changes one line at a time, runs the tests and puts the line back:

| Mutation | Must be caught by |
|---|---|
| an invented citation is accepted | the citation tests |
| a truncated index is accepted | the index tests |
| every update re-reads every file | the incremental update test |
| the model is called with no excerpts | the "nothing found" test |
| a wrong API key is accepted | the authentication test |
| the upload limit ignores chunked bodies | the 413 test |
| the GPU queue lets every job through | the queue test |
| the download progress shows one layer, not the model | the startup test |
| a scan read without OCR counts as converted | the scan test |
| the documents worker reads files outside the uploads volume | the path test |
| uploaded files outlive their job | the clean-up test |
| a full GPU does not make Ollama free its models | the VRAM test |
| SRT timestamps use a dot instead of a comma | the subtitle test |
| speakers are not numbered in order of first speech | the numbering test |
| every voice is put into one speaker | the dialogue test |
| a transcript that says something else still matches | the expected-text test |
| `speakers=true` is silently ignored | the speakers tests |

## Running the CPU tests locally

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements/dev.txt
make test lint mutations
```
