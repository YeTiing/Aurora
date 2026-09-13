# Phase 0 — pyright LSP Protocol Probe Findings

**Status: COMPLETE.** Every answer below is backed by a JSON file in this directory,
produced by `probe/probe.py` against real pyright output. No mock data.

## Environment

| Item | Value |
|---|---|
| pyright install method | `npm i -g pyright` (npm global, worked first try) |
| pyright version | **1.1.414** |
| langserver entrypoint | `C:\home\zenos\.npm-global\node_modules\pyright\langserver.index.js` |
| launcher shims | `C:\home\zenos\.npm-global\pyright-langserver(.cmd)` |
| node used to spawn | `D:\NodeJS\node.exe` (v24.15.0) |
| target repo | `D:\codex_Projects\Aurora` |
| probe target symbol | `backend/tools/base.py::safe_resolve_path` (module-level function, 25 cross-file refs) |

`pyright-langserver --stdio` starts and completes the handshake. Note: on Windows the
`.cmd` shim cannot be spawned by `asyncio.create_subprocess_exec`, so the probe invokes
`node.exe <pyright>/langserver.index.js --stdio` directly. Production code must do the same.

**Init params used (the fix from INDEX.md §2.2), confirmed in `01_initialize.json`:**

```json
"_probe_meta": {
  "rootUri_sent": "file:///D:/codex_Projects/Aurora",
  "workspaceFolders_sent": [{"uri": "file:///D:/codex_Projects/Aurora", "name": "Aurora"}],
  "processId_sent": 172812,
  "callHierarchyProvider_truthy": true
}
```

The correct `rootUri` + `workspaceFolders` produce working cross-file resolution:
`incomingCalls` returned callers from **4 different source files plus 6 test files**.

---

## Q1 — `documentSymbol`: `range` vs `selectionRange` — which points at the name?

**Answer: `selectionRange.start` points at the symbol name. `range.start` points at the
`def` keyword / decorator and is the WRONG point to use.**

Evidence — `03_document_symbol.json`, entry `safe_resolve_path`:

```json
{
  "name": "safe_resolve_path",
  "kind": 12,
  "range":          { "start": {"line": 45, "character": 0}, "end": {"line": 59, "character": 19} },
  "selectionRange": { "start": {"line": 45, "character": 4}, "end": {"line": 45, "character": 21} }
}
```

Source line 46 (0-indexed line 45) is:

```
def safe_resolve_path(target: str, workspace: str) -> Path:
```

- `range.start = {45, 0}` → column 0 = the `d` of **`def`**. `range` spans the whole body
  (lines 45–59).
- `selectionRange.start = {45, 4}` → column 4 = the `s` of **`safe_resolve_path`**.
  `selectionRange` spans exactly the name (cols 4–21).

`range` = declaration span (for extracting source text / spans).
`selectionRange` = name span (for position queries).

**采点规则 (position-picking rule): always use `selectionRange.start` for
`references` / `prepareCallHierarchy` / any position-based query.**

The same rule applies to methods. In `08_implementation_method.json` (class method
`BaseProvider.chat_stream`), `range.start = {206,4}` (decorator/def line) while
`selectionRange.start = {207,14}` (the name).

---

## Q2 — `references` with `range.start` vs `selectionRange.start`

**Answer: `range.start` returns an EMPTY result. `selectionRange.start` returns the full
cross-file reference set. This is a silent failure — no error, just `null`/`[]`.**

| Position used | Request file | Count |
|---|---|---|
| `range.start = {45,0}` | `04a_refs_by_range.json` | **0** (`"raw_result": null`) |
| `selectionRange.start = {45,4}` | `04b_refs_by_selection.json` | **25** |

`04a_refs_by_range.json` in full:

```json
{
  "_probe_meta": {
    "position_used": {"line": 45, "character": 0},
    "position_kind": "range.start",
    "includeDeclaration": false,
    "count": 0,
    "files": []
  },
  "raw_result": null
}
```

`04b_refs_by_selection.json` meta:

