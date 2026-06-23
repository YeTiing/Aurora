from .chunker import ASTChunker, CodeChunk, SUPPORTED_LANGS
from .engine import BM25Index, VectorStore, Reranker, RAGEngine

rag_engine = RAGEngine()

def init_rag(persist_dir="./chroma_db") -> RAGEngine:
    global rag_engine
    rag_engine = RAGEngine(persist_dir)
    return rag_engine

__all__ = ["ASTChunker","CodeChunk","BM25Index","VectorStore","Reranker","RAGEngine","rag_engine","init_rag"]