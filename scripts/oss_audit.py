#!/usr/bin/env python3
"""开源前审查：扫一个目录（或 git 仓库），报告不该公开的东西。

用法:
    python3 oss_audit.py <目录> [--history] [--hints 词1,词2,...]

检查项：
  1. 高危文件名（.env / *.pem / *.key / 声纹 / 会话 / 数据库 / 凭据）
  2. 文件内容里的密钥、令牌、私钥、公网 IP、手机号、身份证、邮箱、家目录绝对路径
  3. 自定义敏感词（学校名、老师名、真名等，用 --hints 传）
  4. 大文件（>5MB，别直接进 git）
  5. .gitignore 是否存在、是否覆盖上面命中的敏感文件
  6. （--history）git 历史里是否出现过敏感文件名——历史泄露最难补救

退出码：0 = 没发现 BLOCKER；1 = 有 BLOCKER。
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__", ".mypy_cache", "dist", "build"}

BLOCK_NAMES = [
    (r"^\.env($|\.)", "环境变量文件（通常放密钥）"),
    (r"\.pem$|\.key$|\.p12$|\.pfx$", "证书/私钥"),
    (r"id_rsa|id_ed25519", "SSH 私钥"),
    (r"voiceprint", "声纹数据（生物特征）"),
    (r"^sessions?$", "会话历史目录"),
    (r"docs\.json$", "上传的资料内容"),
    (r"credential|secret|token|passwd|password", "凭据类文件名"),
]

CONTENT_PATTERNS = [
    (r"sk-[A-Za-z0-9]{16,}", "疑似 API Key（sk-…）", "BLOCKER"),
    (r"github_pat_[A-Za-z0-9_]{20,}", "GitHub 细粒度令牌", "BLOCKER"),
    (r"ghp_[A-Za-z0-9]{30,}", "GitHub 经典令牌", "BLOCKER"),
    (r"AKIA[0-9A-Z]{16}", "AWS Access Key", "BLOCKER"),
    (r"xox[baprs]-[A-Za-z0-9-]{10,}", "Slack 令牌", "BLOCKER"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "私钥正文", "BLOCKER"),
    (r"\b1[3-9]\d{9}\b", "手机号", "BLOCKER"),
    (r"\b\d{17}[\dXx]\b", "身份证号", "BLOCKER"),
    (r"\b[\w.+-]+@[\w-]+\.[A-Za-z]{2,}\b", "邮箱（npm 包名@版本 是误报）", "WARN"),
    (r"/home/(?!user\b|username\b)[a-z][\w-]*/", "家目录绝对路径（泄露用户名）", "WARN"),
    (r"\bssh\s+\w+@|scp\s+\S+@", "带主机的 ssh/scp 命令", "WARN"),
]

PUBLIC_IP = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
PRIVATE_IP = re.compile(r"^(?:10\.|127\.|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.|0\.0\.0\.0$|255\.)")


def scan_file(path: Path, hints: list[str], max_bytes: int = 2_000_000):
    """返回 [(级别, 说明, 行号, 片段)]"""
    found = []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return found
    lines = text.splitlines()
    for ln, line in enumerate(lines, 1):
        for pat, desc, level in CONTENT_PATTERNS:
            m = re.search(pat, line)
            if m:
                found.append((level, desc, ln, m.group(0)[:60]))
        for m in PUBLIC_IP.finditer(line):
            ip = m.group(0)
            if not PRIVATE_IP.match(ip):
                # 版本号(1.2.3.4 形式的依赖)会误报：要求出现上下文关键词
                ctx = line.lower()
                if any(k in ctx for k in ("host", "url", "ip", "http", "://", "addr", "server", "wss")):
                    found.append(("WARN", "公网 IP", ln, ip))
        for h in hints:
            if h and h in line:
                found.append(("BLOCKER", f"敏感词「{h}」", ln, h))
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--history", action="store_true", help="同时检查 git 历史里的文件名")
    ap.add_argument("--hints", default="", help="逗号分隔的敏感词（学校/老师/真名等）")
    args = ap.parse_args()

    root = Path(args.path).resolve()
    hints = [h.strip() for h in args.hints.split(",") if h.strip()]
    blockers, warns, infos = [], [], []

    for p in sorted(root.rglob("*")):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        rel = p.relative_to(root)
        # 示例/模板文件（.env.example 之类）本来就是要提交的，别误报
        is_sample = p.suffix.lower() in {".example", ".sample", ".template", ".dist"} or ".example." in p.name
        if p.is_dir():
            for pat, desc in BLOCK_NAMES:
                if re.search(pat, p.name, re.I):
                    blockers.append(f"目录 {rel}/ —— {desc}")
            continue
        if not is_sample and any(re.search(pat, p.name, re.I) for pat, _ in BLOCK_NAMES):
            blockers.append(f"文件 {rel} —— 名称命中高危模式（{p.stat().st_size:,}B）")
            continue
        size = p.stat().st_size
        if size > 5_000_000:
            warns.append(f"大文件 {rel} —— {size/1e6:.1f}MB（考虑 git-lfs 或排除）")
        if p.suffix.lower() in {".py", ".md", ".html", ".js", ".json", ".sh", ".txt", ".yaml", ".yml", ".example"}:
            for level, desc, ln, frag in scan_file(p, hints):
                rec = f"{rel}:{ln} —— {desc}：{frag}"
                (blockers if level == "BLOCKER" else warns).append(rec)

    gi = root / ".gitignore"
    if gi.exists():
        gi_text = gi.read_text(encoding="utf-8", errors="ignore")
        for key in (".env", "sessions", "voiceprint", "*.pem", "*.key"):
            if key not in gi_text:
                warns.append(f".gitignore 里似乎没排除 {key}")
    else:
        infos.append("没有 .gitignore 文件")

    if args.history and (root / ".git").exists():
        try:
            out = subprocess.run(["git", "-C", str(root), "log", "--all", "--name-only",
                                  "--pretty=format:"], capture_output=True, text=True, timeout=60).stdout
            for name in sorted(set(out.split())):
                nm = Path(name).name
                if nm.endswith((".example", ".sample", ".template", ".dist")) or ".example." in nm:
                    continue                      # 示例文件本来就该提交
                if any(re.search(pat, nm, re.I) for pat, _ in BLOCK_NAMES):
                    blockers.append(f"git 历史里出现过：{name}（历史泄露，需重写历史或换新仓库）")
        except Exception as e:
            infos.append(f"git 历史检查失败：{e}")

    print(f"\n===== 开源前审查：{root} =====")
    for title, items in (("🔴 BLOCKER（必须处理）", blockers), ("🟡 提示（建议处理）", warns), ("ℹ️ 其他", infos)):
        print(f"\n{title}　{len(items)} 项")
        for it in items[:60]:
            print("  -", it)
        if len(items) > 60:
            print(f"  …还有 {len(items)-60} 项")
    print(f"\n结论：{'❌ 还不能公开' if blockers else '✅ 没发现拦路问题（仍需人工过一眼 README 与大文件）'}")
    return 1 if blockers else 0


if __name__ == "__main__":
    sys.exit(main())
