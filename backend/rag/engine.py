# RAG 向量存储 + BM25 检索 + 重排序
from __future__ import annotations
import re, math, os, hashlib, json
from collections import defaultdict
from pathlib import Path
from .chunker import CodeChunk

# ── BM25 ──
class BM25Index:
    def __init__(self, k1=1.5, b=0.75):
        self.k1, self.b = k1, b
        self.chunks: list[CodeChunk] = []
        self.doc_freqs: dict[str,int] = {}
        self.doc_lengths: list[int] = []
        self.avgdl = 0

    @staticmethod
    def tokenize(text: str) -> list[str]:
        return re.findall(r'[a-zA-Z_]\w*|[^\s\w]', text.lower())

    def index(self, chunks: list[CodeChunk]):
        self.chunks = chunks
        self.doc_lengths = [len(self.tokenize(c.content)) for c in chunks]
        self.avgdl = sum(self.doc_lengths)/max(len(chunks),1)
        self.doc_freqs.clear()
        for c in chunks:
            seen = set()
            for t in self.tokenize(c.content):
                if t not in seen:
                    self.doc_freqs[t] = self.doc_freqs.get(t,0)+1
                    seen.add(t)

    def search(self, query: str, top_k=20) -> list[tuple[int,float]]:
        qtokens = self.tokenize(query)
        scores = []
        for idx, c in enumerate(self.chunks):
            doctoks = self.tokenize(c.content)
            tf = defaultdict(int)
            for t in doctoks: tf[t] += 1
            dl = self.doc_lengths[idx]
            score = 0.0
            for t in qtokens:
                if t in tf:
                    df = self.doc_freqs.get(t,1)
                    idf = math.log((len(self.chunks)-df+0.5)/(df+0.5)+1)
                    num = tf[t]*(self.k1+1)
                    den = tf[t]+self.k1*(1-self.b+self.b*dl/max(self.avgdl,1))
                    score += idf*num/max(den,0.001)
            if score > 0: scores.append((idx,score))
        scores.sort(key=lambda x:-x[1])
        return scores[:top_k]


# ── 向量存储 ──
class VectorStore:
    def __init__(self, persist_dir="./chroma_db"):
        self.persist_dir = persist_dir
        self._chunks: list[CodeChunk] = []
        self._embeddings: list[list[float]] = []
        self._dim = 0

    def add(self, chunks: list[CodeChunk]):
        self._chunks.extend(chunks)

    def embed_all(self, llm_client, batch_size=50):
        """批量嵌入所有未嵌入的 chunk"""
        # 生成内容哈希跳过重复
        unembedded = []
        indices = []
        for i, c in enumerate(self._chunks):
            if i >= len(self._embeddings):
                unembedded.append(c.content[:4000])
                indices.append(i)
        if not unembedded or not llm_client: return
        import asyncio
        for batch_start in range(0, len(unembedded), batch_size):
            batch = unembedded[batch_start:batch_start+batch_size]
            try:
                vecs = asyncio.run(llm_client.embeddings(batch))
                for v in vecs:
                    self._embeddings.append(v)
                    if not self._dim: self._dim = len(v)
            except Exception: break

    def search(self, query_vec: list[float], top_k=20) -> list[tuple[int,float]]:
        if not self._embeddings: return []
        scores = []
        for i, emb in enumerate(self._embeddings):
            if len(emb) != len(query_vec): continue
            dot = sum(a*b for a,b in zip(emb, query_vec))
            na = math.sqrt(sum(a*a for a in emb))
            nb = math.sqrt(sum(b*b for b in query_vec))
            sim = dot/max(na*nb, 0.0001) if na>0 and nb>0 else 0
            scores.append((i, sim))
        scores.sort(key=lambda x:-x[1])
        return scores[:top_k]

    def count(self): return len(self._chunks)

    def get_chunks(self, indices: list[int]) -> list[CodeChunk]:
        return [self._chunks[i] for i in indices if i < len(self._chunks)]


# ── 重排序 ──
class Reranker:
    def __init__(self):
        self._model = None

    def _ensure(self):
        if self._model is None:
            try:
                from sentence_transformers import CrossEncoder
                self._model = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
            except: self._model = False

    def rerank(self, query: str, chunks: list[CodeChunk], top_k=5) -> list[CodeChunk]:
        if len(chunks) <= top_k: return chunks
        self._ensure()
        if self._model:
            pairs = [[query, c.content[:2000]] for c in chunks]
            scores = self._model.predict(pairs)
            scored = sorted(zip(chunks, scores), key=lambda x:-x[1])
            return [c for c,_ in scored[:top_k]]
        tokens = set(re.findall(r'\w+', query.lower()))
        scored = [(c, sum(1 for t in tokens if t in c.content.lower())) for c in chunks]
        scored.sort(key=lambda x:-x[1])
        return [c for c,_ in scored[:top_k]]


# ── RAG 引擎 ──
class RAGEngine:
    def __init__(self, persist_dir="./chroma_db"):
        self.vector_store = VectorStore(persist_dir)
        self.bm25 = BM25Index()
        self.reranker = Reranker()
        self.chunker = None
        self._indexed: set[str] = set()

    def _get_chunker(self):
        if self.chunker is None:
            from .chunker import ASTChunker
            self.chunker = ASTChunker()
        return self.chunker

    def index_project(self, root: str|Path):
        root = Path(root)
        patterns = ["**/*.py","**/*.ts","**/*.tsx","**/*.js","**/*.jsx","**/*.go","**/*.rs"]
        all_chunks = []
        chunker = self._get_chunker()
        for pat in patterns:
            for fp in root.glob(pat):
                if str(fp) in self._indexed: continue
                try:
                    chunks = chunker.chunk_file(fp)
                    all_chunks.extend(chunks)
                    self._indexed.add(str(fp))
                except: pass
        if all_chunks:
            self.vector_store.add(all_chunks)
            self.bm25.index(self.vector_store._chunks)

    def search(self, query: str, top_k=5, llm_client=None) -> list[CodeChunk]:
        if self.vector_store.count() == 0: return []

        # 向量检索
        vec_results = []
        if llm_client and query:
            try:
                import asyncio
                qvec = asyncio.run(llm_client.embeddings([query]))
                if qvec and qvec[0]:
                    vec_results = self.vector_store.search(qvec[0], 20)
            except: pass

        # BM25
        bm25_results = self.bm25.search(query, 20)

        # RRF 融合
        rrf_scores: dict[int,float] = {}
        k = 60
        for rank, (idx,_) in enumerate(vec_results):
            rrf_scores[idx] = rrf_scores.get(idx,0) + 1/(k+rank+1)
        for rank, (idx,_) in enumerate(bm25_results):
            rrf_scores[idx] = rrf_scores.get(idx,0) + 1/(k+rank+1)

        fused = sorted(rrf_scores.items(), key=lambda x:-x[1])[:10]
        candidates = self.vector_store.get_chunks([idx for idx,_ in fused])

        return self.reranker.rerank(query, candidates, top_k)

    def format_context(self, chunks: list[CodeChunk]) -> str:
        lines = []
        for c in chunks:
            lines.append(f"// {c.file_path}:{c.start_line}-{c.end_line} ({c.chunk_type})")
            lines.append(c.content[:3000])
            lines.append("---")
        return "\n".join(lines)

    def stats(self) -> dict:
        return {"indexed_files": len(self._indexed), "total_chunks": self.vector_store.count()}