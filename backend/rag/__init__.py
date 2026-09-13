from .chunker import ASTChunker, CodeChunk, SUPPORTED_LANGS
from .engine import BM25Index, VectorStore, Reranker, RAGEngine

def _default_persist_dir() -> str:
    # 持久化目录可通过 rag.persist_dir 配置覆盖；配置不可用时退回原默认值。
    try:
        from backend.config import config as _cfg
        if _cfg is not None:
            return _cfg.rag_persist_dir
    except Exception:
        pass
    return "./chroma_db"


rag_engine = RAGEngine(_default_persist_dir())

def init_rag(persist_dir=None) -> RAGEngine:
    global rag_engine
    rag_engine = RAGEngine(persist_dir or _default_persist_dir())
    return rag_engine

__all__ = ["ASTChunker","CodeChunk","BM25Index","VectorStore","Reranker","RAGEngine","rag_engine","init_rag"]