```json
{
  "position_used": {"line": 45, "character": 4},
  "position_kind": "selectionRange.start",
  "includeDeclaration": false,
  "count": 25,
  "files": ["__init__.py","apply_patch.py","code_search.py","file_rw.py",
            "list_files.py","shell_command.py","test_fixes.py","test_tools.py","view_image.py"]
}
```

25 refs across **9 files**. This is the single highest-risk silent failure in the design:
picking `range.start` yields an empty graph with no error surfaced.

### Corollary — `includeDeclaration` (INDEX.md §2.2 ②) is CONFIRMED

An extra probe (`04c_refs_include_declaration.json`) compared `true` vs `false` at the same
position:

```json
"includeDeclaration": true,
"count": 26,
"delta_vs_false": 1,
"declaration_entry_present_at_selectionRange_start": 1,
"same_file_refs": [{"line": 45, "character": 4}]
```

`includeDeclaration: true` adds exactly one entry — the declaration itself at
`base.py:45:4` — which is what would create a self-loop in the graph. **The doc's claim is
correct: pass `includeDeclaration: false`.** Note this is only visible with the *correct*
(`selectionRange`) position; at `range.start` both settings return 0.

---

## Q3 — `incomingCalls` structure

**Answer: a flat array of edge objects `{ "from": CallHierarchyItem, "fromRanges": Range[] }`.
`fromRanges` exists and is the *call-site* location(s). Nesting is depth-1 — `from` is an
item, not another edge; recursion is done by re-querying `incomingCalls` with `from` as the
new item.**

Evidence — `06_incoming_calls.json` meta:

```json
{
  "count": 10,
  "caller_names": ["apply_patch_handler","file_rw_handler","list_files_handler",
                   "view_image_handler","test_normal_path_allowed","test_workspace_itself_allowed",
                   "test_sibling_prefix_traversal_blocked","test_double_dot_escape_blocked",
                   "test_safe_path_in_workspace","test_safe_path_traversal_blocked"],
  "from_keys": ["detail","kind","name","range","selectionRange","uri"],
  "has_fromRanges": true,
  "fromRanges_len": 1,
  "edge_keys": ["from","fromRanges"]
}
```

First edge in full:

```json
{
  "from": {
    "name": "apply_patch_handler",
    "kind": 12,
    "detail": "(apply_patch.py)",
    "uri": "file:///d%3A/codex_Projects/Aurora/backend/tools/apply_patch.py",
    "range":          { "start": {"line": 638, "character": 10}, "end": {"line": 638, "character": 29} },
    "selectionRange": { "start": {"line": 638, "character": 10}, "end": {"line": 638, "character": 29} }
  },
  "fromRanges": [ { "start": {"line": 723, "character": 24}, "end": {"line": 723, "character": 41} } ]
}
```

Key observations for the builder:

1. **`from` is a full `CallHierarchyItem`**: `{name, kind, detail, uri, range, selectionRange}`.
   Same 6 keys as the `prepareCallHierarchy` item (`05_prepare_call_hierarchy.json`).
2. **`fromRanges` = the call sites** (where inside the caller the call appears), NOT the
   callee's location. This is essential for precise reference spans.
3. **`kind: 12` = Function** (LSP `SymbolKind.Function`); methods are `6`, classes `5`,
   variables `13`. Derived from `03_document_symbol.json`.
4. **`detail` is not a qualified name** — it is inconsistent: `"(base.py)"` for a
   module-level function, but `"class Path (__init__.pyi) · Standard library"` for an
   imported/stdlib symbol. Do **not** parse `detail` to build qualified names.
5. **`fromRanges` can contain duplicates.** In `09_method_call_hierarchy.json`
   (`BaseProvider.chat_stream`), one edge had:
   `"fromRanges": [{"line":201,"character":101},{"line":201,"character":101}]`
   — the same range twice. Deduplicate call sites before persisting edges.

