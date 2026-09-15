"""扩展包静态扫描。

这里刻意使用可解释的规则而不是把扫描结果交给模型：扩展是代码边界，
每条命中都必须能回到文件和行号。静态分析不能执行或隔离扩展，未知情况
必须保留为 `undetermined`，否则用户会把「没扫到」误读成「不存在」。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from backend.security_scanner import SECRET_PATTERNS, ScanFinding

_SKIP = {".git", "__pycache__", "node_modules", ".venv", "venv", ".pytest_cache"}
# npm 生命周期里**安装时自动执行**的钩子。用户还没运行任何东西，命令就已经跑了，
# 所以它们比依赖版本约束更值得单独告警。
_INSTALL_HOOKS = {"preinstall", "install", "postinstall", "prepare", "prepublish"}
_TEXT_EXTENSIONS = {
    ".c", ".cpp", ".css", ".go", ".h", ".html", ".java", ".js", ".json",
    ".jsx", ".kt", ".md", ".php", ".ps1", ".py", ".rb", ".rs", ".sh",
    ".swift", ".toml", ".ts", ".tsx", ".txt", ".yaml", ".yml",
}
_RULES = (
    ("remote_script", "远程脚本直接执行", re.compile(
        r"(?:curl|wget)\b[^\n|]*\|\s*(?:sh|bash|zsh)|"
        r"(?:iwr|invoke-webrequest)\b[^\n|]*\|\s*(?:iex|invoke-expression)", re.I), "critical"),
    ("sensitive_env", "读取敏感环境变量", re.compile(
        # 必须覆盖三种真实写法，实测各漏过一种：
        #   os.environ["VENDOR_API_KEY"]   Python，带引号（最常见）
        #   process.env.BUILD_SECRET       Node，**点号**访问（原规则只认 `[`）
        #   process.env["BUILD_SECRET"]    Node，带引号
        # 环境变量名以 _KEY/_TOKEN/_SECRET 结尾即视为敏感；同时允许前缀，
        # 因为 `VENDOR_API_KEY` 这种带厂商前缀的命名很常见。
        r"(?:os\.environ(?:\.get)?\s*\[\s*|getenv\s*\(\s*"
        r"|process\.env\s*(?:\.|\b\s*\[\s*)|\$\{?)"
        r"[\"']?[A-Za-z0-9_]*(?:_KEY|_TOKEN|_SECRET)\b", re.I), "high"),
    ("user_directory", "访问用户敏感目录", re.compile(
        r"(?:~[/\\]\.(?:ssh|aws|config)|expanduser\s*\(\s*[\"']~[/\\]\.(?:ssh|aws|config)|Path\.home\s*\(\s*\)\s*/\s*[\"']\.(?:ssh|aws|config))", re.I), "high"),
    ("dynamic_shell", "动态 Shell 或命令执行", re.compile(
        r"(?:\beval\s*\(|\bexec\s*\(|\bos\.system\s*\(|\bsubprocess\.[A-Za-z_]+\s*\([^\n)]*\bshell\s*=\s*True)", re.I), "high"),
    ("destructive_delete", "工作区外破坏性删除", re.compile(
        r"(?:rm\s+-[rf]{1,2}\s+|shutil\.rmtree\s*\()[\"']?(?:/|[A-Za-z]:[/\\]|~[/\\])", re.I), "critical"),
    ("exfiltration", "疑似外传文件或环境内容", re.compile(
        r"(?:requests?\.(?:post|put|patch)|urllib\.request\.urlopen|fetch\s*\(|httpx\.(?:post|put|patch))[\s\S]{0,240}(?:os\.environ|process\.env|open\s*\(|read_text\s*\(|read\s*\()", re.I), "critical"),
)


def _files(root: Path) -> list[Path]:
    """列出扩展文本文件；缺失路径不回退到当前目录，避免误扫整个仓库。"""
    if not root.exists() or not root.is_dir():
        return []
    return sorted(
        path for path in root.rglob("*")
        if path.is_file()
        and not any(part in _SKIP for part in path.parts)
        and (path.suffix.lower() in _TEXT_EXTENSIONS or path.name in {
            "Dockerfile", "Makefile", "requirements.txt", "package.json", "package-lock.json"
        })
    )


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeError):
        return None


def _finding(rule_id: str, message: str, severity: str, path: Path,
             root: Path, line: int, snippet: str) -> ScanFinding:
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        rel = path.name
    return ScanFinding(scanner="supply", severity=severity, filepath=rel,
                       line=line, message=message, rule_id=rule_id,
                       snippet=snippet.strip()[:200])


def _line_number(content: str, index: int) -> int:
    return content.count("\n", 0, index) + 1


def _dep_kind(path: Path) -> str:
    """判断是否是依赖清单，返回 "pip" / "npm" / ""。

    ⚠️ 必须按**后缀模式**匹配，不能要求文件名严格等于 `requirements.txt` /
    `package.json`。实测漏报：语料里的 `m18_requirements.txt`、
    `m19_package.json` 因为带前缀而完全没被检查 —— 而真实扩展包
    同样会给清单起各种名字（`requirements-dev.txt`、`dev-requirements.txt`、
    `constraints.txt`、`package-lock.json`…）。要求严格相等等于只覆盖
    最常见的恰好命名，把「换了个名字」变成「扫不到」，
    而用户会把这读成「这套依赖没问题」。
    """
    name = path.name.lower()
    if name.endswith(".json"):
        base = name[:-5]
        # 允许前缀（`m19_package` / `dev_package`）——用**子串**判断，
        # 不要用 `endswith("-package")`：分隔符可能是下划线或点，写死一个
        # 会让另一种命名静默漏检（实测踩过：m19_package.json 判成非清单）。
        return "npm" if "package" in base else ""
    if name.endswith(".txt") or name.endswith(".in"):
        base = name.rsplit(".", 1)[0]
        # 同时认单数 `requirement` 与 `constraint`：真实项目里两种都出现，
        # 只认复数会把单数命名静默变成「没有清单」（对抗检验实测漏过）。
        if any(k in base for k in ("requirement", "constraint", "deps", "dependencies")):
            return "pip"
    return ""


def _dependency_findings(path: Path, root: Path, content: str) -> list[ScanFinding]:
    """检查常见依赖清单；精确锁定版本不告警，模糊约束必须显式暴露。

    除版本约束外，还检查 npm 的 `scripts`：`postinstall` / `preinstall` /
    `install` 会在**安装时自动执行**，是把任意命令塞进用户机器的经典通道
    （规范 §6.5「远程脚本直接执行」的真实形态）。只看 `dependencies`
    会把这一类完全漏掉 —— 对抗检验实测漏过。
    """
    kind = _dep_kind(path)
    if not kind:
        return []
    candidates: list[tuple[int, str]] = []
    script_findings: list[ScanFinding] = []
    if kind == "pip":
        candidates = [(i, line.strip()) for i, line in enumerate(content.splitlines(), 1)]
    else:
        try:
            data = json.loads(content)
            for section in ("dependencies", "devDependencies", "optionalDependencies"):
                candidates.extend((1, f"{name}: {version}")
                                  for name, version in data.get(section, {}).items())
            for hook, cmd in (data.get("scripts") or {}).items():
                if hook.lower() in _INSTALL_HOOKS and isinstance(cmd, str):
                    script_findings.append(
                        _finding("install_script", f"安装钩子 {hook} 会自动执行命令",
                                 "high", path, root, 1, f"{hook}: {cmd}"))
        except (json.JSONDecodeError, AttributeError):
            return []
    findings = []
    for line, value in candidates:
        if not value or value.startswith(("#", "//", "-r ")):
            continue
        if kind == "pip":
            # ⚠️ 判据是「有没有 `==` 精确锁」而不是「版本号有几段」。
            # 此前写成 `==[0-9]+(?:\.[0-9]+){2}`，要求**三段**点分数字：
            #   requests==2.31.0  -> 只有两段 -> 被误判成「未固定」
            # 而 `requests==2.31.0` 恰恰是完全正确的锁定写法。
            # 后果是把**做对了的清单**报成风险（实测把 benign 样本判成误报），
            # 用户一旦发现误报就会一律放行 —— 比漏报更危险。
            # 只认 `==`（含 `===`）与 `~=` 不算锁。
            unpinned = not bool(re.search(r"===?\s*[0-9][0-9A-Za-z.\-+]*", value))
        else:
            version = value.rsplit(":", 1)[-1].strip().strip("\"'")
            unpinned = not bool(re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version))
        if unpinned:
            findings.append(_finding("unpinned_dependency", "依赖未固定版本", "medium",
                                     path, root, line, value))
    return script_findings + findings


def scan_extension(path: str | Path, description: str = "") -> list[ScanFinding]:
    """扫描一个扩展目录，返回可定位的风险发现。

    扫描不会导入模块、启动进程或访问网络；这保证测试离线且避免把审查器
    变成攻击面，但也意味着结果不是沙箱结论，未知语法只能诚实地降级。
    """
    root = Path(path) if path is not None else Path("")
    if path is None or not root.exists() or not root.is_dir():
        return [_finding("scan_error", "扩展路径不存在或不是目录", "high",
                          root, root, 0, str(path))]
    findings: list[ScanFinding] = []
    contents: list[tuple[Path, str]] = []
    for file in _files(root):
        content = _read(file)
        if content is None:
            findings.append(_finding("unreadable_file", "文件无法读取，结果不完整", "high",
                                     file, root, 0, file.name))
            continue
        contents.append((file, content))
        for rule_id, message, pattern, severity in _RULES:
            for match in pattern.finditer(content):
                findings.append(_finding(rule_id, message, severity, file, root,
                                         _line_number(content, match.start()), match.group(0)))
        for pattern, message, severity in SECRET_PATTERNS:
            if re.search(pattern, content, re.MULTILINE):
                line = next((i for i, text in enumerate(content.splitlines(), 1)
                             if re.search(pattern, text)), 1)
                findings.append(_finding("secret", message, severity, file, root, line,
                                         content.splitlines()[line - 1]))
        findings.extend(_dependency_findings(file, root, content))
    if description:
        prompt_pattern = re.compile(
            r"ignore\s+(?:all\s+)?previous|system\s+prompt|override\s+safety|"
            r"do\s+not\s+(?:tell|show)\s+(?:the\s+)?user|send\s+(?:the\s+)?(?:secret|token|key)", re.I)
        for match in prompt_pattern.finditer(description):
            findings.append(_finding("prompt_injection", "扩展说明含越权或隐藏指令",
                                     "high", root, root, _line_number(description, match.start()),
                                     match.group(0)))
    prompt_pattern = re.compile(
        r"ignore\s+(?:all\s+)?previous|system\s+prompt|override\s+safety|"
        r"do\s+not\s+(?:tell|show)\s+(?:the\s+)?user|send\s+(?:the\s+)?(?:secret|token|key)", re.I)
    for file, content in contents:
        for match in prompt_pattern.finditer(content):
            findings.append(_finding("prompt_injection", "扩展内容含越权或隐藏指令",
                                     "high", file, root, _line_number(content, match.start()),
                                     match.group(0)))
    aggregate = "\n".join(content for _, content in contents)
    if re.search(r"(?:requests?\.(?:post|put)|fetch\s*\()[\s\S]{0,400}(?:os\.environ|process\.env|read_text\s*\(|open\s*\()", aggregate, re.I):
        if not any(f.rule_id == "exfiltration" for f in findings):
            file = contents[0][0] if contents else root
            findings.append(_finding("exfiltration", "跨文件组合疑似外传文件或环境内容",
                                     "critical", file, root, 1, "跨文件静态组合"))
    return findings


def scan(path: str | Path, description: str = "") -> list[ScanFinding]:
    """`scan_extension` 的短别名，便于在加载器入口调用。"""
    return scan_extension(path, description)


__all__ = ["SECRET_PATTERNS", "ScanFinding", "scan", "scan_extension"]
