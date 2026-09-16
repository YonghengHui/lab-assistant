#!/usr/bin/env bash
# pre-commit 钩子：提交前拦住密钥与个人信息（真名 / 学号 / 手机号 / 服务器 IP…）
#
#   安装：bash scripts/install-hooks.sh      临时跳过：git commit --no-verify
#
# 设计：模式与 scripts/oss_audit.py 共用一套（不重复维护正则）；
#       个人信息类靠 sensitive-words.txt（该文件在 .gitignore 里，只在你本机）。
set -uo pipefail
root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
cd "$root" || exit 0

staged=$(git diff --cached --name-only --diff-filter=ACM 2>/dev/null \
         | grep -vEi '\.(png|jpe?g|gif|webp|ico|wasm|onnx|zip|gz|pdf|woff2?|ttf)$' || true)
[ -z "$staged" ] && exit 0

SCAN_HINTS_FILE="$root/sensitive-words.txt" SCAN_FILES="$staged" python3 - <<'PY'
import importlib.util, os, subprocess, sys, pathlib

root = os.getcwd()
spec = importlib.util.spec_from_file_location("oss_audit", os.path.join(root, "scripts", "oss_audit.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

hints, hint_file = [], os.environ.get("SCAN_HINTS_FILE", "")
if hint_file and os.path.exists(hint_file):
    for line in open(hint_file, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#"):
            hints.append(line)

bad = []
for f in os.environ.get("SCAN_FILES", "").splitlines():
    try:
        data = subprocess.run(["git", "show", f":{f}"], capture_output=True, timeout=30).stdout
    except Exception:
        continue
    if not data or b"\x00" in data[:4096] or len(data) > 2_000_000:
        continue
    text = data.decode("utf-8", "ignore")
    for level, desc, ln, frag in mod.scan_text(text, hints):
        if level == "BLOCKER":
            bad.append(f"  {f}:{ln}  {desc}：{frag}")

if bad:
    print("\n🚫 提交被拦下——暂存内容里有不该公开的东西：\n")
    print("\n".join(bad[:25]))
    if not hints:
        print("\n（提示：还没有 sensitive-words.txt，真名/学号这类词查不到——"
              "复制 sensitive-words.example.txt 改名填上就好了）")
    print("\n处理：删掉/改掉这些内容再 commit；确认是误报可以 git commit --no-verify 跳过。\n")
    sys.exit(1)
PY
