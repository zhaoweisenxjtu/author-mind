"""向量索引: 基于 numpy + JSONL 的轻量级语义检索.

存储结构:
  ~/.astromind-praxis/vec_index/
    atoms/
      <author_name>/
        embeddings.npy     # float32 matrix (N, 1024)
        records.jsonl      # {id, atom_id, content_summary} per line

使用 aliyun text-embedding-v3 或兼容的 OpenAI API 生成 1024 维向量.
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

VEC_DIR = Path.home() / ".astromind-praxis" / "vec_index" / "atoms"


def _get_index_path(author_name: str) -> Path:
    p = VEC_DIR / author_name
    p.mkdir(parents=True, exist_ok=True)
    return p


def _load_index(author_name: str) -> tuple[list[dict], np.ndarray | None]:
    """Load records and embeddings for an author. Returns (records, embeddings_matrix)."""
    idx_path = _get_index_path(author_name)
    npy_path = idx_path / "embeddings.npy"
    jsonl_path = idx_path / "records.jsonl"

    records = []
    if jsonl_path.exists():
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))

    embeddings = None
    if npy_path.exists() and npy_path.stat().st_size > 0:
        embeddings = np.load(str(npy_path))

    return records, embeddings


def _save_index(author_name: str, records: list[dict], embeddings: np.ndarray):
    idx_path = _get_index_path(author_name)
    np.save(str(idx_path / "embeddings.npy"), embeddings)
    with open(idx_path / "records.jsonl", "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def cosine_similarity(vec: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    """Compute cosine similarity between a vector and a matrix of vectors."""
    vec_norm = vec / (np.linalg.norm(vec) + 1e-10)
    matrix_norm = matrix / (np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-10)
    return np.dot(matrix_norm, vec_norm)


class EmbeddingClient:
    """Embedding API client."""

    def __init__(self, api_base: str = None, api_key: str = None,
                 model: str = "text-embedding-3-small"):
        self.api_base = api_base or os.environ.get("OPENAI_API_BASE", "")
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.model = model

    def is_configured(self) -> bool:
        return bool(self.api_base and self.api_key)

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        """Generate embeddings for a list of texts."""
        if not self.is_configured():
            logger.warning("EmbeddingClient not configured")
            return None

        import httpx
        url = f"{self.api_base.rstrip('/')}/embeddings"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        embeddings = []
        # Process in batches of 20
        for i in range(0, len(texts), 20):
            batch = texts[i:i + 20]
            body = {"model": self.model, "input": batch}
            try:
                with httpx.Client(timeout=30) as client:
                    resp = client.post(url, json=body, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                    for item in data["data"]:
                        embeddings.append(item["embedding"])
                if i + 20 < len(texts):
                    time.sleep(0.1)  # Rate limiting
            except Exception as e:
                logger.error("Embedding API error: %s", e)
                return None
        return embeddings


class AtomVectorIndex:
    """Vector index for knowledge atoms."""

    def __init__(self, author_name: str, embedding_client: EmbeddingClient = None):
        self.author_name = author_name
        self.embedding_client = embedding_client or EmbeddingClient()
        self.records, self.embeddings = _load_index(author_name)

    @property
    def size(self) -> int:
        return len(self.records)

    def add(self, atom_id: int, content: str) -> bool:
        """Add a single atom to the index. Returns True if added."""
        # Check if already indexed
        if any(r.get("atom_id") == atom_id for r in self.records):
            return False

        emb_list = self.embedding_client.embed([content[:500]])
        if not emb_list:
            return False

        vec = np.array(emb_list[0], dtype=np.float32)
        self.records.append({
            "atom_id": atom_id,
            "content_summary": content[:200],
        })

        if self.embeddings is None:
            self.embeddings = vec.reshape(1, -1)
        else:
            self.embeddings = np.vstack([self.embeddings, vec.reshape(1, -1)])

        _save_index(self.author_name, self.records, self.embeddings)
        return True

    def search(self, content: str, top_k: int = 5,
               threshold: float = 0.0) -> list[dict]:
        """Search for similar atoms. Returns [{atom_id, similarity}, ...]."""
        if self.embeddings is None or len(self.records) == 0:
            return []

        emb_list = self.embedding_client.embed([content[:500]])
        if not emb_list:
            return []

        query_vec = np.array(emb_list[0], dtype=np.float32)
        sims = cosine_similarity(query_vec, self.embeddings)

        results = []
        for idx in np.argsort(-sims):
            if sims[idx] < threshold:
                break
            if len(results) >= top_k:
                break
            results.append({
                "atom_id": self.records[idx]["atom_id"],
                "similarity": float(sims[idx]),
                "content_summary": self.records[idx]["content_summary"],
            })

        return results

    def get_all_atom_ids(self) -> set[int]:
        return {r["atom_id"] for r in self.records}
