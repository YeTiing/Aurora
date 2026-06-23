# view_image — image viewer + vision analysis fallback
from __future__ import annotations
import base64, os, pathlib
from typing import Any
from .base import ToolSpec, ToolCallResult, safe_resolve_path

VIEW_IMAGE_SPEC = ToolSpec(
    name="view_image",
    description=(
        "View a local image file from the filesystem when visual inspection is needed. "
        "Use this for images already available on disk. "
        "Set analyze=true to get AI vision analysis of the image content "
        "(auto-fallback to vision model if main model lacks vision capability)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Local filesystem path to an image file."
            },
            "analyze": {
                "type": "boolean",
                "description": "Set true to get AI analysis of the image. Useful when your model lacks vision."
            },
            "question": {
                "type": "string",
                "description": "Specific question about the image for AI analysis."
            },
        },
        "required": ["path"]
    },
    category="file_io",
    exposure="direct",
)

SUPPORTED_FORMATS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico", ".tiff", ".tif"}


async def view_image_handler(arguments: dict, workspace: str = ".") -> ToolCallResult:
    path_str = arguments.get("path", "")
    analyze = arguments.get("analyze", False)
    question = arguments.get("question", "")

    if not path_str:
        return ToolCallResult(id="", name="view_image", output="", success=False, error="No path provided")

    try:
        file_path = safe_resolve_path(path_str, workspace)
    except PermissionError as e:
        return ToolCallResult(id="", name="view_image", output="", success=False, error=str(e))

    if not file_path.exists():
        return ToolCallResult(id="", name="view_image", output="", success=False,
                               error=f"File not found: {path_str}")

    ext = file_path.suffix.lower()
    if ext not in SUPPORTED_FORMATS:
        return ToolCallResult(id="", name="view_image", output="", success=False,
                               error=f"Unsupported format: {ext}. Supported: {', '.join(sorted(SUPPORTED_FORMATS))}")

    size_bytes = file_path.stat().st_size
    if size_bytes > 20_000_000:
        return ToolCallResult(id="", name="view_image", output="", success=False,
                               error=f"Image too large: {size_bytes} bytes (max 20MB)")

    # Read image
    try:
        with open(file_path, "rb") as f:
            img_data = f.read()

        mime_map = {
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
            ".svg": "image/svg+xml", ".ico": "image/x-icon",
            ".tiff": "image/tiff", ".tif": "image/tiff",
        }
        mime = mime_map.get(ext, "application/octet-stream")
        data_url = f"data:{mime};base64,{base64.b64encode(img_data).decode('ascii')}"

        # Try to get dimensions
        dimensions = ""
        try:
            if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
                import struct
                if ext == ".png" and img_data[:8] == b'\x89PNG\r\n\x1a\n':
                    w, h = struct.unpack('>II', img_data[16:24])
                    dimensions = f" {w}x{h}"
                elif ext in (".jpg", ".jpeg") and img_data[:2] == b'\xff\xd8':
                    dimensions = " (JPEG)"
                elif ext == ".gif" and img_data[:3] == b'GIF':
                    w, h = struct.unpack('<HH', img_data[6:10])
                    dimensions = f" {w}x{h}"
                elif ext == ".webp" and img_data[:4] == b'RIFF':
                    dimensions = " (WebP)"
        except Exception:
            pass

        # If analyze mode: use vision subagent
        analysis_text = ""
        if analyze:
            try:
                from backend.agent.vision_subagent import get_vision_analyzer
                analyzer = get_vision_analyzer()
                q = question or "Describe this image in detail. What do you see? Be thorough but concise."
                analysis_text = analyzer.analyze_sync(str(file_path), q)
                analysis_text = f"\n\n## Vision Analysis\n{analysis_text}"
            except Exception as e:
                analysis_text = f"\n\n## Vision Analysis Failed\nError: {type(e).__name__}: {str(e)[:300]}"

        output = (
            f"![{file_path.name}]({data_url[:100]}...)\n\n"
            f"Image: {file_path.name}\n"
            f"Size: {size_bytes:,} bytes{dimensions}\n"
            f"Format: {ext}\n"
            f"{'Path: ' + str(file_path)}"
            f"{analysis_text}"
        )
        return ToolCallResult(
            id="", name="view_image", output=output, success=True,
            metadata={
                "path": str(file_path), "size": size_bytes, "format": ext, "mime": mime,
                "analyzed": analyze, "data_url_prefix": data_url[:100],
            }
        )
    except Exception as e:
        return ToolCallResult(id="", name="view_image", output="", success=False,
                               error=f"Failed to read image: {e}")
