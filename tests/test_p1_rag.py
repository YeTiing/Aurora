"""P1 RAG 修复回归测试 — B8 嵌入降级可见性 / B9 向量持久化 + numpy 检索。

全部离线：不联网、不加载模型（Reranker 的 CrossEncoder 缺失时自动走词重叠兜底）。
"""
import io
import json
import logging
import math
import os
from pathlib import Path

import numpy as np
import pytest

from backend.rag.chunker import CodeChunk
from backend.rag.engine import VectorStore, RAGEngine, _PERSIST_VERSION


def _chunk(cid, content, fpath="a.py", start=1):
    return CodeChunk(
        id=cid, content=content, file_path=fpath, start_line=start, end_line=start + 1,
        chunk_type="block", signature="", language="python",
    )


def _cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / max(na * nb, 0.0001) if na > 0 and nb > 0 else 0.0


# ── B9：numpy 相似度与参考实现一致 ──

def test_numpy_similarity_matches_reference_ranking_and_scores():
    store = VectorStore(persist_dir="")
    store._chunks = [_chunk("c0", "x"), _chunk("c1", "y"), _chunk("c2", "z")]
    store._embeddings = [
        [1.0, 0.0, 0.0],
        [0.5, 0.5, 0.0],
        [0.0, 1.0, 0.0],
    ]
    store._dim = 3
    query = [1.0, 0.2, 0.0]

    # 参考实现：逐项余弦，按分数降序、同分按下标升序（stable）
    expected = sorted(
        ((i, _cosine(e, query)) for i, e in enumerate(store._embeddings)),
        key=lambda x: (-x[1], x[0]),
    )
    got = store.search(query, top_k=3)

    assert [i for i, _ in got] == [i for i, _ in expected]
    for (gi, gs), (_, es) in zip(got, expected):
        assert gs == pytest.approx(es, abs=1e-12)


def test_numpy_similarity_hand_computed_top_hit():
    store = VectorStore(persist_dir="")
    store._chunks = [_chunk("c0", "a"), _chunk("c1", "b")]
    store._embeddings = [[3.0, 4.0], [1.0, 0.0]]
    query = [1.0, 0.0]
    got = store.search(query, top_k=2)
    # 与 [1,0] 余弦：向量0 (3,4)->3/5=0.6；向量1 (1,0)->1.0
    assert got[0] == (1, pytest.approx(1.0))
    assert got[1][0] == 0
    assert got[1][1] == pytest.approx(0.6)


def test_numpy_search_skips_dim_mismatch_like_old_loop():
    store = VectorStore(persist_dir="")
    store._chunks = [_chunk("c0", "a"), _chunk("c1", "b")]
    # 旧实现会跳过维度不匹配的向量（不报错、也不返回）
    store._embeddings = [[1.0, 0.0], [1.0, 0.0, 0.0]]
    got = store.search([1.0, 0.0], top_k=5)
    assert got == [(0, pytest.approx(1.0))]


def test_numpy_search_zero_vector_scores_zero():
    store = VectorStore(persist_dir="")
    store._chunks = [_chunk("c0", "a")]
    store._embeddings = [[0.0, 0.0]]
    assert store.search([1.0, 1.0]) == [(0, 0.0)]


def test_numpy_search_empty_embeddings_returns_empty():
    store = VectorStore(persist_dir="")
    store.add([_chunk("c0", "a")])
    assert store.search([1.0, 0.0]) == []


# ── B9：持久化 round-trip ──

def test_persistence_round_trip_new_instance(tmp_path):
    persist = str(tmp_path / "idx")
    s1 = VectorStore(persist)
    s1.add([_chunk("c0", "alpha beta", "a.py"), _chunk("c1", "gamma delta", "b.py")])
    s1._embeddings = [[1.0, 0.0], [0.0, 1.0]]
    s1._dim = 2
    s1.indexed_files.add("a.py")
    s1.indexed_files.add("b.py")
    s1.save()

    # 全新实例：从磁盘恢复
    s2 = VectorStore(persist)
    assert s2.count() == 2
    assert s2._dim == 2
    assert s2.indexed_files == {"a.py", "b.py"}
    assert s2._chunks[1].content == "gamma delta"
    np.testing.assert_allclose(s2._embeddings, [[1.0, 0.0], [0.0, 1.0]])

    # 重启后同一查询应返回同一 top hit
    before = s1.search([0.9, 0.1], top_k=2)
    after = s2.search([0.9, 0.1], top_k=2)
    assert after == before
    assert after[0][0] == 0


