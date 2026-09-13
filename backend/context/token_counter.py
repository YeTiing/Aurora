# Token 计数器 — 多模型支持
from __future__ import annotations
try:
    import tiktoken
except Exception:  # tiktoken 未安装时也要能导入（模块级 import 不得失败）
    tiktoken = None

MODEL_ENCODINGS = {
    "gpt-4o": "o200k_base",
    "gpt-4o-mini": "o200k_base",
    "gpt-4-turbo": "cl100k_base",
    "gpt-4": "cl100k_base",
    "gpt-3.5-turbo": "cl100k_base",
    "claude-3-opus": "cl100k_base",
    "claude-3.5-sonnet": "cl100k_base",
    "text-embedding-3-small": "cl100k_base",
    "text-embedding-ada-002": "cl100k_base",
}

MESSAGE_OVERHEAD = 4
REPLY_OVERHEAD = 2

# 已解析编码器缓存（进程级）。tiktoken 的 encoding_for_model 在首次使用时会
# 下载 BPE 文件；缓存避免重复下载与重复失败尝试。
_ENCODER_CACHE: dict = {}

# 断网/编码缺失的哨兵：一旦降级就不再反复重试（否则每次 count() 都触发网络）。
_OFFLINE = object()


def _encoder_lookup(model: str, encoding_name: str):
    """真正解析编码器。抽成独立函数便于测试替换。

    可能抛任意异常（KeyError、requests 的 SSLError 等）——调用方负责降级。
    """
    if tiktoken is None:
        raise RuntimeError("tiktoken unavailable")
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        return tiktoken.get_encoding(encoding_name)


class TokenCounter:
    # 注意：__init__ 不做任何编码器解析，构造是零网络、零副作用的。
    # 旧实现会在 __init__ 里直接 encoding_for_model，配合模块底部
    # ``counter = TokenCounter()`` 等价于「import 即联网下载 BPE」；在无网络或
    # TLS 中间人代理环境下 requests.exceptions.SSLError 会让整个 backend
    # 无法导入。现在解析推迟到第一次 count()，并带离线降级。
    def __init__(self, model: str = "gpt-4o"):
        self.model = model
        self._encoder = None

    def _resolve_encoder(self):
        """返回 tiktoken 编码器；不可用时返回 None（不抛出）。"""
        if self._encoder is not None:
            return None if self._encoder is _OFFLINE else self._encoder
        cached = _ENCODER_CACHE.get(self.model)
        if cached is not None:
            self._encoder = cached
            return cached
        encoding_name = MODEL_ENCODINGS.get(self.model, "cl100k_base")
        try:
            enc = _encoder_lookup(self.model, encoding_name)
        except Exception:
            # 网络失败 / TLS 拦截 / 编码文件缺失 / 任何 tiktoken 异常
            # 一律降级，绝不向调用方传播。
            self._encoder = _OFFLINE
            return None
        _ENCODER_CACHE[self.model] = enc
        self._encoder = enc
        return enc

    @staticmethod
    def _estimate(text: str) -> int:
        """离线近似：约 4 字符 ≈ 1 token；非空文本至少 1。"""
        if not text:
            return 0
        return max(1, len(text) // 4)

    def count(self, text: str) -> int:
        encoder = self._resolve_encoder()
        if encoder is None:
            return self._estimate(text)
        return len(encoder.encode(text))

    def count_messages(self, messages: list[dict]) -> int:
        total = 0
        for msg in messages:
            total += MESSAGE_OVERHEAD
            for key, value in msg.items():
                if isinstance(value, str):
                    total += self.count(value)
                elif isinstance(value, list):
                    total += self.count(str(value))
                elif key == "name":
                    total += self.count(str(value)) - 1  # name already accounted
        total += REPLY_OVERHEAD
        return total

    def count_tool_schemas(self, tools: list[dict]) -> int:
        import json
        return self.count(json.dumps(tools, ensure_ascii=False))

    def change_model(self, model: str):
        if model != self.model:
            self.model = model
            self._encoder = None


_counter: TokenCounter | None = None


def get_counter() -> TokenCounter:
    """惰性获取全局计数器：首次调用才构造（构造本身仍不联网）。"""
    global _counter
    if _counter is None:
        _counter = TokenCounter()
    return _counter


class _LazyCounter:
    """惰性代理：模块导入时不再实例化 TokenCounter。

    保留 ``counter`` 这个公开名字以兼容既有消费者，同时把实例化推迟到首次
    属性访问。``__slots__`` 防止代理自身持有状态。
    """

    __slots__ = ()

    def __getattr__(self, item):
        return getattr(get_counter(), item)


counter = _LazyCounter()
