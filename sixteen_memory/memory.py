import hashlib
import heapq
import json
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi

from .schema import AddRequest, SearchRequest, Settings


class Conflict(Exception):
    pass


class Capacity(Exception):
    pass


def tokens(text):
    # CJK characters are separate terms; no Latin-only tokenization assumption.
    return re.findall(r"[^\W_]+|[\u3400-\u9fff]", re.sub(
        r"([\u3400-\u9fff])", r" \1 ", text.casefold()
    ))


def chunks(text, size):
    step = size - min(160, size // 5)
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        yield start, text[start:end]
        if end == len(text):
            break
        start += step


class Memory:
    def __init__(self, settings: Settings, models):
        self.settings = settings
        self.models = models
        self.lock = threading.RLock()
        path = Path(settings.database)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA secure_delete=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS requests (
              user_id TEXT NOT NULL, request_id TEXT NOT NULL, digest TEXT NOT NULL,
              expires_at REAL NOT NULL, PRIMARY KEY(user_id, request_id)
            );
            CREATE TABLE IF NOT EXISTS memories (
              id TEXT PRIMARY KEY, user_id TEXT NOT NULL, request_id TEXT NOT NULL,
              session_id TEXT NOT NULL, position INTEGER NOT NULL, offset INTEGER NOT NULL,
              role TEXT NOT NULL, timestamp INTEGER, source TEXT NOT NULL,
              facts TEXT NOT NULL, embedding BLOB, expires_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS memories_user ON memories(user_id);
            CREATE INDEX IF NOT EXISTS memories_expiry ON memories(expires_at);
            CREATE INDEX IF NOT EXISTS requests_expiry ON requests(expires_at);
        """)
        path.chmod(0o600)
        fingerprint = json.dumps({
            "schema": 1, "mode": settings.mode, "model": settings.embedding_model,
            "embedding_base_url": settings.embedding_base_url,
            "dimensions": settings.dimensions, "facts": settings.enable_facts,
            "llm": settings.llm_model, "llm_base_url": settings.llm_base_url,
            "chunk_chars": settings.chunk_chars,
        }, sort_keys=True)
        old = self.db.execute("SELECT value FROM metadata WHERE key='config'").fetchone()
        if old and old["value"] != fingerprint:
            self.db.close()
            raise ValueError("Index configuration changed; use a fresh database")
        self.db.execute("INSERT OR IGNORE INTO metadata VALUES ('config', ?)", (fingerprint,))
        self.purge()

    @contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def close(self):
        with self.lock:
            self.db.close()

    def purge(self, now=None):
        cutoff = time.time() if now is None else now
        with self.transaction():
            self.db.execute("DELETE FROM memories WHERE expires_at <= ?", (cutoff,))
            self.db.execute("DELETE FROM requests WHERE expires_at <= ?", (cutoff,))
        with self.lock:
            self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def _existing(self, request, digest):
        row = self.db.execute(
            "SELECT digest FROM requests WHERE user_id=? AND request_id=? AND expires_at>?",
            (request.user_id, request.request_id, time.time()),
        ).fetchone()
        if row and row["digest"] != digest:
            raise Conflict("request_id already belongs to a different payload")
        return row is not None

    def _capacity(self, user_id, incoming):
        count = self.db.execute(
            "SELECT COUNT(*) FROM memories WHERE user_id=? AND expires_at>?",
            (user_id, time.time()),
        ).fetchone()[0]
        if count + incoming > self.settings.max_chunks_per_user:
            raise Capacity("User capacity exceeded; increase capacity before evaluation")

    def add(self, request: AddRequest):
        digest = hashlib.sha256(
            request.model_dump_json().encode()
        ).hexdigest()
        with self.lock:
            if self._existing(request, digest):
                return
        records = []
        for position, message in enumerate(request.messages):
            for offset, text in chunks(message.content, self.settings.chunk_chars):
                records.append((position, offset, message, text))
        with self.lock:
            self._capacity(request.user_id, len(records))
        # No database acknowledgement is emitted until every index component is ready.
        facts = [self.models.extract(record[3]) for record in records]
        vectors = self.models.embed([record[3] for record in records])
        if len(vectors) != len(records):
            raise ValueError("Embedding count does not match source chunks")
        expires = time.time() + self.settings.retention_days * 86400
        with self.transaction():
            if self._existing(request, digest):
                return
            self._capacity(request.user_id, len(records))
            for (position, offset, message, text), fact, vector in zip(records, facts, vectors):
                identity = json.dumps(
                    [request.user_id, request.request_id, position, offset], ensure_ascii=False
                )
                memory_id = "mem_" + hashlib.sha256(identity.encode()).hexdigest()
                self.db.execute(
                    "INSERT OR REPLACE INTO memories VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (memory_id, request.user_id, request.request_id, request.session_id,
                     position, offset, message.role, message.timestamp, text, fact, vector, expires),
                )
            self.db.execute(
                "INSERT OR REPLACE INTO requests VALUES (?, ?, ?, ?)",
                (request.user_id, request.request_id, digest, expires),
            )

    def search(self, request: SearchRequest):
        with self.lock:
            rows = self.db.execute(
                "SELECT * FROM memories WHERE user_id=? AND expires_at>? ORDER BY id",
                (request.user_id, time.time()),
            ).fetchall()
        if not rows:
            return []
        query = request.query
        if request.options:
            query += "\n" + "\n".join(request.options)
        corpus = [tokens(row["source"] + "\n" + row["facts"]) for row in rows]
        query_tokens = tokens(query)
        scores = [0.0] * len(rows)
        if any(corpus) and query_tokens:
            lexical = BM25Okapi(corpus).get_scores(query_tokens)
            # BM25 can be negative in tiny corpora. Preserve matching candidates.
            query_set = set(query_tokens)
            matching = [i for i, terms in enumerate(corpus) if query_set.intersection(terms)]
            ranked = sorted(matching, key=lambda i: (-float(lexical[i]), rows[i]["id"]))
            for rank, index in enumerate(ranked[:300], 1):
                scores[index] += 1 / (60 + rank)
        if self.settings.mode == "hybrid":
            query_parts = [part for _, part in chunks(query, self.settings.chunk_chars)]
            vectors = self.models.embed(query_parts)
            query_vector = np.mean([np.frombuffer(v, dtype="<f4") for v in vectors], axis=0)
            norm = np.linalg.norm(query_vector)
            if norm > 0:
                query_vector /= norm
            # shortcut: exact scoped scan; measure long-history latency before a Full run.
            ranked = heapq.nlargest(
                min(300, len(rows)), range(len(rows)),
                key=lambda i: float(np.dot(
                    np.frombuffer(rows[i]["embedding"], dtype="<f4"), query_vector
                )),
            )
            for rank, index in enumerate(ranked, 1):
                scores[index] += 1 / (60 + rank)
        ranked = sorted(range(len(rows)), key=lambda i: (-scores[i], rows[i]["id"]))
        results = []
        seen = set()
        for index in ranked:
            if scores[index] <= 0:
                continue
            row = rows[index]
            identity = (row["session_id"], row["position"], row["timestamp"], row["source"])
            if identity in seen:
                continue
            seen.add(identity)
            source_time = (
                datetime.fromtimestamp(row["timestamp"] / 1000, UTC).isoformat()
                if row["timestamp"] is not None else None
            )
            metadata = json.dumps({
                "session": row["session_id"], "message": row["position"],
                "request_id": row["request_id"],
                "character_offset": row["offset"], "role": row["role"],
                "source_time": source_time,
            }, ensure_ascii=False)
            result = {
                "id": row["id"], "content": metadata + "\n" + row["source"],
                "score": scores[index],
            }
            if source_time is not None:
                result["created_at"] = source_time
            results.append(result)
            if len(results) >= request.top_k:
                break
        return results
