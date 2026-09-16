#!/usr/bin/env bash
# 发布前检查：把改动推到公开仓库**之前**跑这一条。不通过就别推。
#
#   bash scripts/publish-check.sh
#
# 三层里它是"临门一脚"那层：① 语法自检 ② 密钥/隐私扫描（含 git 历史里的内容）
# 词表：sensitive-words.txt（已在 .gitignore，只在你本机；没有就用 .example 的提示）
set -uo pipefail
cd "$(dirname "$0")/.."
fail=0

echo "== 1/3 语法自检 =="
bash scripts/check.sh || fail=1

echo
echo "== 2/3 密钥与个人信息（含历史提交内容） =="
WORDS=""
if [ -f sensitive-words.txt ]; then
  WORDS=$(grep -v '^[[:space:]]*#' sensitive-words.txt | grep -v '^[[:space:]]*$' | paste -sd, - || true)
  n=$(printf '%s' "$WORDS" | tr ',' '\n' | grep -c . || true)
  echo "（已加载本机词表 sensitive-words.txt：$n 个词）"
else
  echo "⚠️ 没有 sensitive-words.txt —— 真名 / 学号 / 手机号这类**个人信息查不出来**。"
  echo "   跑一次 bash scripts/install-hooks.sh 生成，然后把你的词填进去。"
fi
python3 scripts/oss_audit.py . --history --hints "$WORDS" || fail=1

echo
echo "== 3/3 结论 =="
if [ "$fail" -eq 0 ]; then
  echo "✅ 可以推。（推送前再确认：这类改动该不该进公开仓库？）"
else
  echo "❌ 不要推 —— 上面有 BLOCKER，先解决掉。"
fi
exit $fail
