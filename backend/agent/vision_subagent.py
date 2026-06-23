"""Vision SubAgent — image analysis fallback when main model lacks vision support.

Uses a separate vision-capable LLM (auto-detected or configured) to analyze images.
The result is streamed back to the main agent as if it had vision capability.
"""

from __future__ import annotations
import base64, json, os, io
from pathlib import Path
from typing import Any, Optional

import httpx

from backend.model_discovery import KNOWN_MODELS

# Vision models ranked by preference (cheapest capable first)
VISION_FALLBACK_PRIORITY = [
    "gpt-4o-mini",       # cheapest vision model
    "gpt-4o",            # best multimodal
    "claude-3-haiku",    # cheap Claude vision
    "claude-3-5-sonnet", # good Claude vision
    "gpt-4-turbo",       # older but works
    "claude-3-opus",     # expensive
]

VISION_FALLBACK_PROVIDER = "openai"  # default for vision fallback


class VisionAnalyzer:
    """Self-contained vision analysis using any vision-capable model."""

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        provider: str = "openai",
    ):
        self.model = model or self._auto_detect_vision_model()
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.base_url = base_url or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        self.provider = provider

    @staticmethod
    def _auto_detect_vision_model() -> str:
        """Find the cheapest available vision-capable model."""
        # Check if any known vision models have API keys set
        for model_id in VISION_FALLBACK_PRIORITY:
            info = KNOWN_MODELS.get(model_id, {})
            if info.get("vision_support"):
                return model_id
        return "gpt-4o-mini"

    def _format_messages(self, image_data: str, mime: str, question: str) -> list[dict]:
        """Build OpenAI-compatible vision messages."""
        return [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime};base64,{image_data}",
                            "detail": "auto",
                        },
                    },
                    {
                        "type": "text",
                        "text": question or "Please describe this image in detail. What do you see?",
                    },
                ],
            }
        ]

    async def analyze(
        self,
        image_path: str,
        question: str = "",
        max_tokens: int = 2000,
    ) -> str:
        """Analyze an image and return the description/answer."""
        path = Path(image_path)
        if not path.exists():
            return f"[Vision Agent Error] Image not found: {image_path}"

        # Read and encode image
        img_bytes = path.read_bytes()
        mime_map = {
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
        }
        ext = path.suffix.lower()
        mime = mime_map.get(ext, "image/png")
        img_b64 = base64.b64encode(img_bytes).decode("ascii")

        # Truncate if too large (> 20MB roughly)
        if len(img_bytes) > 20_000_000:
            return f"[Vision Agent Error] Image too large: {len(img_bytes):,} bytes (max 20MB)"

        messages = self._format_messages(img_b64, mime, question)

        endpoint = f"{self.base_url.rstrip('/')}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.2,
        }

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(endpoint, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                return content
        except httpx.HTTPStatusError as e:
            return f"[Vision Agent Error] HTTP {e.response.status_code}: {e.response.text[:300]}"
        except Exception as e:
            return f"[Vision Agent Error] {type(e).__name__}: {str(e)[:300]}"

    def analyze_sync(self, image_path: str, question: str = "", max_tokens: int = 2000) -> str:
        """Synchronous wrapper for non-async contexts."""
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            # We're in async context, use run_coroutine_threadsafe or create_task
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(lambda: asyncio.run(self.analyze(image_path, question, max_tokens)))
                return future.result(timeout=60)
        except RuntimeError:
            # No running loop, safe to use asyncio.run
            return asyncio.run(self.analyze(image_path, question, max_tokens))


def get_vision_analyzer() -> VisionAnalyzer:
    """Factory: auto-reuse main LLM config, zero setup needed.
    
    Priority: dedicated vision_fallback config > main LLM config > env vars > auto-detect.
    """
    from backend.config import config as cfg

    # 1. Try dedicated vision_fallback config
    vision_cfg = {}
    if cfg is not None:
        vision_cfg = cfg.get("vision_fallback", {})

    model = vision_cfg.get("model") or None
    api_key = vision_cfg.get("api_key") or None
    base_url = vision_cfg.get("base_url") or None
    provider = vision_cfg.get("provider", "openai")

    # 2. If no dedicated config, auto-reuse main LLM config
    if not api_key and cfg is not None:
        api_key = cfg.get("llm.api_key") or cfg.get("api_key") or os.environ.get("OPENAI_API_KEY")
    if not base_url and cfg is not None:
        base_url = cfg.get("llm.base_url") or cfg.get("base_url") or ""

    # 3. Auto-detect best vision model if not specified
    if not model:
        model = VisionAnalyzer._auto_detect_vision_model()

    return VisionAnalyzer(
        model=model,
        api_key=api_key or None,
        base_url=base_url or None,
        provider=provider,
    )


def main_model_has_vision() -> bool:
    """Check if currently configured main model supports vision."""
    from backend.config import config
    model = config.get("model", "") if config else ""
    if not model:
        return True  # default assumption
    info = KNOWN_MODELS.get(model)
    if info:
        return info.get("vision_support", False)
    # Unknown model — check common vision models
    vision_patterns = ["gpt-4o", "claude", "gemini", "vision", "vl"]
    return any(p in model.lower() for p in vision_patterns)
