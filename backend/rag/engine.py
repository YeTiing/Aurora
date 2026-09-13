# RAG 向量存储 + BM25 检索 + 重排序
from __future__ import annotations
import re, math, os, hashlib, json
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from .chunker import CodeChunk
import logging
import numpy as np
logger = logging.getLogger("aurora")

# 持久化格式版本号：结构变更时递增，旧文件会被安全忽略（降级为空库）而不是让进程崩溃
_PERSIST_VERSION = 1
_EMB_FILE = "embeddings.npz"
_META_FILE = "chunks.json"

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
        # 已索引文件集合：与 RAGEngine._indexed 共享同一对象；load 时原地更新以保持引用有效
        self.indexed_files: set[str] = set()
        # B8：嵌入降级状态，供调用方观测，避免"混合检索"静默退化成纯 BM25
        self.embedding_degraded = False
        self.last_embedding_error: str | None = None
        self._embedding_warned: set[str] = set()
        # 启动即尝试恢复（文件不存在 = 首次运行，静默；损坏 = 告警后空库）
        self.load()

    # ── B8 嵌入降级可见性 ──

    def record_embedding_failure(self, exc: BaseException, where: str):
        """记录一次嵌入失败并暴露降级状态。

        同一失败原因只在首次告警，避免在 search 这种热路径上反复刷屏。
        """
        msg = f"{type(exc).__name__}: {exc}"
        self.last_embedding_error = msg
        self.embedding_degraded = True
        key = f"{where}|{msg}"
        if key not in self._embedding_warned:
            self._embedding_warned.add(key)
            logger.warning(
                "RAG 嵌入失败(%s)：%s；本次及后续检索已退化为纯 BM25 关键词检索，"
                "向量召回不可用。如需恢复，请在 rag.embedding_provider/base_url/api_key/model "
                "配置可用的嵌入服务。", where, msg)

    def clear_embedding_failure(self):
        """嵌入成功后解除降级标记。"""
        self.embedding_degraded = False
        self.last_embedding_error = None

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
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        async def _embed_batches():
            for batch_start in range(0, len(unembedded), batch_size):
                batch = unembedded[batch_start:batch_start+batch_size]
                try:
                    vecs = await llm_client.embeddings(batch)
                    for v in vecs:
                        self._embeddings.append(v)
                        if not self._dim: self._dim = len(v)
                    self.clear_embedding_failure()
                except Exception as e:
                    # 不再静默 break：记录原因 + 暴露降级状态，并落盘已成功的部分
                    self.record_embedding_failure(e, "embed_all")
                    break
            # 嵌入后立即持久化，否则重启又退化为纯 BM25
            self.save()

        if loop is not None:
            import asyncio
            try:
                asyncio.ensure_future(_embed_batches())
            except Exception as e:
                self.record_embedding_failure(e, "embed_all_schedule")
        else:
            asyncio.run(_embed_batches())

    def search(self, query_vec: list[float], top_k=20) -> list[tuple[int,float]]:
        """numpy 向量化余弦相似度检索。

        语义与旧的双重循环实现逐项一致：逐行跳过维度不匹配的向量，
        sim = dot / max(|a|*|b|, 0.0001)，当 |a| 或 |b| 为 0 时取 0。
        使用 stable 排序，保证同分时与旧版 list.sort 一样按下标升序。
        """
        if not self._embeddings or not query_vec: return []
        qdim = len(query_vec)
        valid = [i for i, e in enumerate(self._embeddings) if len(e) == qdim]
        if not valid: return []
        mat = np.asarray([self._embeddings[i] for i in valid], dtype=np.float64)
        q = np.asarray(query_vec, dtype=np.float64)
        dots = mat @ q
        na = np.sqrt(np.einsum("ij,ij->i", mat, mat))
        nb = float(np.sqrt(np.dot(q, q)))
        denom = np.maximum(na * nb, 0.0001)
        sims = np.where((na > 0) & (nb > 0), dots / denom, 0.0)
        order = np.argsort(-sims, kind="stable")[:top_k]
        return [(valid[int(j)], float(sims[int(j)])) for j in order]

    def count(self): return len(self._chunks)

    def get_chunks(self, indices: list[int]) -> list[CodeChunk]:
        return [self._chunks[i] for i in indices if i < len(self._chunks)]

    # ── B9 持久化 ──
    # 文件布局（persist_dir 下）：
    #   chunks.json     — 版本、维度、CodeChunk 元数据、已索引文件列表（JSON，便于人工排查）
    #   embeddings.npz  — 单一 float64 矩阵，第 i 行对应 _chunks[i]（存在部分嵌入时行数 <= chunk 数）
    # 写入采用 tmp + os.replace 原子替换，避免进程中断留下半个文件。

    def _paths(self) -> tuple[Path, Path]:
        base = Path(self.persist_dir)
        return base / _EMB_FILE, base / _META_FILE

    def _reset_empty(self):
        # 原地清空，保持与 BM25 / RAGEngine 共享的容器引用有效
        self._chunks.clear()
        self._embeddings.clear()
        self._dim = 0
        self.indexed_files.clear()

    def save(self):
        """持久化 chunk 元数据与向量；任何失败只告警不抛出，绝不影响主流程。"""
        if not self.persist_dir:
            return
        # 索引对齐保护：嵌入数绝不能超过 chunk 数，否则下标错位会静默返回错误结果
        if len(self._embeddings) > len(self._chunks):
            logger.warning(
                "RAG 持久化中止：嵌入数(%d) 超过 chunk 数(%d)，拒绝写入错位索引。",
                len(self._embeddings), len(self._chunks))
            return
        emb_path, meta_path = self._paths()
        try:
            Path(self.persist_dir).mkdir(parents=True, exist_ok=True)
            meta = {
                "version": _PERSIST_VERSION,
                "dim": self._dim,
                "chunks": [asdict(c) for c in self._chunks],
                "indexed": sorted(self.indexed_files),
            }
            vectors = (np.asarray(self._embeddings, dtype=np.float64)
                       if self._embeddings else np.zeros((0, 0), dtype=np.float64))
            emb_tmp = emb_path.with_name(emb_path.name + ".tmp")
            meta_tmp = meta_path.with_name(meta_path.name + ".tmp")
            # 传入文件句柄，避免 np.savez 自动追加 .npz 扩展名
            with open(emb_tmp, "wb") as f:
                np.savez_compressed(f, vectors=vectors)
            meta_tmp.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
            os.replace(emb_tmp, emb_path)
            os.replace(meta_tmp, meta_path)
        except Exception as e:
            logger.warning("RAG 持久化失败(%s)：%s", self.persist_dir, e, exc_info=True)

    def load(self) -> bool:
        """从磁盘恢复；缺失=首次运行(静默)，损坏/版本不符=告警并降级为空库。"""
        if not self.persist_dir:
            return False
        emb_path, meta_path = self._paths()
        if not emb_path.exists() or not meta_path.exists():
            return False
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            with np.load(emb_path, allow_pickle=False) as data:
                vectors = data["vectors"]
        except Exception as e:
            logger.warning("RAG 持久化文件损坏，已忽略并以空库启动：%s", e)
            self._reset_empty()
            return False
        if not isinstance(meta, dict) or meta.get("version") != _PERSIST_VERSION:
            logger.warning("RAG 持久化版本不匹配(期望 %s)，已忽略并以空库启动。", _PERSIST_VERSION)
            self._reset_empty()
            return False
        try:
            chunks = [CodeChunk(**d) for d in meta.get("chunks", [])]
            indexed = set(meta.get("indexed", []))
        except Exception as e:
            logger.warning("RAG chunk 元数据解析失败，已忽略并以空库启动：%s", e)
            self._reset_empty()
            return False
        # 对齐校验：向量行数不得超过 chunk 数
        if getattr(vectors, "ndim", 0) != 2 or vectors.shape[0] > len(chunks):
            logger.warning(
                "RAG 持久化索引错位(向量 %s 行 / chunk %d 个)，已忽略并以空库启动。",
                getattr(vectors, "shape", "?"), len(chunks))
            self._reset_empty()
            return False
        dim = int(vectors.shape[1]) if vectors.size else 0
        if dim and meta.get("dim") not in (0, None, dim):
            logger.warning("RAG 持久化维度不一致(meta=%s / 向量=%s)，已忽略并以空库启动。",
                           meta.get("dim"), dim)
            self._reset_empty()
            return False
        # 原地更新，保证外部（BM25Index.chunks、RAGEngine._indexed）持有的引用继续有效
        self._chunks[:] = chunks
        self._embeddings[:] = [list(map(float, row)) for row in vectors]
        self._dim = dim
        self.indexed_files.clear()
        self.indexed_files.update(indexed)
        return True


