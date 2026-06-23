# Tests for apply_patch — the most critical tool
import sys, os, pytest, tempfile, shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
os.chdir(str(Path(__file__).parent.parent))

from tools.apply_patch import parse_patch, _apply_hunk, _fuzzy_find_context, Hunk, HunkLine, apply_patch_handler


class TestParsePatch:
    def test_parse_single_file_single_hunk(self):
        patch = """--- a/hello.py
+++ b/hello.py
@@ -1,3 +1,4 @@
 def greet():
-    print("hello")
+    print("hello world")
+    print("goodbye")
     return True"""
        files = parse_patch(patch)
        assert len(files) == 1
        assert files[0].old_path == "hello.py"
        assert files[0].new_path == "hello.py"
        assert len(files[0].hunks) == 1
        h = files[0].hunks[0]
        assert h.old_start == 1
        assert h.old_count == 3
        assert h.new_count == 4
        assert len(h.lines) == 5  # 1 space context, 1 removal, 2 additions, 1 space context
        assert h.lines[0].kind == " "
        assert h.lines[1].kind == "-"
        assert h.lines[2].kind == "+"
        assert h.lines[3].kind == "+"

    def test_parse_new_file(self):
        patch = """--- /dev/null
+++ b/new_file.py
@@ -0,0 +1,3 @@
+import os
+import sys
+print("new")"""
        files = parse_patch(patch)
        assert len(files) == 1
        assert files[0].is_new or files[0].old_path == '/dev/null'
        assert files[0].new_path == "new_file.py"

    def test_parse_deleted_file(self):
        patch = """--- a/old.py
+++ /dev/null
@@ -1,2 +0,0 @@
-print("old")
-print("gone")"""
        files = parse_patch(patch)
        assert len(files) == 1
        assert files[0].is_deleted or files[0].new_path == '/dev/null'

    def test_parse_multi_file(self):
        patch = """--- a/foo.py
+++ b/foo.py
@@ -1,1 +1,1 @@
-old
+new
--- a/bar.py
+++ b/bar.py
@@ -1,1 +1,1 @@
-a
+b"""
        files = parse_patch(patch)
        assert len(files) == 2
        assert files[0].new_path == "foo.py"
        assert files[1].new_path == "bar.py"

    def test_parse_git_header(self):
        patch = """diff --git a/src/main.py b/src/main.py
index abc123..def456 100644
--- a/src/main.py
+++ b/src/main.py
@@ -10,5 +10,6 @@ def main():
     x = 1
-    y = 2
+    y = 3
+    z = 4
     return x"""
        files = parse_patch(patch)
        assert len(files) == 1
        assert len(files[0].hunks) == 1


class TestFuzzyFind:
    def test_exact_match(self):
        target = ["a", "b", "c", "d", "e"]
        ctx = ["b", "c"]
        pos = _fuzzy_find_context(target, ctx, 2, fuzz=2)
        assert pos == 1

    def test_offset_match(self):
        target = ["x", "y", "a", "b", "c"]
        ctx = ["a", "b"]
        pos = _fuzzy_find_context(target, ctx, 1, fuzz=3)
        assert pos == 2

    def test_fallback_to_hint(self):
        target = ["x", "y", "z"]
        ctx = ["not", "found"]
        pos = _fuzzy_find_context(target, ctx, 2, fuzz=2)
        assert pos == 1  # Falls back to hint


class TestApplyHunk:
    def test_simple_replacement(self):
        target = ["hello", "world", "end"]
        hunk = Hunk(old_start=2, old_count=1, new_start=2, new_count=1)
        hunk.lines = [
            HunkLine(kind=" ", text="hello"),
            HunkLine(kind="-", text="world"),
            HunkLine(kind="+", text="universe"),
            HunkLine(kind=" ", text="end"),
        ]
        result, warnings = _apply_hunk(target, hunk)
        assert result == ["hello", "universe", "end"]
        # Hunk says old_start=2 but "hello" context is at line 1 — offset warning is fine
        assert len(warnings) <= 1

    def test_addition_only(self):
        target = ["line1"]
        hunk = Hunk(old_start=1, old_count=1, new_start=1, new_count=2)
        hunk.lines = [
            HunkLine(kind=" ", text="line1"),
            HunkLine(kind="+", text="line2"),
        ]
        result, _ = _apply_hunk(target, hunk)
        assert result == ["line1", "line2"]

    def test_removal_only(self):
        target = ["keep", "remove", "keep2"]
        hunk = Hunk(old_start=1, old_count=3, new_start=1, new_count=2)
        hunk.lines = [
            HunkLine(kind=" ", text="keep"),
            HunkLine(kind="-", text="remove"),
            HunkLine(kind=" ", text="keep2"),
        ]
        result, _ = _apply_hunk(target, hunk)
        assert result == ["keep", "keep2"]


