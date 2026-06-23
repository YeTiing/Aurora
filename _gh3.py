import sys, urllib.request, json
sys.stdout.reconfigure(encoding="utf-8")

# Get key frontend source files
files_to_fetch = [
    "src/renderer/App.tsx",
    "src/renderer/components/chat/ChatInput.tsx",
    "src/renderer/components/chat/ChatPanel.tsx",
    "src/renderer/styles/global.css",
]
base = "https://raw.githubusercontent.com/liliMozi/openhanako/main"

for f in files_to_fetch:
    try:
        req = urllib.request.Request(f"{base}/{f}", headers={"User-Agent": "Aurora"})
        with urllib.request.urlopen(req, timeout=10) as r:
            content = r.read().decode()[:2000]
            print(f"\n===== {f} =====")
            print(content[:1500])
            if len(content) > 1500:
                print("...(truncated)")
    except Exception as e:
        print(f"\n===== {f} FAIL: {e} =====")