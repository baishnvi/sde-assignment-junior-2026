# AI Usage Disclosure

AI tools are allowed. We want to know **how** you used them, because using them well is a
skill. Honest answers are never held against you. Discovering undisclosed use is.

## Tools used
Claude (Anthropic), in a chat conversation.

## What I used them for
Claude read the assignment repo, chose Assignment A, and generated the first full draft of the solution:
the schema/DDL, ingestion code, report derivation, CLI, the test suite (including the oracle used for
cross-checking), and the drafts of `SUBMISSION.md` and this file. It also ran the code and the tests on
the generated data (seeds 42, 7, 2024 and the large scale) and reported timings.

## What I wrote or decided myself
_EDIT before submitting; write only what is true. For example: which assignment I wanted to submit, the
changes I made after reading the code, decisions I reviewed and agreed or disagreed with (see the decision
table in SUBMISSION.md), any changes I made while preparing for the interview._

## Where the AI was wrong or unhelpful
_EDIT: add what you notice while reading and running the code. Candid notes from the generation process:_
- Claude's first notion of the idempotency key was a content hash of the file; it switched to
  `(drop, relative path)` + hash check, because a content-only key would wrongly skip a re-uploaded
  identical lead file whose rows should count as merged duplicates.
- In the first draft of `leads.py` the default-value logic had an incorrect condition that skipped defaults for
  PII fields (no shipped schema has one, so no test would have shown it); Claude caught it on re-reading and fixed it.

## How I verified AI-generated code
Test suite (`python -m pytest solution`, 83 tests), including a naive independent re-implementation of the
spec cross-checked on three generator seeds; manual runs of the CLI on small and large data; the README's
own example values (`0.5625`, `2026-10-14T17:55:24+05:30`) reproduced in the output; a grep of the DB files
for plaintext names/phones. _EDIT: add what you personally read, ran, or changed._
