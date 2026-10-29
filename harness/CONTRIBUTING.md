# Research Harness Implementation Interface

This offline study compares one mechanism at a time with pinned Cloudflare
v0.2.5, commit a3520ccb7843156e2dd51e92e29caf0f6dafafa4. Production is untouched.
No private benchmark data, model APIs, answer labels in retrieval, or secret access.

Each direction module exports `experiments() -> list[Experiment]`, exactly ten
experiments per direction, unique IDs D01.01 ... D10.10.
Use only the Python standard library and numpy; no downloads from hook functions.
Import shared types from `harness.types`. Each experiment enables ONE hook only.
No parameter sweeps: each experiment must implement a substantively different
mechanism with its own falsifiable hypothesis. All 100 compare to the same base,
not a sequence where previous changes accumulate.

## Shared Types

`Message` frozen dataclass:
`id: str, request_id: str, session_id: str, position: int, role: str,
text: str, timestamp: int | None, ordinal: int`.

`Document` frozen dataclass:
`id: str, message_id: str, session_id: str, request_id: str, position: int,
offset: int, text: str, role: str, timestamp: int | None, ordinal: int,
index_text: str = "", context: str = ""`.
`text` always remains an exact original substring; `offset` is Python Unicode
code-point offset in the source message. `index_text` defaults to `text`.
Use `dataclasses.replace`; never mutate shared documents.

`Hit` frozen dataclass: `document: Document, score: float`.

`Context`:
- `messages: tuple[Message,...]`, `documents: tuple[Document,...]`.
- `tokens(text: str) -> list[str]`: baseline Unicode/CJK tokens.
- `focused(text: str) -> str`: baseline English query stopwords removed.
- `lexical(text: str, limit: int = 300) -> list[Hit]`: baseline FTS ranking.
- `message_documents: dict[str, tuple[Document,...]]`.
- `session_documents: dict[str, tuple[Document,...]]`.
- `idf: dict[str,float]`, `doc_tokens: dict[str,frozenset[str]]`.
No gold answers, evidence annotations, benchmark IDs/types, scoring metadata,
or private data is reachable through Context. The session/request IDs are
opaque source provenance, never parsed for "answer" markers.

`Experiment` frozen dataclass:
`id, direction, name, hypothesis, mechanism, limitation: str`
plus optional callable fields:
- `query_hook(query: str, ctx: Context) -> list[tuple[str,float]]`
  Replaces baseline lexical query lists (original2,focused2). Keep original.
- `document_hook(messages: tuple[Message,...], documents: tuple[Document,...])
  -> list[Document]`
  Changes segmentation/index representation; document source provenance must
  still match original substrings. Do not introduce generated answers.
- `retrieval_hook(query: str, hits: list[Hit], ctx: Context) -> list[Hit]`
  Changes candidate acquisition/ranking after baseline context expansion.
  Can call ctx.lexical, access source docs/messages, build local associations.
- `output_hook(query: str, hits: list[Hit], ctx: Context) -> list[Hit]`
  Changes ordering/selection/format before top100. May emit extractive subspans
  with corrected offsets and new stable IDs; no final answers/instructions.

All hooks must be deterministic and bounded by the input corpus. Never use
random randomness, current time, dataset labels, oracle relevance, or fixed
question-specific answer lists. Semantic language rules may be generic, not
fitted to benchmark case wording.

## Responsibilities

Each worker owns only assigned `harness/directions/dNN_*.py` and
`harness/tests/test_dNN_*.py`. Do not edit shared engine/types/catalog or
production code, install dependencies, commit/push, deploy, or call AML.
Write real implementations and targeted tests in the assigned files.
Tests should check actual behavioral effect, original-source provenance,
determinism, and a counterexample/limitation. Synthetic fixtures are only
regression checks, not the claimed performance benchmark.
Export concise plain English names and descriptions; avoid inflated Chinese.
The parent creates the runner, datasets, baseline, reports and full scoring.