Also captured: `06b_outgoing_calls.json` — 7 outgoing calls. **All of them point into
pyright's bundled typeshed** (`.../pyright/dist/typeshed-fallback/stdlib/pathlib/__init__.pyi`,
`builtins.pyi`), e.g. `Path.resolve`, `PurePath.is_relative_to`, `str.startswith`,
`PermissionError`. The builder MUST filter URIs outside the workspace root or the graph will
be flooded with stdlib nodes.

`09_method_call_hierarchy.json` confirms call hierarchy works for **methods** too, not just
module-level functions: `BaseProvider.chat_stream` → 2 incoming (`LLMClient.chat_stream`,
`ProviderPool.chat_stream`), resolved across files.

---

## Q4 — `textDocument/implementation` on Python

**Answer: pyright does NOT implement this method at all.** It returns JSON-RPC error
`-32601 Unhandled method textDocument/implementation` for both a plain function and an
abstract method. It is not "semantically mismatched Java-style implements" — the capability
is entirely absent.

Evidence — `07_implementation.json` (plain function):

```json
{
  "_probe_meta": {
    "target": "backend/tools/base.py::safe_resolve_path (plain module-level function)",
    "position_used": {"line": 45, "character": 4},
    "server_error": {
      "method": "textDocument/implementation",
      "detail": "{'code': -32601, 'message': 'Unhandled method textDocument/implementation'}"
    },
    "result_type": "NoneType",
    "count": null
  },
  "raw_result": null
}
```

`08_implementation_method.json` (abstract method `BaseProvider.chat_stream`, method was
found in `documentSymbol`) returns the identical `-32601` error.

Static confirmation from the installed package: `textDocument/implementation` appears only
in pyright's bundled `vendor.js` (the generic vscode-languageserver library) and **not at
all in `pyright-internal.js`**; no `implementationProvider` key appears in the initialize
result capabilities.

**Verdict on the design doc's judgment: correct outcome, wrong reason.** INDEX.md §3.4 says
to drop `implements` because pyright's semantics differ. The stronger, verified fact is that
the method is unimplemented, so there is nothing to accidentally include. Keep it excluded
from the graph — but also do not waste Phase 1 time wiring an `implementation` edge type or
testing it; it can never return data on pyright.

---

## Q5 — `capabilities.callHierarchyProvider`

**Answer: `true`.**

Evidence — `01_initialize.json`:

```json
"callHierarchyProvider": true
```

(`_probe_meta.callHierarchyProvider_truthy` is also `true`.) Full capability key list
returned by pyright 1.1.414:

```
callHierarchyProvider, codeActionProvider, completionProvider, declarationProvider,
definitionProvider, documentHighlightProvider, documentSymbolProvider,
executeCommandProvider, hoverProvider, referencesProvider, renameProvider,
signatureHelpProvider, textDocumentSync, typeDefinitionProvider, workspace,
workspaceSymbolProvider
```

---

## CRITICAL corrections to the design doc / task spec

1. **Wrong LSP method name — would have failed 100% of the time.**
   The task spec and INDEX.md §6.2 say `callHierarchy/prepareCallHierarchy`. The correct
   method is **`textDocument/prepareCallHierarchy`**. Sending the former returns:
   `{'code': -32601, 'message': 'Unhandled method callHierarchy/prepareCallHierarchy'}`.
   The sub-methods `callHierarchy/incomingCalls` and `callHierarchy/outgoingCalls` *are*
   correct (no `textDocument/` prefix). This was verified directly in pyright's bundled
   `vendor.js`, which registers `textDocument/prepareCallHierarchy` +
   `callHierarchy/incomingCalls` + `callHierarchy/outgoingCalls`.

2. **`implementation` is unimplemented, not just semantically wrong** (see Q4). The
   exclusion decision stands, but the stated justification does not.

3. **`CallHierarchyItem.range` is NOT the declaration span — it equals `selectionRange`.**
   In `05_prepare_call_hierarchy.json`, both are `{start:{45,4},end:{45,21}}` (the name
   only), whereas `documentSymbol.range` for the same symbol is `{45,0}..{59,19}` (the whole
   body). **If the builder needs a source span it must come from `documentSymbol.range`, not
   from the callHierarchy item.** This contradicts any assumption that the hierarchy item
   carries the full symbol extent.

