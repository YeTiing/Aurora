"""Necessity —— Agent 改动质量保障系统（Aurora 内建模块）。

原为独立项目 D:\22ndCentury\necessity，按 INTEGRATION.md §1.1 的原设计
合并进 Aurora（它本就是「Aurora 的中间件」）。

⚠️ LSP 传输层**不再自带** —— 统一使用 backend/lsp/。
原先 Necessity 自带的 1273 行 LSP 实现已删除，其修复
（rootUri/workspaceFolders/processId + callHierarchy + documentSymbol）
已移植回 backend/lsp/server_manager.py 与 server_instance.py。
"""
