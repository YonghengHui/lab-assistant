#!/usr/bin/env bash
# 本地自检：语法 + 敏感信息扫描（改完代码跑一遍，和 CI 里跑的是同一套）
# 用法: bash scripts/check.sh
set -e
cd "$(dirname "$0")/.."

echo "== 语法检查 =="
python3 -m py_compile lab_server.py app.py && echo "  ✓ Python 语法 OK"
python3 - <<'PY'
import re, pathlib, subprocess, sys, tempfile, os
ok = True
for name in ("lab.html", "standalone.html", "index.html"):
    p = pathlib.Path(name)
    if not p.exists():
        continue
    html = p.read_text(encoding="utf-8")
    blocks = re.findall(r"<script>(.*?)</script>", html, re.S)
    if not blocks:
        print(f"  (跳过 {name}：没有内嵌脚本)")
        continue
    code = "\n".join(blocks)
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(code); tmp = f.name
    r = subprocess.run(["node", "--check", tmp], capture_output=True, text=True)
    os.unlink(tmp)
    if r.returncode == 0:
        print(f"  ✓ {name} 内嵌 JS 语法 OK（{len(code)} 字符）")
    else:
        ok = False
        print(f"  ✗ {name} 内嵌 JS 有语法错误:\n{r.stderr[:800]}")
sys.exit(0 if ok else 1)
PY

echo "== 敏感信息扫描（密钥 / 隐私 / git 历史）=="
python3 scripts/oss_audit.py . --history

echo
echo "全部通过 ✅  （有 BLOCKER 时上面的扫描会以非 0 退出）"
