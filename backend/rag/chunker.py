# RAG AST 语义分块 — Tree-sitter + 正则兜底
from __future__ import annotations
import re
from dataclasses import dataclass, field
from pathlib import Path

@dataclass
class CodeChunk:
    id: str
    content: str
    file_path: str
    start_line: int
    end_line: int
    chunk_type: str
    signature: str
    language: str
    metadata: dict = field(default_factory=dict)

SUPPORTED_LANGS = {".py":"python",".ts":"typescript",".tsx":"tsx",".js":"javascript",".jsx":"javascript",
    ".go":"go",".rs":"rust",".java":"java",".kt":"kotlin",".swift":"swift",".c":"c",".cpp":"cpp",
    ".h":"c",".hpp":"cpp",".sql":"sql",".sh":"bash",".ps1":"powershell",".yaml":"yaml",".yml":"yaml",
    ".json":"json",".md":"markdown",".toml":"toml"}

class ASTChunker:
    def __init__(self):
        self._parsers = {}

    def _get_parser(self, lang: str):
        if lang in self._parsers: return self._parsers[lang]
        try:
            import tree_sitter
            py_langs = {"python","typescript","tsx","javascript"}
            if lang in py_langs:
                if lang in ("typescript","tsx"):
                    import tree_sitter_typescript as t
                    ts_lang = t.language_typescript() if lang=="typescript" else t.language_tsx()
                else:
                    import tree_sitter_python as t
                    ts_lang = t.language() if hasattr(t,'language') else t.language()
                parser = tree_sitter.Parser()
                parser.set_language(tree_sitter.Language(ts_lang))
                self._parsers[lang] = parser
                return parser
        except ImportError: pass
        self._parsers[lang] = None
        return None

    def chunk_file(self, file_path: Path) -> list[CodeChunk]:
        ext = file_path.suffix.lower()
        lang = SUPPORTED_LANGS.get(ext, "")
        if not lang: return []
        content = file_path.read_text("utf-8",errors="replace")
        parser = self._get_parser(lang)
        if parser:
            return self._tree_sitter_chunk(content, str(file_path), lang, parser)
        return self._regex_chunk(content, str(file_path), lang, ext)

    def _tree_sitter_chunk(self, content: str, fpath: str, lang: str, parser) -> list[CodeChunk]:
        NODE_TYPES = {
            "python": ("function_definition","class_definition","method_definition"),
            "typescript": ("function_declaration","method_definition","class_declaration","interface_declaration","export_statement"),
            "tsx": ("function_declaration","method_definition","class_declaration","interface_declaration","export_statement"),
            "javascript": ("function_declaration","method_definition","class_declaration","export_statement"),
        }
        types = NODE_TYPES.get(lang, ("function_definition","class_definition"))
        tree = parser.parse(content.encode())
        chunks = []
        lines = content.split("\n")
        self._walk(tree.root_node, lines, fpath, lang, types, chunks)

        if not chunks:
            chunks.append(CodeChunk(id=f"{fpath}:1-{len(lines)}", content=content[:3000],
                file_path=fpath, start_line=1, end_line=len(lines), chunk_type="file", signature="", language=lang))
        return chunks

    def _walk(self, node, lines, fpath, lang, types, chunks, depth=0):
        if depth > 4: return
        for child in node.children:
            if child.type in types:
                s,e = child.start_point[0]+1, child.end_point[0]+1
                sig = "\n".join(lines[s-1:min(s+2,e)])
                chunk_content = "\n".join(lines[s-1:e])
                chunks.append(CodeChunk(id=f"{fpath}:{s}-{e}", content=chunk_content,
                    file_path=fpath, start_line=s, end_line=e, chunk_type=child.type, signature=sig[:200], language=lang))
            self._walk(child, lines, fpath, lang, types, chunks, depth+1)

    def _regex_chunk(self, content: str, fpath: str, lang: str, ext: str) -> list[CodeChunk]:
        patterns = {
            ".py": r'^\s*(?:async\s+)?(?:def |class )\w+',
            ".ts": r'^\s*(?:export\s+)?(?:async\s+)?(?:function |class |const \w+\s*=\s*(?:async\s+)?\()',
            ".tsx": r'^\s*(?:export\s+)?(?:async\s+)?(?:function |class |const \w+\s*=\s*(?:async\s+)?\(|interface |type )',
            ".js": r'^\s*(?:async\s+)?(?:function |class |const \w+\s*=\s*(?:async\s+)?\()',
            ".jsx": r'^\s*(?:async\s+)?(?:function |class |const \w+\s*=\s*(?:async\s+)?\(|export )',
            ".go": r'^\s*func\s+(?:\(\w+\s+\*?\w+\)\s+)?\w+',
            ".rs": r'^\s*(?:pub\s+)?(?:async\s+)?fn\s+\w+|^\s*(?:pub\s+)?struct\s+\w+|^\s*(?:pub\s+)?impl\b',
            ".java": r'^\s*(?:public|private|protected)?\s*(?:static\s+)?(?:class|interface|enum)\s+\w+|^\s*(?:public|private|protected)?\s*(?:static\s+)?\w+\s+\w+\s*\(',
        }
        pat = patterns.get(ext, r'^.*$')
        lines = content.split("\n")
        boundaries = [1]
        for i, line in enumerate(lines):
            if re.match(pat, line):
                boundaries.append(i+1)
        boundaries.append(len(lines)+1)
        chunks = []
        for j in range(len(boundaries)-1):
            s, e = boundaries[j], boundaries[j+1]
            chunk_lines = lines[s-1:e-1]
            if not chunk_lines: continue
            sig = chunk_lines[0].strip()[:200] if chunk_lines else ""
            chunks.append(CodeChunk(id=f"{fpath}:{s}-{e-1}", content="\n".join(chunk_lines),
                file_path=fpath, start_line=s, end_line=e-1, chunk_type="block", signature=sig, language=lang))
        return chunks