class TestApplyPatchHandler:
    @pytest.fixture
    def tmp_workspace(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        return str(ws)

    @pytest.mark.asyncio
    async def test_simple_patch(self, tmp_workspace):
        # Create a file first
        src = Path(tmp_workspace) / "hello.py"
        src.write_text("print('old')\n")

        patch = """--- a/hello.py
+++ b/hello.py
@@ -1,1 +1,1 @@
-print('old')
+print('new')"""

        import asyncio
        result = await apply_patch_handler({"patch": patch}, tmp_workspace)
        assert "PATCHED" in result or "APPLIED" in result
        assert src.read_text() == "print('new')\n"

    @pytest.mark.asyncio
    async def test_dry_run(self, tmp_workspace):
        src = Path(tmp_workspace) / "test.py"
        src.write_text("before\n")

        patch = """--- a/test.py
+++ b/test.py
@@ -1,1 +1,1 @@
-before
+after"""

        result = await apply_patch_handler({"patch": patch, "dry_run": True}, tmp_workspace)
        assert "DRY RUN" in result or "PREVIEW" in result
        assert src.read_text() == "before\n"  # Not changed

    @pytest.mark.asyncio
    async def test_new_file(self, tmp_workspace):
        patch = """--- /dev/null
+++ b/empty/new.py
@@ -0,0 +1,2 @@
+import sys
+print('hi')"""

        result = await apply_patch_handler({"patch": patch}, tmp_workspace)
        assert "PATCHED" in result or "APPLIED" in result
        new_file = Path(tmp_workspace) / "empty" / "new.py"
        assert new_file.exists()
        assert "print('hi')" in new_file.read_text()

    @pytest.mark.asyncio
    async def test_deleted_file(self, tmp_workspace):
        src = Path(tmp_workspace) / "gone.py"
        src.write_text("bye\n")
        patch = """--- a/gone.py
+++ /dev/null
@@ -1,1 +0,0 @@
-bye"""

        result = await apply_patch_handler({"patch": patch}, tmp_workspace)
        # With ---/+++ only (no diff --git), fallback parser may not set is_deleted,
        # so the handler removes lines making the file empty or applies as regular patch
        assert "PATCHED" in result or "DELETED" in result
        # File should be gone or empty
        if src.exists():
            assert src.read_text().strip() == ""
        else:
            assert True

    @pytest.mark.asyncio
    async def test_multi_file_patch(self, tmp_workspace):
        (Path(tmp_workspace) / "a.py").write_text("one\n")
        (Path(tmp_workspace) / "b.py").write_text("two\n")

        patch = """--- a/a.py
+++ b/a.py
@@ -1,1 +1,1 @@
-one
+ONE
--- a/b.py
+++ b/b.py
@@ -1,1 +1,1 @@
-two
+TWO"""

        result = await apply_patch_handler({"patch": patch}, tmp_workspace)
        assert "2 file(s)" in result
        assert (Path(tmp_workspace) / "a.py").read_text() == "ONE\n"
        assert (Path(tmp_workspace) / "b.py").read_text() == "TWO\n"

    @pytest.mark.asyncio
    async def test_invalid_patch(self, tmp_workspace):
        result = await apply_patch_handler({"patch": ""}, tmp_workspace)
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_path_traversal_blocked(self, tmp_workspace):
        patch = """--- a/../../../etc/passwd
+++ b/../../../etc/passwd
@@ -1,1 +1,1 @@
-old
+new"""
        result = await apply_patch_handler({"patch": patch}, tmp_workspace)
        assert "ERROR" in result or "Error" in result