4. **URI normalization is required.** `didOpen` was sent with
   `file:///D:/codex_Projects/Aurora/...` but pyright echoes back
   `file:///d%3A/codex_Projects/Aurora/...` (lowercased, percent-encoded drive colon) in
   diagnostics, references, and callHierarchy items. Naive string comparison of URIs drops
   every result. Normalize (decode + case-fold drive letter) before matching.

5. **`includeDeclaration: false` confirmed** (Q2 corollary): adds exactly 1 self entry when
   `true`. The doc is right.

6. **`selectionRange` confirmed as mandatory** (Q1/Q2): `range.start` silently returns 0
   refs with no error.

## Other operational findings

- **`textDocument/implementation` aside, nothing else needed a fallback.** `didOpen` on
  `base.py` produced exactly 1 `publishDiagnostics` notification with an **empty**
  diagnostics array (0 errors) — see `02_didopen_diagnostics.json`. A bounded wait for the
  notification is sufficient; no long warm-up needed for a clean file.
- **Server→client requests must be answered or pyright stalls.** The probe handles
  `client/registerCapability`, `window/workDoneProgress/create`, and
  `workspace/configuration` (returning `[{}]` per requested item). Aurora's client already
  does this; production must keep it.
- **SymbolKind values observed**: Class=5, Method=6, Function=12, Variable=13.

## Orphan-process check (Task 4)

The probe stops pyright via `shutdown`/`exit`, then `terminate()`, with an `atexit` handler,
`SIGINT`/`SIGTERM` handlers, and a final sweep. After a normal run:

```
$ tasklist | grep -iE "pyright|langserver"   -> (no output)
$ wmic process where "name='node.exe'" get ProcessId,CommandLine | grep langserver -> (no output)
```

**No pyright orphan remains.** Two unrelated `node.exe` processes were present during
testing (a `tsx` CLI harness and a browser launcher); these predate the probe and were not
touched. The final sweep matches only processes whose command line contains
`langserver.index.js`, so it cannot kill unrelated node apps.

The earlier crash-path run (before the method-name fix) *did* leave an orphan, confirming
the risk is real when an exception escapes before `stop()` — the `atexit` + signal + sweep
layers are necessary, not decorative.

## Files produced

| File | Contents |
|---|---|
| `01_initialize.json` | full initialize result + init params actually sent |
| `02_didopen_diagnostics.json` | raw publishDiagnostics notification(s) |
| `03_document_symbol.json` | 9 top-level symbols with ranges/selectionRanges |
| `04a_refs_by_range.json` | references at `range.start` → **0 / null** |
| `04b_refs_by_selection.json` | references at `selectionRange.start` → **25** |
| `04c_refs_include_declaration.json` | `includeDeclaration:true` → 26 (delta 1) |
| `05_prepare_call_hierarchy.json` | the CallHierarchyItem |
| `06_incoming_calls.json` | 10 incoming callers, full edge shape |
| `06b_outgoing_calls.json` | 7 outgoing calls (all stdlib/typeshed) |
| `07_implementation.json` | `-32601 Unhandled` (plain function) |
| `08_implementation_method.json` | `-32601 Unhandled` (abstract method) |
| `09_method_call_hierarchy.json` | method-level hierarchy works; dup `fromRanges` found |

## Could not determine

- **Whether a *decorated* function's `range.start` lands on the decorator or the `def`.**
  `safe_resolve_path` is undecorated. `range.start` is at col 0 of the `def` line; a
  decorator would likely push it up. Not probed — but this does not affect the rule, since
  `selectionRange.start` is correct regardless.
- **Dotted-call resolution quality** (e.g. `obj.method()` where the type is inferred). The
  observed method case used explicit `self`-based async methods and resolved fine, but no
  adversarial test (duck typing, `getattr`, re-exports) was run.
- **Whether `workspace/symbol` could replace documentSymbol for bulk indexing** — out of
  Phase 0 scope, not probed.
