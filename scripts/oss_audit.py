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
  6. （--history）git 历史里出现过的敏感**文件名** + 历史提交里的**内容**
     （密钥/手机号/身份证/自定义敏感词）——历史泄露最难补救：
     改名、删文件、甚至重写分支都删不掉仍在 clone / fork / 快照里的旧提交。

退出码：0 = 没发现 BLOCKER；1 = 有 BLOCKER。
"""
from __future__ import annotations

import argparse
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

# 文档里的占位示例（sk-xxxxxxxx / YOUR_KEY / example）不该当密钥报
PLACEHOLDER_HINTS = ("example", "placeholder", "your", "xxx", "todo", "fake", "dummy",
                     "sample", "test", "自定义", "见 .env", "你的", "换成")


def looks_placeholder(matched: str, line: str) -> bool:
    s = (matched + " " + line).lower()
    if any(k in s for k in PLACEHOLDER_HINTS):
        return True
    body = re.sub(r"[^A-Za-z0-9]", "", matched)
    return bool(body) and len(set(body)) <= 2          # sk-aaaaaaa… 这种


def scan_text(text: str, hints: list[str]):
    """扫一段文本 → [(级别, 说明, 行号, 片段)]（当前树与历史内容共用）"""
    found = []
    for ln, line in enumerate(text.splitlines(), 1):
        for pat, desc, level in CONTENT_PATTERNS:
            m = re.search(pat, line)
            if m and not looks_placeholder(m.group(0), line):
                found.append((level, desc, ln, m.group(0)[:60]))
        for m in PUBLIC_IP.finditer(line):
            ip = m.group(0)
            if not PRIVATE_IP.match(ip):
                ctx = line.lower()      # 版本号(1.2.3.4)会误报：要求出现上下文关键词
                if any(k in ctx for k in ("host", "url", "ip", "http", "://", "addr", "server", "wss")):
                    found.append(("WARN", "公网 IP", ln, ip))
        for h in hints:
            if h and h in line:
                found.append(("BLOCKER", f"敏感词「{h}」", ln, line.strip()[:80]))
    return found


def scan_file(path: Path, hints: list[str]):
    try:
        return scan_text(path.read_text(encoding="utf-8", errors="ignore"), hints)
    except Exception:
        return []


def scan_history_content(root: Path, hints: list[str], max_blobs: int = 3000, max_bytes: int = 1_000_000):
    """扫 git 历史里所有提交涉及的内容（按对象去重）。返回 4 元组列表。"""
    try:
        out = subprocess.run(["git", "-C", str(root), "rev-list", "--objects", "--all"],
                             capture_output=True, text=True, timeout=180).stdout
    except Exception as e:
        return [("INFO", f"git 历史内容检查失败：{e}", 0, "")]

    objects: dict[str, str] = {}
    for line in out.splitlines():
        parts = line.split(" ", 1)
        if len(parts) == 2:
            objects.setdefault(parts[0], parts[1])

    hits, seen, tested = [], set(), 0
    for sha, path in objects.items():
        if tested >= max_blobs:
            hits.append(("INFO", f"历史对象超过 {max_blobs} 个，其余未扫（可加 --history-limit 提高）", 0, ""))
            break
        tested += 1
        try:
            blob = subprocess.run(["git", "-C", str(root), "cat-file", "-p", sha],
                                  capture_output=True, timeout=60).stdout
        except Exception:
            continue
        if not blob or len(blob) > max_bytes or b"\x00" in blob[:4096]:
            continue
        for level, desc, ln, frag in scan_text(blob.decode("utf-8", "ignore"), hints):
            key = (sha, desc, frag)
            if key in seen:
                continue
            seen.add(key)
            hits.append((level, f"历史内容 {path}（{sha[:7]}）{desc}", ln, frag))
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--history", action="store_true",
                    help="同时检查 git 历史的文件名与历史提交内容（密钥/手机号/敏感词）")
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
        # 6a. 历史里的文件名
        try:
            out = subprocess.run(["git", "-C", str(root), "log", "--all", "--name-only",
                                  "--pretty=format:"], capture_output=True, text=True, timeout=60).stdout
            for name in sorted(set(out.split())):
                nm = Path(name).name
                if nm.endswith((".example", ".sample", ".template", ".dist")) or ".example." in nm:
                    continue                      # 示例文件本来就该提交
                if any(re.search(pat, nm, re.I) for pat, _ in BLOCK_NAMES):
                    blockers.append(f"git 历史里出现过文件：{name}（历史泄露，需重写历史或换新仓库）")
        except Exception as e:
            infos.append(f"git 历史文件名检查失败：{e}")

        # 6b. 历史提交里的内容（密钥/手机号/敏感词）——只查文件名会漏掉"内容里写过真名"
        for level, desc, ln, frag in scan_history_content(root, hints):
            rec = f"{desc}" + (f"：{frag}" if frag else "")
            if level == "BLOCKER":
                blockers.append(rec)
            elif level == "WARN":
                warns.append(rec)
            else:
                infos.append(rec)

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
