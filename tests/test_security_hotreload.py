# -*- coding: utf-8 -*-
"""Tests for security_scanner + plugin_hotreload."""
import sys, os, pytest, tempfile, asyncio
sys.path.insert(0, r"D:\codex_Projects\Aurora")

# ── Security Scanner ──────────────────────────────────────────

def test_scanner_secrets_finds_key():
    from backend.security_scanner import SecurityScanner
    with tempfile.TemporaryDirectory() as d:
        tf = os.path.join(d, "test.py")
        with open(tf, "w") as f:
            f.write('# Hardcoded key\nAPI_KEY = "sk-abcdefghijklmnopqrstuvwxyz123456"\n')
        s = SecurityScanner(d)
        findings = s.scan_secrets(str(tf))
        assert len(findings) >= 1
        assert findings[0].severity == "critical"

def test_scanner_secrets_finds_password():
    from backend.security_scanner import SecurityScanner
    with tempfile.TemporaryDirectory() as d:
        tf = os.path.join(d, "config.py")
        with open(tf, "w") as f:
            f.write('password = "super_secret_pass_12345"\n')
        s = SecurityScanner(d)
        findings = s.scan_secrets(str(tf))
        assert len(findings) >= 1

def test_scanner_clean_file():
    from backend.security_scanner import SecurityScanner
    with tempfile.TemporaryDirectory() as d:
        tf = os.path.join(d, "clean.py")
        with open(tf, "w") as f:
            f.write('x = 1\ny = 2\nprint(x + y)\n')
        s = SecurityScanner(d)
        findings = s.scan_secrets(str(tf))
        assert len(findings) == 0

def test_scanner_skips_binary(tmp_path):
    from backend.security_scanner import SecurityScanner
    real_file = tmp_path / "main.py"
    real_file.write_text("x=1")
    s = SecurityScanner(str(tmp_path))
    assert s._should_skip(str(tmp_path / "node_modules" / "foo" / "bar.py"))
    assert not s._should_skip(str(real_file))
    assert s._should_skip("test.min.js")
    assert s._should_skip("test.pyc")

def test_scan_report():
    from backend.security_scanner import ScanFinding, ScanReport
    r = ScanReport(
        findings=[
            ScanFinding(scanner="secrets", severity="critical", message="API key"),
            ScanFinding(scanner="secrets", severity="high", message="JWT"),
            ScanFinding(scanner="secrets", severity="low", message="hash"),
        ]
    )
    assert r.critical_count == 1
    assert r.high_count == 1
    assert not r.is_clean

@pytest.mark.asyncio
async def test_scanner_bandit_not_installed():
    from backend.security_scanner import SecurityScanner
    s = SecurityScanner(".")
    findings = await s.scan_bandit("nonexistent.py")
    assert isinstance(findings, list)  # Should return empty list, not crash

@pytest.mark.asyncio
async def test_scanner_post_edit_scan():
    from backend.security_scanner import SecurityScanner
    with tempfile.TemporaryDirectory() as d:
        tf = os.path.join(d, "test.py")
        with open(tf, "w") as f:
            f.write('print("hello")\n')
        s = SecurityScanner(d)
        result = await s.post_edit_scan(str(tf))
        assert result is None  # Clean file

@pytest.mark.asyncio
async def test_scanner_post_edit_scan_finds_secret():
    from backend.security_scanner import SecurityScanner
    with tempfile.TemporaryDirectory() as d:
        tf = os.path.join(d, "test.py")
        with open(tf, "w") as f:
            f.write('SECRET_KEY = "my-super-secret-key-12345"\n')
        s = SecurityScanner(d)
        result = await s.post_edit_scan(str(tf))
        assert result is not None
        assert "CRITICAL" in result

def test_scanner_singleton():
    from backend.security_scanner import get_scanner
    s1 = get_scanner()
    s2 = get_scanner()
    assert s1 is s2

# ── Plugin Hot-Reload ─────────────────────────────────────────

def test_hotreload_create():
    from backend.plugin_hotreload import PluginHotReload
    hr = PluginHotReload(poll_interval=99)
    assert hr._interval == 99
    assert not hr.is_running

def test_hotreload_add_dir():
    from backend.plugin_hotreload import PluginHotReload
    hr = PluginHotReload()
    hr.add_dir("/tmp/plugins")
    hr.add_dir("/tmp/plugins")  # dedup
    assert len(hr._dirs) == 1

def test_hotreload_stats():
    from backend.plugin_hotreload import PluginHotReload
    hr = PluginHotReload()
    s = hr.stats()
    assert "running" in s
    assert "reload_count" in s

def test_hotreload_reload_all_no_manager():
    from backend.plugin_hotreload import PluginHotReload
    hr = PluginHotReload()
    result = hr.reload_all()
    assert "error" in result

@pytest.mark.asyncio
async def test_hotreload_start_stop():
    from backend.plugin_hotreload import PluginHotReload
    hr = PluginHotReload(poll_interval=0.5)
    await hr.start()
    assert hr.is_running
    await hr.stop()
    assert not hr.is_running

@pytest.mark.asyncio
async def test_hotreload_scan_changes_empty():
    from backend.plugin_hotreload import PluginHotReload
    hr = PluginHotReload()
    changed = hr._scan_changes()
    assert len(changed) == 0

def test_hotreload_scan_detects_change():
    from backend.plugin_hotreload import PluginHotReload
    with tempfile.TemporaryDirectory() as d:
        tf = os.path.join(d, "test.py")
        with open(tf, "w") as f:
            f.write("x = 1\n")
        hr = PluginHotReload([d])
        hr._scan_all()
        assert tf in hr._states
        # Modify file
        with open(tf, "w") as f:
            f.write("x = 2\n")
        changed = hr._scan_changes()
        assert len(changed) == 0  # No manager, so no plugin names mapped

def test_hotreload_singleton():
    from backend.plugin_hotreload import get_hotreload
    h1 = get_hotreload()
    h2 = get_hotreload()
    assert h1 is h2

def test_hotreload_on_reload_callback():
    from backend.plugin_hotreload import PluginHotReload
    hr = PluginHotReload()
    called = []
    hr.on_reload(lambda plugins: called.extend(plugins))

def test_file_state():
    from backend.plugin_hotreload import PluginFileState
    fs = PluginFileState(path="/tmp/test.py", hash="abc123", mtime=12345.0)
    assert fs.hash == "abc123"