# ── 重排序 ──
class Reranker:
    def __init__(self):
        self._model = None

    def _ensure(self):
        if self._model is None:
            try:
                from sentence_transformers import CrossEncoder
                self._model = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
            except Exception: self._model = False


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
        # 与 vector_store.indexed_files 共享同一集合对象（load 原地更新，引用不失效）
        self._indexed: set[str] = self.vector_store.indexed_files
        self._embedding_client = None
        # 从磁盘恢复后必须重建 BM25，否则重启后关键词检索同样是空的
        if self.vector_store.count():
            self.bm25.index(self.vector_store._chunks)

    def _get_chunker(self):
        if self.chunker is None:
            from .chunker import ASTChunker
            self.chunker = ASTChunker()
        return self.chunker

    # ── B8：嵌入降级状态对外暴露 ──

    @property
    def embedding_degraded(self) -> bool:
        return self.vector_store.embedding_degraded

    @property
    def last_embedding_error(self) -> str | None:
        return self.vector_store.last_embedding_error

    def save(self):
        """持久化当前索引（chunk + 向量 + 已索引文件）。"""
        self.vector_store.save()

    def _resolve_embedding_client(self, default_client):
        """B8：嵌入可使用独立于 chat provider 的来源。

        返回 (client, model)：配置了 rag.embedding_* 时使用独立 LLMClient；
        否则沿用调用方传入的 client，行为与旧版一致。
        """
        try:
            from backend.config import config as _cfg
        except Exception:
            _cfg = None
        if _cfg is not None:
            try:
                if _cfg.has_embedding_override():
                    if self._embedding_client is None:
                        from backend.agent.llm_client import LLMClient, LLMConfig
                        self._embedding_client = LLMClient(LLMConfig(
                            provider=_cfg.embedding_provider,
                            model=_cfg.embedding_model,
                            api_key=_cfg.embedding_api_key,
                            base_url=_cfg.embedding_base_url,
                        ))
                    return self._embedding_client, _cfg.embedding_model
            except Exception as e:
                self.vector_store.record_embedding_failure(e, "embedding_config")
        return default_client, None

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
                except Exception:
                    # 单文件解析失败应可见（不是热路径），但不中断整体索引
                    logger.warning("RAG 索引文件失败，已跳过：%s", fp, exc_info=True)
        if all_chunks:
            self.vector_store.add(all_chunks)
            self.bm25.index(self.vector_store._chunks)
            # 索引后落盘，重启后 chunk 与文件索引不再丢失
            self.vector_store.save()

    def search(self, query: str, top_k=5, llm_client=None) -> list[CodeChunk]:
        if self.vector_store.count() == 0: return []

        # 向量检索
        vec_results = []
        embed_client, embed_model = self._resolve_embedding_client(llm_client)
        if embed_client and query:
            try:
                import asyncio, concurrent.futures
                async def _embed():
                    if embed_model:
                        return await embed_client.embeddings([query], model=embed_model)
                    return await embed_client.embeddings([query])
                try:
                    loop = asyncio.get_running_loop()
                    # In async context: use thread pool to avoid nesting
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                        fut = pool.submit(asyncio.run, _embed())
                        qvec = fut.result(timeout=15)
                except RuntimeError:
                    # No running loop: safe direct call
                    qvec = asyncio.run(_embed())
                if qvec and qvec[0]:
                    vec_results = self.vector_store.search(qvec[0], 20)
                    self.vector_store.clear_embedding_failure()
            except Exception as e:
                # B8：不再 logger.debug 静默吞掉 —— 记录并告警（同原因仅一次），检索继续走 BM25
                self.vector_store.record_embedding_failure(e, "search")


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
        return {
            "indexed_files": len(self._indexed),
            "total_chunks": self.vector_store.count(),
            "vector_count": len(self.vector_store._embeddings),
            "embedding_degraded": self.vector_store.embedding_degraded,
            "last_embedding_error": self.vector_store.last_embedding_error,
        }
