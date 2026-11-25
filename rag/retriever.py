"""Vector search over the RAG documents + join-path expansion over foreign keys."""
from collections import deque
from dataclasses import dataclass

import numpy as np
import psycopg

import config
from core.llm import Gemini


@dataclass
class Doc:
    kind: str     # 'table' | 'glossary' | 'example'
    key: str      # table name, glossary term, or example question
    content: str  # text shown to the model
    meta: dict


@dataclass
class Retrieved:
    tables: list[tuple[Doc, float]]
    join_tables: list[str]          # added by BFS to connect the retrieved tables
    glossary: list[tuple[Doc, float]]
    examples: list[tuple[Doc, float]]


class Retriever:
    def __init__(self, llm: Gemini):
        self.llm = llm
        with psycopg.connect(**config.pg_params("app")) as conn:
            rows = conn.execute(
                "SELECT kind, key, content, meta, embedding FROM rag.documents ORDER BY doc_id"
            ).fetchall()
        if not rows:
            raise RuntimeError("RAG index is empty. Run: python -m rag.build_index")
        self.docs = [Doc(k, key, content, meta) for k, key, content, meta, _ in rows]
        matrix = np.array([r[4] for r in rows], dtype=np.float32)
        self.matrix = matrix / np.linalg.norm(matrix, axis=1, keepdims=True)  # unit vectors
        self.kinds = np.array([d.kind for d in self.docs])

        # Undirected foreign-key graph: table -> neighbouring tables
        self.graph: dict[str, set[str]] = {t: set() for t in config.STORE_TABLES}
        for d in self.docs:
            if d.kind == "table":
                for ref in d.meta.get("fk_tables", []):
                    if ref != d.key:  # skip self-references (employees.manager_id)
                        self.graph[d.key].add(ref)
                        self.graph[ref].add(d.key)

    def table_doc(self, name: str) -> Doc:
        return next(d for d in self.docs if d.kind == "table" and d.key == name)

    def all_tables_text(self) -> str:
        return "\n\n".join(d.content for d in self.docs if d.kind == "table")

    # ------------------------------------------------------------------ search
    def _top_k(self, query_vec: np.ndarray, kind: str, k: int) -> list[tuple[Doc, float]]:
        idx = np.where(self.kinds == kind)[0]
        scores = self.matrix[idx] @ query_vec          # cosine similarity (unit vectors)
        best = idx[np.argsort(-scores)[:k]]
        return [(self.docs[i], float(self.matrix[i] @ query_vec)) for i in best]

    def retrieve(self, question: str, k_tables: int = 4, k_glossary: int = 3,
                 k_examples: int = 3) -> Retrieved:
        q = np.array(self.llm.embed([question], "RETRIEVAL_QUERY")[0], dtype=np.float32)
        q /= np.linalg.norm(q)
        tables = self._top_k(q, "table", k_tables)
        examples = self._top_k(q, "example", k_examples)
        found = {d.key for d, _ in tables}
        # Extra tables: (1) named literally in the question, (2) used by the
        # best-matching example's SQL. Embeddings alone often miss "orders"
        # for profit questions, but the matching example query uses it.
        lowered = question.lower()
        extra = [n for n in config.STORE_TABLES if n.replace("_", " ") in lowered]
        if examples and examples[0][1] >= 0.6:
            extra += examples[0][0].meta.get("tables", [])
        for name in extra:
            if name not in found:
                found.add(name)
                tables.append((self.table_doc(name), 0.0))  # 0.0 = added by rule, not by score
        names = [d.key for d, _ in tables]
        return Retrieved(
            tables=tables,
            join_tables=[t for t in self.connect(names) if t not in names],
            glossary=self._top_k(q, "glossary", k_glossary),
            examples=examples,
        )

    # --------------------------------------------------------- join-path (BFS)
    def shortest_path(self, start: str, goal: str) -> list[str]:
        """Breadth-first search: fewest joins from start to goal."""
        parent = {start: None}
        queue = deque([start])
        while queue:
            node = queue.popleft()
            if node == goal:
                path = []
                while node is not None:
                    path.append(node)
                    node = parent[node]
                return path[::-1]
            for nxt in sorted(self.graph[node]):
                if nxt not in parent:
                    parent[nxt] = node
                    queue.append(nxt)
        return []

    def connect(self, tables: list[str]) -> list[str]:
        """Grow a connected set: attach each table by its shortest path to the set.

        A greedy approximation of the Steiner tree problem, which is NP-hard in
        general but trivial at 10 tables.
        """
        if not tables:
            return []
        connected = [tables[0]]
        for t in tables[1:]:
            if t in connected:
                continue
            best = min((self.shortest_path(c, t) for c in connected),
                       key=lambda p: len(p) if p else 99)
            for node in best:
                if node not in connected:
                    connected.append(node)
        return connected
