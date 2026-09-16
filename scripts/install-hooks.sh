#!/usr/bin/env bash
# 装本地 git 钩子（clone 下来跑一次就行）：以后每次 commit 自动扫密钥 / 个人信息。
#
#   bash scripts/install-hooks.sh
#
# 钩子是本地文件、不随仓库分发，所以**每台机器 / 每个 clone 都要装一次**。
set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p .git/hooks
cp templates/pre-commit-secret-guard.sh .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
echo "✅ 已装 .git/hooks/pre-commit（提交前自动扫密钥与个人信息）"

if [ ! -f sensitive-words.txt ]; then
  cp sensitive-words.example.txt sensitive-words.txt
  echo "📝 已生成 sensitive-words.txt（已在 .gitignore 里，不会提交）"
  echo "   把你的真名 / 学号 / 手机号 / 学校名 / 服务器 IP 等填进去，钩子才查得到。"
else
  echo "ℹ️ sensitive-words.txt 已存在，未改动。"
fi

echo
echo "临时跳过：git commit --no-verify"