def test_engine_restart_rebuilds_bm25_and_stats(tmp_path):
    persist = str(tmp_path / "idx")
    e1 = RAGEngine(persist)
    e1.vector_store.add([_chunk("c0", "unique_keyword_orchid", "a.py")])
    e1.bm25.index(e1.vector_store._chunks)
    e1._indexed.add("a.py")
    e1.save()

    e2 = RAGEngine(persist)
    assert e2.vector_store.count() == 1
    # BM25 也必须重建，否则重启后关键词检索为空
    hits = e2.search("unique_keyword_orchid", top_k=3)
    assert hits and hits[0].id == "c0"
    stats = e2.stats()
    assert stats["total_chunks"] == 1
    assert stats["indexed_files"] == 1
    assert stats["embedding_degraded"] is False


def test_partial_embeddings_stay_index_aligned(tmp_path):
    persist = str(tmp_path / "idx")
    s1 = VectorStore(persist)
    s1.add([_chunk("c0", "a"), _chunk("c1", "b"), _chunk("c2", "c")])
    s1._embeddings = [[1.0, 0.0], [0.0, 1.0]]  # 只有前两个已嵌入
    s1._dim = 2
    s1.save()

    s2 = VectorStore(persist)
    assert s2.count() == 3
    assert len(s2._embeddings) == 2
    # 第 i 行必须仍对应第 i 个 chunk
    assert s2.search([1.0, 0.0], top_k=1)[0][0] == 0
    assert s2.search([0.0, 1.0], top_k=1)[0][0] == 1


def test_save_refuses_misaligned_index(tmp_path, caplog):
    persist = str(tmp_path / "idx")
    s = VectorStore(persist)
    s.add([_chunk("c0", "a")])
    s._embeddings = [[1.0], [2.0]]  # 嵌入数 > chunk 数：错位，拒绝写入
    with caplog.at_level(logging.WARNING, logger="aurora"):
        s.save()
    assert any("错位" in r.message or "超过" in r.message for r in caplog.records)
    assert not (tmp_path / "idx" / "chunks.json").exists()


# ── B9：损坏文件安全降级 ──

def test_corrupt_persist_files_load_as_empty_with_warning(tmp_path, caplog):
    persist = tmp_path / "idx"
    persist.mkdir(parents=True)
    (persist / "chunks.json").write_bytes(b"\x00\x01not-json")
    (persist / "embeddings.npz").write_bytes(b"garbage-not-npz")
    with caplog.at_level(logging.WARNING, logger="aurora"):
        s = VectorStore(str(persist))
    assert s.count() == 0
    assert s._embeddings == []
    assert any("损坏" in r.message for r in caplog.records)


def test_version_mismatch_loads_as_empty_with_warning(tmp_path, caplog):
    persist = tmp_path / "idx"
    persist.mkdir(parents=True)
    (persist / "chunks.json").write_text(
        json.dumps({"version": _PERSIST_VERSION + 99, "dim": 0, "chunks": [], "indexed": []}),
        encoding="utf-8",
    )
    # 写一个合法的 npz，确保失败原因只可能是版本
    with open(persist / "embeddings.npz", "wb") as f:
        np.savez_compressed(f, vectors=np.zeros((0, 0)))
    with caplog.at_level(logging.WARNING, logger="aurora"):
        s = VectorStore(str(persist))
    assert s.count() == 0
    assert any("版本" in r.message for r in caplog.records)


def test_missing_persist_files_is_silent_first_run(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="aurora"):
        s = VectorStore(str(tmp_path / "does_not_exist"))
    assert s.count() == 0
    assert caplog.records == []


# ── B8：嵌入降级可见性 ──

class _RaisingEmbedClient:
    """模拟 DeepSeek 这类没有 embeddings 端点的 provider。"""
    def __init__(self):
        self.calls = 0

    async def embeddings(self, texts, model="text-embedding-3-small"):
        self.calls += 1
        raise RuntimeError("404 embeddings endpoint not found")


def _engine_with_one_chunk(tmp_path):
    e = RAGEngine(str(tmp_path / "idx"))
    e.vector_store.add([_chunk("c0", "fastapi route handler", "server.py")])
    e.bm25.index(e.vector_store._chunks)
    return e


def test_b8_search_falls_back_to_bm25_and_flags_degradation(tmp_path, caplog):
    e = _engine_with_one_chunk(tmp_path)
    client = _RaisingEmbedClient()

    with caplog.at_level(logging.WARNING, logger="aurora"):
        hits = e.search("fastapi", top_k=3, llm_client=client)

    # (a) 仍然通过 BM25 返回结果
    assert [c.id for c in hits] == ["c0"]
    # (b) 降级可观测：flag + 错误原因 + 警告日志
    assert e.embedding_degraded is True
    assert "RuntimeError" in e.last_embedding_error
    assert e.stats()["embedding_degraded"] is True
    assert any("退化为纯 BM25" in r.message for r in caplog.records)


