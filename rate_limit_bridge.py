#!/usr/bin/env python3
"""
Claude Code ステータスラインスクリプト
~/.claude/settings.json の statusLine.command に登録して使う

Claude Code から stdin に JSON が渡され、
rate_limits データをキャッシュファイルに書き出す。
同時に Claude Code のステータスラインにも表示する。
"""
import json
import sys
import os
from datetime import datetime, timezone

_ZIKKOKEY_DIR = os.path.join(os.path.expanduser("~"), ".zikkokey")
os.makedirs(_ZIKKOKEY_DIR, exist_ok=True)
CACHE_FILE = os.path.join(_ZIKKOKEY_DIR, "rate_limits_cache.json")

try:
    data = json.load(sys.stdin)
except Exception:
    print("")
    sys.exit(0)

rate_limits = data.get("rate_limits", {})

# キャッシュファイルに書き出す（claude_input.py が定期的に読む）
try:
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "rate_limits": rate_limits,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }, f, ensure_ascii=False, indent=2)
except Exception:
    pass

# Claude Code のステータスラインに表示するテキストを出力
# キー名は five_hour / seven_day（Unixタイムスタンプ）
h5 = rate_limits.get("five_hour", {})
d7 = rate_limits.get("seven_day", {})

h5_pct = h5.get("used_percentage")
d7_pct = d7.get("used_percentage")

def fmt_reset(ts):
    """resets_at のUnixタイムスタンプを「MM/DD HH:MM」形式に変換"""
    if not ts:
        return "?"
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%m/%d %H:%M")
    except Exception:
        return "?"

parts = []
if h5_pct is not None:
    parts.append(f"5h:{h5_pct:.0f}% ↺{fmt_reset(h5.get('resets_at'))}")
if d7_pct is not None:
    parts.append(f"7d:{d7_pct:.0f}% ↺{fmt_reset(d7.get('resets_at'))}")

print("  ".join(parts) if parts else "")