def test_b8_warning_is_rate_limited_per_cause(tmp_path, caplog):
    e = _engine_with_one_chunk(tmp_path)
    client = _RaisingEmbedClient()

    with caplog.at_level(logging.WARNING, logger="aurora"):
        e.search("fastapi", llm_client=client)
        e.search("fastapi", llm_client=client)
        e.search("route", llm_client=client)

    # 同一失败原因只告警一次，避免热路径刷屏
    warned = [r for r in caplog.records if "退化为纯 BM25" in r.message]
    assert len(warned) == 1
    assert client.calls == 3  # 每次仍会尝试，只是不重复告警


def test_b8_embed_all_does_not_silently_lose_index(tmp_path, caplog):
    e = _engine_with_one_chunk(tmp_path)
    client = _RaisingEmbedClient()

    with caplog.at_level(logging.WARNING, logger="aurora"):
        e.vector_store.embed_all(client)

    assert e.vector_store.count() == 1  # chunk 未丢失
    assert e.vector_store.embedding_degraded is True
    assert any("embed_all" in r.message for r in caplog.records)
    # 部分失败的索引仍会落盘（chunk 保留），重启后 count 不为 0
    e2 = RAGEngine(str(tmp_path / "idx"))
    assert e2.vector_store.count() == 1


def test_b8_success_clears_degradation(tmp_path):
    e = _engine_with_one_chunk(tmp_path)
    # 先制造降级
    e.vector_store.record_embedding_failure(RuntimeError("boom"), "test")
    assert e.embedding_degraded is True
    # 让向量库与 chunk 对齐，随后一次成功的嵌入检索应解除降级
    e.vector_store._embeddings = [[1.0, 0.0]]
    e.vector_store._dim = 2

    class _OkClient:
        async def embeddings(self, texts, model="text-embedding-3-small"):
            return [[1.0, 0.0] for _ in texts]

    hits = e.search("fastapi", top_k=3, llm_client=_OkClient())
    assert hits  # 结果正常返回
    assert e.embedding_degraded is False
    assert e.last_embedding_error is None


# ── B8：独立嵌入配置 ──

def test_embedding_config_overrides_chat_provider(tmp_path):
    from backend.config import Config
    (tmp_path / "aurora.json").write_text(json.dumps({
        "llm": {"provider": "deepseek", "base_url": "https://api.deepseek.com",
                "api_key": "chat-key", "model": "deepseek-v4-flash"},
        "rag": {
            "embedding_provider": "openai",
            "embedding_base_url": "https://api.openai.com/v1",
            "embedding_api_key": "embed-key",
            "embedding_model": "text-embedding-3-small",
        },
    }), encoding="utf-8")
    cfg = Config(tmp_path)
    assert cfg.has_embedding_override() is True
    assert cfg.embedding_provider == "openai"
    assert cfg.embedding_base_url == "https://api.openai.com/v1"
    assert cfg.embedding_api_key == "embed-key"
    assert cfg.embedding_model == "text-embedding-3-small"
    # chat provider 不受影响
    assert cfg.get("llm.provider") == "deepseek"
    assert cfg.llm_base_url == "https://api.deepseek.com"


def test_embedding_config_falls_back_to_chat_provider(tmp_path):
    from backend.config import Config
    (tmp_path / "aurora.json").write_text(json.dumps({
        "llm": {"provider": "openai", "base_url": "https://api.openai.com/v1",
                "api_key": "chat-key"},
    }), encoding="utf-8")
    cfg = Config(tmp_path)
    assert cfg.has_embedding_override() is False
    assert cfg.embedding_provider == "openai"
    assert cfg.embedding_base_url == "https://api.openai.com/v1"
    assert cfg.embedding_api_key == "chat-key"
    assert cfg.embedding_model == "text-embedding-3-small"


def test_default_persist_dir_config(tmp_path):
    from backend.config import Config
    (tmp_path / "aurora.json").write_text(
        json.dumps({"rag": {"persist_dir": "./custom_idx"}}), encoding="utf-8")
    assert Config(tmp_path).rag_persist_dir == "./custom_idx"
    (tmp_path / "aurora.json").write_text(json.dumps({}), encoding="utf-8")
    assert Config(tmp_path).rag_persist_dir == "./chroma_db"
