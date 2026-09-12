"""实验助手 —— 极简 Demo 后端。

流程：拍照/提问 → 强推理模型（DeepSeek V4.1，带视觉）→ 文字答案 + 语音 mp3

设计取舍（跟实时语音页的区别）：
  - 这里追求"讲得透"，用慢但强的模型，不用实时语音那套浅答的
  - 语音只做输出（TTS），输入靠打字或浏览器原生识别

依赖：aiohttp（已有）
用法：python lab_server.py
"""
from __future__ import annotations

import asyncio
import base64
import glob
import json
import logging
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from collections import deque

import aiohttp
from aiohttp import web

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("lab")

HERE = Path(__file__).parent
HOST = os.environ.get("LAB_HOST", "127.0.0.1")
PORT = int(os.environ.get("LAB_PORT", "8901"))
CERT = HERE / "cert.pem"
KEY = HERE / "key.pem"

DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
ASR_KEY = os.environ.get("DASHSCOPE_API_KEY", "")
TAVILY_KEY = os.environ.get("TAVILY_API_KEY", "")
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL = os.environ.get("LAB_MODEL", "deepseek-v4.1-flash-expires-on-0910")
# 语音转文字用的模型（浏览器录音、视频旁白共用）
ASR_MODEL_NAME = os.environ.get("LAB_ASR_MODEL", "qwen3-asr-flash")

# 拍纸质指导书走的 OCR 视觉模型（.env 里改这两行即可换模型，不用动代码）
# 主模型读空/报错时会自动用兜底模型重试一次
OCR_MODEL = os.environ.get("LAB_OCR_MODEL", "deepseek-v4.1-flash-expires-on-0910")
OCR_FALLBACK = os.environ.get("LAB_OCR_FALLBACK", "qwen3.5-omni-plus")
OCR_PROMPT = ("这是一份实验指导书/讲义的照片，可能因为手抖、光线不好而有些模糊或倾斜。"
              "请把上面所有能看清的文字按原结构完整提取出来（标题、编号、公式、表格都保留），"
              "不要总结、不要解释、不要加自己的话，只输出提取到的文字本身，不要任何开场白或说明。"
              "看不清的地方写[看不清]。")

# 视频抽帧后的识读提示：既要看清设备/动作，也要把仪表读数、纸面文字读出来
VIDEO_PROMPT = ("这是实验课上用手机拍的短视频的一帧。用 3~6 行短句说清楚："
                "①画面里是什么设备/模块（看丝印文字）②正在做什么操作 ③仪表、屏幕、数码管上的读数或文字。"
                "有清晰文字或数据表就照抄读出来。看不清就说看不清，不要猜。")
VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".3gp")
VIDEO_MAX_FRAMES = int(os.environ.get("LAB_VIDEO_FRAMES", "12"))
# 多张图打包成一次请求时，要求它分页输出，好还原每页对应关系
MULTI_OCR_HINT = "\n若一次给了多张图，请按顺序分别输出，每张开头写【第 k 页】，不要合并成一段。"

# 实验背景资料：预载进系统提示词，让它一开口就懂这台设备
# 实验背景资料：放在 lab_context.md（仓库里只给 example，别把本校资料提交上去）
LAB_CONTEXT_FILE = HERE / "lab_context.md"
LAB_CONTEXT = (LAB_CONTEXT_FILE.read_text(encoding="utf-8")
               if LAB_CONTEXT_FILE.exists() else "")


SYSTEM = """你是实验课的助教，学生正站在实验台前，手机摄像头对着设备，你通过屏幕看着他的操作实时指导。

工作方式：
- 学生说话提问（语音转文字），同时附带当前摄像头画面。你既看画面，也听问题。
- 学生在动手操作，没空看屏幕，答案会被语音念出来。所以：
  要口语化、短句、按顺序说，避免表格、符号堆砌、大段代码。

回答结构（务必遵守）：
1. 第一句直接给结论/动作（"把功率计的量程拨到 2mW"）——学生只听这一句也能动手。
2. 再补关键原因（一句话）。
3. 如果涉及多个步骤，用"第一步…第二步…"口述式列出，每步一句话。
4. 如果画面看不清，直接说看不清哪里、让他把手机挪近/换个角度，不要猜。
5. 涉及读数时，明确告诉他读哪个数、单位是什么。

下面是学生实际使用的设备与实验清单，回答时直接假定他用的是这些：

""" + LAB_CONTEXT + """

若下面给了【联网资料】，那是刚搜到的实时信息，优先采信它；
引用时要口语化（"我查到…"），不要念网址。

约束：中文；控制在 200 字以内（要念出来，太长听着累）；不确定就说不确定，绝不编数据。"""

# 需要联网搜索的触发词（用户提到这些就说明要查具体信息）
SEARCH_HINTS = (
    "参数", "规格", "型号", "多少钱", "价格", "官网", "手册", "说明书",
    "百度", "搜一下", "查一下", "查查", "联网", "网上", "最新",
    "什么是", "定义", "原理是什么",
)


async def web_search(q: str, max_results: int = 4) -> str:
    """用 Tavily 搜一下，返回拼好的参考资料文本（失败返回空串）。"""
    if not TAVILY_KEY:
        return ""
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                "https://api.tavily.com/search",
                json={"api_key": TAVILY_KEY, "query": q, "max_results": max_results,
                      "search_depth": "basic", "include_answer": True},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as r:
                d = await r.json()
        parts = []
        if d.get("answer"):
            parts.append("速答：" + str(d["answer"])[:400])
        for it in (d.get("results") or [])[:max_results]:
            t = (it.get("title") or "")[:60]
            c = (it.get("content") or "")[:400]
            parts.append(f"- {t}：{c}")
        out = "\n".join(parts)
        log.info("search %r -> %d 条", q[:30], len(parts))
        return out
    except Exception as e:
        log.warning("search failed: %s", e)
        return ""


# ── 资料库（上传的指导书等，一次上传长期可用）─────────────────────────
DOCS_PATH = HERE / "docs.json"          # {"docs": [{"id","name","text","added","group"}]}
_docs = []
DOC_CHAR_LIMIT = 24000                  # 单次塞进提示词的总字数上限
DOC_ONE_LIMIT = 20000                   # 单个文档的字数上限


def load_docs():
    global _docs
    try:
        if DOCS_PATH.exists():
            _docs = json.load(open(DOCS_PATH)).get("docs", [])
            log.info("已加载 %d 份资料（共 %d 字）",
                     len(_docs), sum(len(d.get("text") or "") for d in _docs))
    except Exception as e:
        log.warning("资料加载失败: %s", e)


def save_docs():
    try:
        with open(DOCS_PATH, "w") as f:
            json.dump({"docs": _docs}, f, ensure_ascii=False)
    except Exception as e:
        log.warning("资料保存失败: %s", e)


def extract_text(path: str, filename: str) -> str:
    """从 PDF / docx / pptx / txt 里抽文字；图片类返回空（走视觉模型）。"""
    low = filename.lower()
    try:
        if low.endswith(".pdf"):
            import pymupdf
            doc = pymupdf.open(path)
            parts = []
            for i, page in enumerate(doc):
                parts.append(f"[第{i+1}页]\n" + page.get_text())
            return "\n".join(parts)
        if low.endswith(".docx"):
            import docx
            d = docx.Document(path)
            parts = [p.text for p in d.paragraphs if p.text.strip()]
            for t in d.tables:                     # 表格也要
                for row in t.rows:
                    cells = [c.text.strip() for c in row.cells if c.text.strip()]
                    if cells:
                        parts.append(" | ".join(cells))
            return "\n".join(parts)
        if low.endswith(".pptx"):
            from pptx import Presentation
            prs = Presentation(path)
            parts = []
            for i, slide in enumerate(prs.slides):
                texts = []
                for shape in slide.shapes:
                    if shape.has_text_frame and shape.text_frame.text.strip():
                        texts.append(shape.text_frame.text.strip())
                if texts:
                    parts.append(f"[第{i+1}页]\n" + "\n".join(texts))
            return "\n".join(parts)
        if low.endswith((".txt", ".md", ".csv")):
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
    except Exception as e:
        log.warning("解析 %s 失败: %s", filename, e)
    return ""


def _norm_img(path: str, max_side: int = 1600) -> str:
    """把图片缩放/转成 JPEG，返回 base64（控制体积，视觉模型也吃得住）。"""
    from PIL import Image
    import io
    im = Image.open(path)
    im = im.convert("RGB")
    w, h = im.size
    if max(w, h) > max_side:
        r = max_side / max(w, h)
        im = im.resize((int(w * r), int(h * r)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=82)
    return base64.b64encode(buf.getvalue()).decode()


async def _vl_multi(model: str, imgs: list[str], prompt: str) -> str:
    """一次请求里塞多张图（提示词只算一次 —— 实测比分开调用省 ~10% token、快 ~27%）。

    只在图片少（≤2 张）时用：输出要挤在同一个 max_tokens 里，张数一多会被截断。
    """
    ds = model.lower().startswith("deepseek")
    url = DEEPSEEK_URL if ds else "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    key = DEEPSEEK_KEY if ds else ASR_KEY
    content = [{"type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b}"}} for b in imgs]
    content.append({"type": "text", "text": prompt})
    body = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": 3000 * max(1, len(imgs)),
    }
    async with aiohttp.ClientSession() as s:
        async with s.post(url, headers={"Authorization": f"Bearer {key}"}, json=body,
                          timeout=aiohttp.ClientTimeout(total=180)) as r:
            d = await r.json()
    return (d["choices"][0]["message"]["content"] or "").strip()


async def _vl_once(model: str, img_b64: str, prompt: str = OCR_PROMPT) -> str:
    """调一次视觉模型做 OCR。deepseek* 走 DeepSeek 官方接口，其余走百炼。"""
    ds = model.lower().startswith("deepseek")
    url = DEEPSEEK_URL if ds else "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    key = DEEPSEEK_KEY if ds else ASR_KEY
    body = {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
            {"type": "text", "text": prompt}
        ]}],
        "max_tokens": 3000,
    }
    async with aiohttp.ClientSession() as s:
        async with s.post(url, headers={"Authorization": f"Bearer {key}"}, json=body,
                          timeout=aiohttp.ClientTimeout(total=120)) as r:
            d = await r.json()
    return (d["choices"][0]["message"]["content"] or "").strip()


async def video_transcript(path: str) -> str:
    """把视频里的**声音**转成文字——用户经常一边拍一边说（旁白比画面更说明问题）。

    取不到音轨 / ASR 失败都返回空串，不影响抽帧那条路。
    """
    wav = path + ".16k.wav"
    try:
        conv = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", path, "-vn", "-ar", "16000", "-ac", "1", "-f", "wav", wav,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await conv.wait()
        if not os.path.exists(wav) or os.path.getsize(wav) < 2000:
            return ""                                  # 没有音轨
        with open(wav, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        body = {"model": ASR_MODEL_NAME,
                "messages": [{"role": "user", "content": [
                    {"type": "input_audio",
                     "input_audio": {"data": f"data:audio/wav;base64,{b64}"}}]}]}
        async with aiohttp.ClientSession() as s:
            async with s.post(
                "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
                headers={"Authorization": f"Bearer {ASR_KEY}"}, json=body,
                timeout=aiohttp.ClientTimeout(total=180),
            ) as r:
                d = await r.json()
        txt = (d["choices"][0]["message"]["content"] or "").strip()
        log.info("视频旁白转写 %s -> %d 字", os.path.basename(path), len(txt))
        return txt
    except Exception as e:
        log.warning("视频旁白转写失败 %s: %s", path, e)
        return ""
    finally:
        try:
            os.unlink(wav)
        except Exception:
            pass


async def video_frames(path: str, max_frames: int = VIDEO_MAX_FRAMES) -> list[tuple[float, str]]:
    """用 ffmpeg 均匀抽帧，返回 [(秒, jpeg_base64)]。抽不到就返回空列表。"""
    import tempfile
    loop = asyncio.get_event_loop()

    def _dur() -> float:
        try:
            out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                  "-of", "default=nw=1:nk=1", path],
                                 capture_output=True, text=True, timeout=30).stdout.strip()
            return float(out or 0)
        except Exception:
            return 0.0

    dur = await loop.run_in_executor(None, _dur)
    if dur <= 0:
        dur = float(max_frames)            # 拿不到时长就按 1 帧/秒
    # 时长自适应间隔（工程界经验：短视频密、长视频稀；3 分钟约每 5s 一帧、15 分钟每 10s 一帧）
    step = 2.0 if dur <= 20 else (4.0 if dur <= 60 else 8.0)
    n = max(4, min(max_frames, int(dur / step) + 1))
    with tempfile.TemporaryDirectory() as td:
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", path,
               "-vf", f"fps=1/{step:.3f},scale='min(1024,iw)':-2",
               "-frames:v", str(n), "-q:v", "3", f"{td}/f_%03d.jpg"]
        try:
            await loop.run_in_executor(None, lambda: subprocess.run(
                cmd, capture_output=True, text=True, timeout=180))
        except Exception as e:
            log.warning("抽帧失败 %s: %s", path, e)
            return []
        files = sorted(glob.glob(f"{td}/f_*.jpg"))
        out = []
        for i, fp in enumerate(files):
            try:
                b = await loop.run_in_executor(None, lambda p=fp: _norm_img(p, max_side=1024))
                out.append((round(i * step, 1), b))
            except Exception:
                continue
        return out


async def ocr_image(path: str, filename: str) -> str:
    """用视觉模型把图片读成文字（用于拍纸质指导书）。
    主模型（OCR_MODEL，默认 DeepSeek V4.1 Flash）读空/报错时自动换兜底模型（OCR_FALLBACK）重试一次。"""
    try:
        img_b64 = await asyncio.get_event_loop().run_in_executor(None, lambda: _norm_img(path))
    except Exception as e:
        log.warning("OCR 图片预处理失败 %s: %s", filename, e)
        return ""
    for model in (OCR_MODEL, OCR_FALLBACK):
        if not model:
            continue
        try:
            txt = await _vl_once(model, img_b64)
        except Exception as e:
            log.warning("OCR %s 用 %s 失败: %s", filename, model, e)
            continue
        if len(txt) >= 20:
            log.info("OCR %s -> %d 字（%s）", filename, len(txt), model)
            return txt
        log.warning("OCR %s 用 %s 只读到 %d 字，换兜底模型", filename, model, len(txt))
    return ""


def images_to_pdf(paths: list[str], out: str):
    """多张照片合成一个 PDF（用户说的"整理成 PDF"）。"""
    import pymupdf
    doc = pymupdf.open()
    for p in paths:
        img = pymupdf.open(p)
        rect = img[0].rect
        page = doc.new_page(width=rect.width, height=rect.height)
        page.insert_image(rect, filename=p)
        img.close()
    doc.save(out)
    doc.close()
    return out


async def docs_merge(request: web.Request) -> web.Response:
    """把已经上传的图片资料合并成一个 PDF 文件（可下载/归档）。"""
    global _docs
    try:
        d = await request.json()
    except Exception:
        return web.json_response({"error": "参数错误"}, status=400)
    ids = d.get("ids") or []
    group = str(d.get("group") or "")
    picks = [x for x in _docs if x["id"] in ids and x.get("images")]
    if not picks:
        return web.json_response({"error": "选中的资料里没有图片"}, status=400)
    paths, tmpfiles = [], []
    try:
        for doc in picks:
            for b64s in doc["images"]:
                t = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
                t.write(base64.b64decode(b64s)); t.close()
                paths.append(t.name); tmpfiles.append(t.name)
        out = str(SESS_DIR.parent / f"merged_{int(time.time())}.pdf")
        images_to_pdf(paths, out)
        return web.json_response({"ok": True, "file": os.path.basename(out)})
    finally:
        for p in tmpfiles:
            try: os.unlink(p)
            except Exception: pass
async def docs_upload(request: web.Request) -> web.Response:
    """上传资料：multipart（group 指定实验分组，docname 指定合并名）。

    多张图片若带同一个 docname，会合并成一份资料（按页顺序），
    列表里就只显示一条，不会一页照片一条。
    """
    global _docs
    added = []
    grp = ""
    docname = ""
    img_pages = []          # [(页名, 文字)]，最后合并成一份
    pending = []            # [(文件名, 临时路径, 是否图片)]，先收齐再并行识别
    try:
        reader = await request.multipart()
        while True:
            part = await reader.next()
            if part is None:
                break
            if part.name == "group":
                grp = (await part.text()).strip()
                continue
            if part.name == "docname":
                docname = (await part.text()).strip()
                continue
            if part.name != "files":
                continue
            fn = part.filename or "未命名"
            suffix = os.path.splitext(fn)[1] or ".bin"
            tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
            while True:
                chunk = await part.read_chunk()
                if not chunk:
                    break
                tmp.write(chunk)
            tmp.close()
            is_img = fn.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".heic", ".bmp"))
            kind = "image" if is_img else ("video" if fn.lower().endswith(VIDEO_EXTS) else "doc")
            pending.append((fn, tmp.name, kind))

        # 并行识别：一次传多张照片时串行 OCR 要等好几倍（4 张约 20s → 约 6s）
        async def _read_one(fn, path, kind):
            try:
                if kind == "image":
                    return await ocr_image(path, fn)      # 照片走视觉模型 OCR
                if kind == "video":
                    # 视频：抽帧 → 逐帧描述（并行）+ 把视频里的旁白转成文字
                    frames, transcript = await asyncio.gather(
                        video_frames(path), video_transcript(path))
                    if not frames and not transcript:
                        return ""
                    out = []
                    if transcript:
                        out.append("【他自己说的旁白（视频里的声音，最重要，优先按它理解）】\n" + transcript)
                    if frames:
                        descs = await asyncio.gather(
                            *[_vl_once(OCR_MODEL, b, VIDEO_PROMPT) for _, b in frames],
                            return_exceptions=True)
                        blocks = []
                        for (sec, _b), d in zip(frames, descs):
                            d = "" if isinstance(d, Exception) else (d or "").strip()
                            if d:
                                blocks.append(f"【第 {sec:.0f} 秒的画面】\n{d}")
                        log.info("视频 %s 抽帧 %d 张、识读 %d 段、旁白 %d 字",
                                 fn, len(frames), len(blocks), len(transcript))
                        if blocks:
                            out.append("【画面逐段描述】\n" + "\n\n".join(blocks))
                    return "\n\n".join(out)
                return await asyncio.get_event_loop().run_in_executor(
                    None, lambda p=path, f=fn: extract_text(p, f))
            except Exception as e:
                log.warning("读取 %s 失败: %s", fn, e)
                return ""

        # 识别：1 张直接读；2 张打包成一次请求（省重复提示词）；3 张以上并行分开读
        # （3 张以上不打包：输出要挤在同一个 max_tokens 里，页数一多会被截断）
        img_idx = [i for i, p in enumerate(pending) if p[2] == "image"]
        texts = [""] * len(pending)
        if len(img_idx) == 2:
            bs = []
            for i in img_idx:
                try:
                    bs.append(await asyncio.get_event_loop().run_in_executor(
                        None, lambda p=pending[i][1]: _norm_img(p)))
                except Exception as e:
                    log.warning("预处理 %s 失败: %s", pending[i][0], e)
            if len(bs) == 2:
                try:
                    one = await _vl_multi(OCR_MODEL, bs, OCR_PROMPT + MULTI_OCR_HINT)
                    chunks = [c.strip() for c in re.split(r"【第\s*\d+\s*页】", one) if c.strip()]
                    log.info("OCR 打包 2 张 -> %d 字（%s）", len(one), OCR_MODEL)
                    if len(chunks) >= 2:
                        texts[img_idx[0]], texts[img_idx[1]] = chunks[0], chunks[1]
                    elif one:
                        texts[img_idx[0]] = one
                except Exception as e:
                    log.warning("打包 OCR 失败，退回分开：%s", e)
            for i in img_idx:
                if not texts[i]:
                    texts[i] = await _read_one(pending[i][0], pending[i][1], "image")
        elif img_idx:
            res = await asyncio.gather(*[_read_one(pending[i][0], pending[i][1], "image")
                                         for i in img_idx])
            for i, t in zip(img_idx, res):
                texts[i] = t
        rest = [i for i, p in enumerate(pending) if p[2] != "image"]
        if rest:
            res = await asyncio.gather(*[_read_one(pending[i][0], pending[i][1], pending[i][2])
                                         for i in rest])
            for i, t in zip(rest, res):
                texts[i] = t

        for (fn, tmp_path, kind), text in zip(pending, texts):
            try: os.unlink(tmp_path)
            except Exception: pass
            text = (text or "").strip()
            if not text:
                added.append({"name": fn, "ok": False,
                              "msg": "没读出内容（照片/视频是否太糊？拍清楚一点再试）"})
                continue
            import time as _t
            if kind == "image":
                img_pages.append((fn, text[:DOC_ONE_LIMIT]))
            else:
                t2 = text[:DOC_ONE_LIMIT]
                name = fn if kind == "doc" else (docname or f"视频：{fn}")
                doc = {"id": f"d{int(_t.time()*1000)}", "name": name, "text": t2,
                       "added": _t.time(), "chars": len(t2), "group": grp, "kind": kind}
                _docs.append(doc)
                added.append({"name": name, "ok": True, "chars": len(t2), "kind": kind})

        # 图片页合并成一份资料
        if img_pages:
            import time as _t
            merged_name = docname or (f"{len(img_pages)} 页照片资料")
            body = []
            for i, (pn, pt) in enumerate(img_pages, 1):
                body.append(f"\n\n===== 第 {i} 页（{pn}） =====\n{pt}")
            full = "".join(body).strip()[:DOC_ONE_LIMIT]
            doc = {"id": f"d{int(_t.time()*1000)}", "name": merged_name, "text": full,
                   "added": _t.time(), "chars": len(full), "group": grp,
                   "kind": "image", "pages": len(img_pages)}
            _docs.append(doc)
            added.append({"name": merged_name, "ok": True, "chars": len(full),
                          "kind": "image", "pages": len(img_pages)})
        save_docs()
        log.info("上传资料 %d 份", len(added))
        return web.json_response({"ok": True, "added": added, "docs": docs_brief()})
    except Exception as e:
        log.error("upload failed: %s", e)
        return web.json_response({"error": str(e)[:200]}, status=500)


def docs_brief():
    return [{"id": d["id"], "name": d["name"], "group": d.get("group") or "",
             "kind": d.get("kind") or "doc", "pages": d.get("pages") or 1,
             "chars": d.get("chars") or len(d.get("text") or "")}
            for d in _docs]


def _groups():
    return sorted({(d.get("group") or "") for d in _docs})


async def docs_list(request: web.Request) -> web.Response:
    b = docs_brief()
    return web.json_response({"docs": b, "groups": _groups(), "active": _active_group,
                              "total_chars": sum(d["chars"] for d in b)})


async def docs_activate(request: web.Request) -> web.Response:
    """切换当前生效的资料分组（一次实验一组）。"""
    global _active_group
    try:
        d = await request.json()
    except Exception:
        return web.json_response({"error": "参数错误"}, status=400)
    g = str(d.get("group") or "").strip()
    if g and g not in _groups():
        return web.json_response({"error": "没有这个分组"}, status=400)
    _active_group = g
    try:
        ACTIVE_PATH.write_text(g, encoding="utf-8")     # 记在磁盘上，重启后仍是这一组
    except Exception as e:
        log.warning("保存分组失败: %s", e)
    log.info("资料分组切换 → %r", g or "(全部)")
    return web.json_response({"ok": True, "active": g})


async def docs_get(request: web.Request) -> web.Response:
    """取单份资料的全文（页面里点开看内容用）。"""
    did = request.match_info.get("did", "")
    for d in _docs:
        if d["id"] == did:
            txt = d.get("text") or ""
            return web.json_response({"ok": True, "doc": {
                "id": d["id"], "name": d.get("name") or "",
                "group": d.get("group") or "",
                "chars": d.get("chars") or len(txt),
                "text": txt}})
    return web.json_response({"error": "没有这份资料"}, status=404)


async def docs_delete(request: web.Request) -> web.Response:
    global _docs
    did = request.match_info.get("did", "")
    before = len(_docs)
    _docs = [d for d in _docs if d["id"] != did]
    save_docs()
    return web.json_response({"ok": True, "removed": before - len(_docs)})


# 当前生效的资料分组（空串 = 全部）——落盘保存，重启/重进不要回到"全部"
ACTIVE_PATH = HERE / "active_group.txt"


def _load_active_group() -> str:
    try:
        return ACTIVE_PATH.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


_active_group = _load_active_group()
# 视频开关打开期间客户端推来的帧（只缓存，提问时才用；不调模型 = 零成本）
_live_frames: deque = deque(maxlen=6)


def docs_context(group: str | None = None) -> str:
    """把资料拼成给模型的上下文；只取当前分组（超限时取最新的）。"""
    if not _docs:
        return ""
    g = _active_group if group is None else group
    pool = [d for d in _docs if not g or (d.get("group") or "") == g]
    if not pool:
        return ""
    parts, used = [], 0
    for d in reversed(pool):               # 最新的优先
        t = d.get("text") or ""
        if used + len(t) > DOC_CHAR_LIMIT:
            t = t[: max(0, DOC_CHAR_LIMIT - used)]
        if not t:
            continue
        parts.append(f"【资料：{d['name']}】\n{t}")
        used += len(t)
        if used >= DOC_CHAR_LIMIT:
            break
    return "\n\n".join(reversed(parts))


# ── 提示词（用户可编辑）─────────────────────────────────────────────
PROMPT_PATH = HERE / "prompt.json"
DEFAULT_PROMPT = """你是实验课的助教，学生正站在实验台前，手机摄像头对着设备，你通过屏幕看着他的操作实时指导。

工作方式：
- 学生说话提问（语音转文字），同时附带当前摄像头画面。你既看画面，也听问题。
- 学生在动手操作，没空看屏幕，答案会被语音念出来。所以：
  要口语化、短句、按顺序说，避免表格、符号堆砌、大段代码。

回答结构（务必遵守）：
1. 第一句直接给结论/动作（"把功率计的量程拨到 2mW"）——学生只听这一句也能动手。
2. 再补关键原因（一句话）。
3. 如果涉及多个步骤，用"第一步…第二步…"口述式列出，每步一句话。
4. 如果画面看不清，直接说看不清哪里、让他把手机挪近/换个角度，不要猜。
5. 涉及读数时，明确告诉他读哪个数、单位是什么。

学生对设备不熟，说明要具体到"调哪个旋钮、看哪个读数、拧到什么程度"。

【重要】若下面提供了【实验资料】，**必须优先依据资料**来回答：
资料里怎么写的就怎么说，不要用自己的通用知识覆盖它；
资料里没写的，可以说"资料里没提，按一般做法是…"。

若给了【联网资料】，那是刚搜到的实时信息，优先采信；引用时口语化，不要念网址。

约束：中文；控制在 200 字以内（要念出来，太长听着累）；不确定就说不确定，绝不编数据。"""

_user_prompt = None
_history_limit = 6              # 带几轮历史给模型（0 = 不带）


def load_prompt():
    global _user_prompt, _history_limit
    try:
        if PROMPT_PATH.exists():
            d = json.load(open(PROMPT_PATH))
            _user_prompt = d.get("system")
            _history_limit = int(d.get("history_limit", 6))
    except Exception as e:
        log.warning("提示词加载失败: %s", e)


def save_prompt():
    try:
        with open(PROMPT_PATH, "w") as f:
            json.dump({"system": _user_prompt, "history_limit": _history_limit}, f,
                      ensure_ascii=False)
    except Exception as e:
        log.warning("提示词保存失败: %s", e)


DEFAULT_PROMPT_EN = """You are the teaching assistant for a lab course. The student is standing at the bench with the equipment in front of them; you are watching through the phone camera they are holding and guiding them in real time.

How you work:
- The student asks out loud (speech-to-text), and the current camera frame (and often the last minutes of footage) is attached. You both see and hear.
- Their hands are busy and they will not look at the screen; the answer is read aloud. So: short spoken sentences, in order, no tables, no symbol soup, no long code blocks.

Answer structure (follow it):
1. The first sentence is the conclusion or the action ("turn the power meter to 2 mW") - they can act on that alone.
2. Then one sentence of why.
3. For multi-step tasks, say "First... Second...", one sentence per step.
4. If the picture is unclear, say exactly what you cannot see and ask them to move closer or change the angle. Never guess.
5. For readings, say which number to read and in which unit.

Constraints: answer in English; keep it short (it gets spoken aloud); when unsure say so; never invent data."""


def current_prompt(lang: str = "zh") -> str:
    if _user_prompt:
        return _user_prompt
    return DEFAULT_PROMPT_EN if str(lang).lower().startswith("en") else DEFAULT_PROMPT


async def prompt_get(request: web.Request) -> web.Response:
    return web.json_response({"system": current_prompt(),
                              "is_default": _user_prompt is None,
                              "history_limit": _history_limit,
                              "default": DEFAULT_PROMPT})


async def prompt_set(request: web.Request) -> web.Response:
    global _user_prompt, _history_limit
    try:
        d = await request.json()
    except Exception:
        return web.json_response({"error": "参数错误"}, status=400)

    # 只改上下文深度（不动提示词）
    if "history_limit" in d and "system" not in d:
        try:
            n = int(d["history_limit"])
        except Exception:
            return web.json_response({"error": "轮数要是整数"}, status=400)
        _history_limit = max(0, min(60, n))
        save_prompt()
        log.info("上下文深度 → %d 轮", _history_limit)
        return web.json_response({"ok": True, "history_limit": _history_limit})
    if d.get("reset"):
        _user_prompt = None
        try: PROMPT_PATH.unlink()
        except Exception: pass
        return web.json_response({"ok": True, "system": DEFAULT_PROMPT, "is_default": True})
    s = str(d.get("system") or "").strip()
    if not s:
        return web.json_response({"error": "内容不能为空"}, status=400)
    if len(s) > 20000:
        return web.json_response({"error": "太长了（上限 2 万字）"}, status=400)
    _user_prompt = s
    save_prompt()
    log.info("提示词已更新 (%d 字)", len(s))
    return web.json_response({"ok": True, "system": s, "is_default": False})


async def index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(HERE / "lab.html")


# ── 历史会话（服务端存，只存文字）────────────────────────────────────
SESS_DIR = HERE / "sessions"
SESS_DIR.mkdir(exist_ok=True)
MAX_SESSIONS = 100


def _sess_path(sid: str):
    # 防目录穿越：只允许字母数字和短横线
    if not sid or not all(c.isalnum() or c in "-_" for c in sid):
        return None
    return SESS_DIR / f"{sid}.json"


def _list_sessions():
    items = []
    for p in SESS_DIR.glob("*.json"):
        try:
            d = json.load(open(p))
            msgs = d.get("messages") or []
            first_user = next((m["content"] for m in msgs if m.get("role") == "user"), "")
            items.append({
                "id": p.stem,
                "title": (d.get("title") or first_user or "新对话")[:24],
                "updated": d.get("updated") or p.stat().st_mtime,
                "count": len(msgs),
            })
        except Exception:
            continue
    items.sort(key=lambda x: x["updated"], reverse=True)
    return items[:MAX_SESSIONS]


async def sessions_list(request: web.Request) -> web.Response:
    return web.json_response({"sessions": _list_sessions()})


async def session_get(request: web.Request) -> web.Response:
    sid = request.match_info.get("sid", "")
    p = _sess_path(sid)
    if not p or not p.exists():
        return web.json_response({"error": "没有这个会话"}, status=404)
    try:
        return web.json_response(json.load(open(p)))
    except Exception as e:
        return web.json_response({"error": str(e)[:150]}, status=500)


async def session_save(request: web.Request) -> web.Response:
    """保存/更新一个会话（前端在每轮问答后调用）。"""
    try:
        d = await request.json()
    except Exception:
        return web.json_response({"error": "参数错误"}, status=400)
    sid = str(d.get("id") or "").strip()
    if not sid:
        import time as _t
        sid = f"s{int(_t.time() * 1000)}"
    p = _sess_path(sid)
    if not p:
        return web.json_response({"error": "非法 ID"}, status=400)
    # 只留文字，丢掉图片（省空间）
    msgs = []
    for m in (d.get("messages") or [])[-200:]:
        if m.get("role") in ("user", "assistant") and m.get("content"):
            msgs.append({"role": m["role"], "content": str(m["content"])[:4000]})
    import time as _t
    payload = {"id": sid, "title": str(d.get("title") or "")[:60],
               "messages": msgs, "updated": _t.time()}
    try:
        with open(p, "w") as f:
            json.dump(payload, f, ensure_ascii=False)
    except Exception as e:
        return web.json_response({"error": str(e)[:150]}, status=500)
    return web.json_response({"ok": True, "id": sid})


async def session_delete(request: web.Request) -> web.Response:
    sid = request.match_info.get("sid", "")
    p = _sess_path(sid)
    if p and p.exists():
        try:
            p.unlink()
        except Exception as e:
            return web.json_response({"error": str(e)[:150]}, status=500)
    return web.json_response({"ok": True})


# ── 声纹（sherpa-onnx + 3D-Speaker CAM++ advanced）─────────────────────
# 实测：advanced 版（与旧版同为 28MB）分离度更好——本人 0.74 / 别人 0.66 / 阈值 0.70
SPK_MODEL = HERE / "campplus_sv_advanced.onnx"
_spk_ext = None
_voiceprint = None            # 已注册的声纹（平均后的 list[float]）
_spk_threshold = float(os.environ.get("SPK_THRESHOLD", "0.62"))
_spk_consistency = None       # 注册时的自相似度（质量指标）
CFG_PATH = HERE / "voiceprint.json"


def get_spk():
    global _spk_ext
    if _spk_ext is None:
        if not SPK_MODEL.exists():
            raise RuntimeError(f"声纹模型不存在: {SPK_MODEL}")
        import sherpa_onnx
        cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(SPK_MODEL))
        _spk_ext = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)
        log.info("声纹模型已加载, dim=%d", _spk_ext.dim)
    return _spk_ext


def embed_wav(path: str):
    """从 16k 单声道 wav 提取 192 维声纹嵌入。返回 list[float] 或 None。"""
    import numpy as np
    import wave
    try:
        with wave.open(path, "rb") as w:
            if w.getnchannels() != 1 or w.getframerate() != 16000:
                return None
            data = w.readframes(w.getnframes())
        samples = np.frombuffer(data, dtype=np.int16).astype("float32") / 32768.0
        if len(samples) < 16000 * 0.6:          # 太短（<0.6s）不可靠
            return None
        ext = get_spk()
        s = ext.create_stream()
        s.accept_waveform(16000, samples)
        s.input_finished()
        return list(ext.compute(s))
    except Exception as e:
        log.warning("embed failed: %s", e)
        return None


def cosine(a, b) -> float:
    import numpy as np
    va, vb = np.asarray(a, "float32").ravel(), np.asarray(b, "float32").ravel()
    na, nb = np.linalg.norm(va), np.linalg.norm(vb)
    return float(np.dot(va, vb) / (na * nb)) if na and nb else 0.0


def to_16k_wav(src: str) -> str:
    """任意音频 → 16k 单声道 wav。"""
    dst = src + ".16k.wav"
    subprocess.run(["ffmpeg", "-y", "-i", src, "-ar", "16000", "-ac", "1", "-f", "wav", dst],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return dst


async def enroll(request: web.Request) -> web.Response:
    """注册声纹：支持一次提交多段录音（建议 3 段，取平均，比单段稳得多）。

    请求：JSON {"audios": ["<base64 webm>", ...]}  或单段原始音频（兼容旧写法）
    """
    global _voiceprint, _spk_consistency, _anchor, _adapt_count
    blobs = []
    ct = request.headers.get("Content-Type", "")
    if "application/json" in ct:
        try:
            payload = await request.json()
            blobs = [base64.b64decode(a) for a in (payload.get("audios") or []) if a]
        except Exception as e:
            return web.json_response({"error": f"参数错误: {e}"}, status=400)
    else:
        raw = await request.read()
        if raw:
            blobs = [raw]
    if not blobs:
        return web.json_response({"error": "没有音频"}, status=400)

    embs = []
    tmpfiles = []
    try:
        for i, b in enumerate(blobs):
            t = tempfile.NamedTemporaryFile(suffix=".webm", delete=False)
            t.write(b); t.close(); tmpfiles.append(t.name)
            wav = to_16k_wav(t.name)
            tmpfiles.append(wav)
            e = await asyncio.get_event_loop().run_in_executor(None, lambda p=wav: embed_wav(p))
            if e is not None:
                embs.append(e)
        if not embs:
            return web.json_response({"error": "录音太短或听不清，请每段说 2 秒以上"}, status=400)

        # 一致性：各段两两相似度（衡量录音质量）
        import numpy as np
        cons = None
        if len(embs) >= 2:
            sims = [cosine(embs[i], embs[j])
                    for i in range(len(embs)) for j in range(i + 1, len(embs))]
            cons = round(float(np.mean(sims)), 4)

        # 平均（先各自归一化再平均，避免某段音量过大的影响）
        arrs = [np.asarray(e, "float32").ravel() for e in embs]
        arrs = [a / (np.linalg.norm(a) + 1e-9) for a in arrs]
        avg = np.mean(arrs, axis=0)
        avg = avg / (np.linalg.norm(avg) + 1e-9)

        _voiceprint = [float(x) for x in avg]
        _anchor = list(_voiceprint)          # 注册锚点：自适应不许离它太远
        _adapt_count = 0
        _spk_consistency = cons
        save_cfg()
        log.info("声纹已注册: %d 段, %d 维, 一致性 %s", len(embs), len(_voiceprint), cons)
        return web.json_response({"ok": True, "dim": len(_voiceprint),
                                  "samples": len(embs), "consistency": cons,
                                  "hint": _cons_hint(cons)})
    finally:
        for p in tmpfiles:
            try: os.unlink(p)
            except Exception: pass


def _cons_hint(cons):
    if cons is None:
        return "只录了一段，建议再录两段提高稳定性"
    if cons >= 0.8:
        return "录音很一致，声纹质量好"
    if cons >= 0.65:
        return "录音基本一致，可用"
    return "各段差异较大（可能环境吵或音量不稳），建议重录"


def save_cfg():
    """把声纹和阈值落盘，重启不丢。"""
    try:
        with open(CFG_PATH, "w") as f:
            json.dump({"voiceprint": _voiceprint, "threshold": _spk_threshold,
                       "consistency": _spk_consistency, "anchor": _anchor,
                       "adapt_count": _adapt_count, "adapt_enabled": _adapt_enabled}, f)
    except Exception as e:
        log.warning("保存声纹失败: %s", e)


# ── 自适应更新 ────────────────────────────────────────────────────────
# 思路（业界叫 template adaptation）：
#   判定为本人、且分数明显高于阈值时，把这次的嵌入以小权重混进声纹。
#   用久了能适应环境/嗓子的慢变化（感冒、换房间）。
# 防跑偏三重保险：
#   ① 只有 score ≥ 阈值 + MARGIN 才纳入（模糊样本不要）
#   ② 混合权重很小（0.08），单次跑不远
#   ③ 与"注册锚点"的相似度低于 ANCHOR_FLOOR 就退回锚点（防止长歪）
_anchor = None
_adapt_count = 0
_adapt_enabled = True
ADAPT_W = 0.08
ADAPT_MARGIN = 0.05          # 要比阈值高这么多才纳入
ANCHOR_FLOOR = 0.60          # 与注册锚点的最低相似度
ADAPT_MAX = 500              # 最多累积这么多次，之后冻结（避免越飘越远）


def _norm(v):
    import numpy as np
    a = np.asarray(v, "float32").ravel()
    n = np.linalg.norm(a)
    return a / n if n else a


def maybe_adapt(emb, score):
    """把一个通过校验的嵌入按小权重混入声纹模板。"""
    global _voiceprint, _adapt_count
    if not _adapt_enabled or _anchor is None or emb is None:
        return
    if score is None or score < (_spk_threshold + ADAPT_MARGIN):
        return
    if _adapt_count >= ADAPT_MAX:
        return
    try:
        import numpy as np
        cur = _norm(_voiceprint)
        new = _norm(cur * (1 - ADAPT_W) + _norm(emb) * ADAPT_W)
        # ③ 防止长歪：离注册锚点太远就退回锚点
        if cosine(list(new), _anchor) < ANCHOR_FLOOR:
            log.warning("自适应漂移过大，回退到注册声纹")
            _voiceprint = list(_anchor)
            _adapt_count = 0
            save_cfg()
            return
        _voiceprint = [float(x) for x in new]
        _adapt_count += 1
        if _adapt_count % 10 == 0:            # 别每次都写盘
            save_cfg()
            log.info("声纹自适应已累积 %d 次", _adapt_count)
    except Exception as e:
        log.warning("adapt failed: %s", e)


async def set_adapt(request: web.Request) -> web.Response:
    """开关自适应更新。"""
    global _adapt_enabled, _adapt_count, _voiceprint
    try:
        d = await request.json()
    except Exception:
        return web.json_response({"error": "参数错误"}, status=400)
    if "enabled" in d:
        _adapt_enabled = bool(d["enabled"])
    if d.get("reset") and _anchor is not None:
        _voiceprint = list(_anchor)
        _adapt_count = 0
        log.info("声纹已重置回注册时的状态")
    save_cfg()
    return web.json_response({"ok": True, "enabled": _adapt_enabled, "count": _adapt_count})


def load_voiceprint():
    global _voiceprint, _spk_threshold, _spk_consistency, _anchor, _adapt_count, _adapt_enabled
    if not CFG_PATH.exists():
        return
    try:
        d = json.load(open(CFG_PATH))
        if isinstance(d, list):                    # 兼容旧格式（纯数组）
            _voiceprint = d
            _anchor = list(d)
        else:
            _voiceprint = d.get("voiceprint")
            _spk_threshold = float(d.get("threshold", _spk_threshold))
            _spk_consistency = d.get("consistency")
            _anchor = d.get("anchor") or (list(_voiceprint) if _voiceprint else None)
            _adapt_count = int(d.get("adapt_count") or 0)
            _adapt_enabled = bool(d.get("adapt_enabled", True))
        log.info("已加载声纹 (%s 维, 阈值 %.2f, 一致性 %s, 自适应累积 %d 次)",
                 len(_voiceprint) if _voiceprint else 0, _spk_threshold,
                 _spk_consistency, _adapt_count)
    except Exception as e:
        log.warning("声纹加载失败: %s", e)


async def set_threshold(request: web.Request) -> web.Response:
    """运行时调声纹灵敏度（不用重启）。"""
    global _spk_threshold
    try:
        d = await request.json()
        v = float(d.get("value"))
    except Exception:
        return web.json_response({"error": "参数错误"}, status=400)
    if not (0.1 <= v <= 0.95):
        return web.json_response({"error": "阈值应在 0.1~0.95"}, status=400)
    _spk_threshold = v
    save_cfg()
    log.info("声纹阈值 → %.2f", v)
    return web.json_response({"ok": True, "threshold": v})


async def test_score(request: web.Request) -> web.Response:
    """拿一段录音算当前声纹分数（用于校准阈值，不做拦截）。"""
    raw = await request.read()
    tmp = tempfile.NamedTemporaryFile(suffix=".webm", delete=False)
    tmp.write(raw); tmp.close()
    try:
        wav = to_16k_wav(tmp.name)
        emb = await asyncio.get_event_loop().run_in_executor(None, lambda: embed_wav(wav))
        if emb is None:
            return web.json_response({"error": "音频太短或听不清"})
        if _voiceprint is None:
            return web.json_response({"error": "还没注册声纹"})
        sc = cosine(emb, _voiceprint)
        return web.json_response({"score": round(sc, 4), "threshold": _spk_threshold,
                                  "match": sc >= _spk_threshold})
    finally:
        for p in (tmp.name, tmp.name + ".16k.wav"):
            try: os.unlink(p)
            except Exception: pass


async def health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "model": MODEL,
                              "voiceprint": _voiceprint is not None,
                              "threshold": _spk_threshold,
                              "consistency": _spk_consistency,
                              "adapt": {"enabled": _adapt_enabled, "count": _adapt_count}})


async def stt(request: web.Request) -> web.Response:
    """接收浏览器录音 → ①声纹校验 ②转文字。一次上传完成两件事。"""
    raw = await request.read()
    if not raw:
        return web.json_response({"error": "空音频"}, status=400)

    ct = request.headers.get("Content-Type", "audio/webm")
    ext = ".webm" if "webm" in ct else (".mp4" if "mp4" in ct else ".ogg")
    tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
    tmp.write(raw); tmp.close()

    wav = tmp.name + ".16k.wav"
    spk_ok, spk_score = True, None
    try:
        # 浏览器录的是 webm/opus，先转 16k 单声道 wav（声纹和 ASR 都用这个）
        conv = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", tmp.name, "-ar", "16000", "-ac", "1", "-f", "wav", wav,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await conv.wait()

        # ① 声纹校验（只有在已注册且开启时才算）
        if _voiceprint is not None and request.query.get("spk", "1") == "1":
            emb = await asyncio.get_event_loop().run_in_executor(
                None, lambda: embed_wav(wav))
            if emb is None:
                spk_ok = False
            else:
                spk_score = cosine(emb, _voiceprint)
                spk_ok = spk_score >= _spk_threshold
            if not spk_ok:
                log.info("声纹不匹配 (%.3f < %.2f) → 丢弃", spk_score or -1, _spk_threshold)
                return web.json_response({"text": "", "speaker_ok": False,
                                          "score": round(spk_score, 4) if spk_score else None})
            # 自适应更新（只有分数明显高于阈值才纳入，避免慢慢跑偏）
            maybe_adapt(emb, spk_score)

        import base64 as _b64
        with open(wav, "rb") as f:
            audio_b64 = _b64.b64encode(f.read()).decode()

        body = {
            "model": "qwen3-asr-flash",
            "messages": [{"role": "user", "content": [
                {"type": "input_audio", "input_audio": {"data": f"data:audio/wav;base64,{audio_b64}"}}
            ]}],
        }
        async with aiohttp.ClientSession() as s:
            async with s.post(
                "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
                headers={"Authorization": f"Bearer {ASR_KEY}"},
                json=body, timeout=aiohttp.ClientTimeout(total=60),
            ) as r:
                data = await r.json()

        text = ""
        try:
            text = (data["choices"][0]["message"]["content"] or "").strip()
        except Exception:
            pass
        if not text:
            log.warning("asr raw: %s", json.dumps(data)[:300])
        log.info("stt: %d bytes spk=%s(%.3f) -> %r", len(raw),
                 spk_ok, spk_score if spk_score is not None else -1, text[:50])
        return web.json_response({"text": text, "speaker_ok": spk_ok,
                                  "score": round(spk_score, 4) if spk_score is not None else None,
                                  "error": None if text else json.dumps(data)[:200]})
    except Exception as e:
        log.error("stt failed: %s", e)
        return web.json_response({"error": str(e)[:200]}, status=500)
    finally:
        for p in (tmp.name, wav):
            try:
                os.unlink(p)
            except Exception:
                pass


async def tts(text: str) -> str | None:
    """用 edge-tts 生成中文语音，返回 base64 mp3。"""
    try:
        import edge_tts
    except ImportError:
        log.warning("edge_tts 未安装，跳过语音")
        return None
    try:
        comm = edge_tts.Communicate(text, "zh-CN-XiaoxiaoNeural")
        buf = bytearray()
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                buf.extend(chunk["data"])
        return base64.b64encode(bytes(buf)).decode()
    except Exception as e:
        log.warning("tts failed: %s", e)
        return None


async def quick_ack(request: web.Request) -> web.Response:
    """延迟优化：主模型还在想的时候，先秒回一句短话顶上。

    只做 TTS，不调大模型 —— 目标是"问完马上有反应"。
    """
    try:
        d = await request.json()
        kind = str(d.get("kind") or "think")
    except Exception:
        kind = "think"
    # 备选短句（随机，避免每次都一样显得机械）
    import random
    phrases = {
        "think": ["嗯，我看一下。", "好的，我想想。", "稍等，我看看画面。", "嗯……"],
        "search": ["我查一下。", "稍等，我搜搜看。", "好，我找找资料。"],
        "busy": ["好，我接着看。", "嗯，明白。", "好，我在听。"],
    }
    text = random.choice(phrases.get(kind, phrases["think"]))
    audio = await tts(text)
    return web.json_response({"text": text, "audio": audio})


def _dedupe_frames(imgs: list[str], keep: int = 3, thresh: float = 6.0) -> list[str]:
    """去掉几乎相同的帧：同一画面连发 3 张等于白烧 token。

    用 16×16 灰度缩略图算平均绝对差，差得少就认为是同一画面（人没动/场景没变）。
    """
    import io
    from PIL import Image

    def sig(u: str):
        try:
            b = u.split(",", 1)[-1]
            im = Image.open(io.BytesIO(base64.b64decode(b))).convert("L").resize((16, 16))
            px = list(im.getdata())
            return px
        except Exception:
            return None

    out, last = [], None
    for u in imgs:
        s = sig(u)
        if s is None:                       # 解析不出来就留着，别丢信息
            out.append(u)
            continue
        if last is not None:
            diff = sum(abs(a - b) for a, b in zip(s, last)) / len(s)
            if diff < thresh:               # 和上一张几乎一样 → 跳过
                continue
        out.append(u)
        last = s
    return out[-keep:]


async def live_frame(request: web.Request) -> web.Response:
    """视频开关打开期间，客户端每几秒推一帧过来。

    这里**只缓存、不调模型**（成本为零），提问时自动用最近几帧当画面。
    """
    try:
        d = await request.json()
    except Exception:
        return web.json_response({"error": "bad json"}, status=400)
    img = (d.get("image") or "").strip()
    if img.startswith("data:"):
        img = img.split(",", 1)[-1]
    if len(img) < 100:
        return web.json_response({"error": "空帧"}, status=400)
    _live_frames.append((time.time(), img))
    return web.json_response({"ok": True, "n": len(_live_frames)})


async def ask(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except Exception:
        return web.json_response({"error": "bad json"}, status=400)

    question = (payload.get("question") or "").strip()
    image = payload.get("image")
    images = payload.get("images") or []
    if image and not images:
        images = [image]
    images = [i for i in images if i][:6]        # 最多 6 张（回带窗口用）
    if len(images) > 1:
        _before = len(images)
        images = _dedupe_frames(images)
        if len(images) != _before:
            log.info("画面去重 %d → %d 张（几乎没变的帧不发，省 token）", _before, len(images))
    # 摄像头关着也照样带画面：客户端把最后拍到的几帧发过来（stale_sec > 0 表示是"旧照"）
    stale_sec = int(payload.get("stale_sec") or 0)
    # 客户端一张都没带时，用服务端缓存的最近帧顶上（不再限制时间窗：关掉摄像头后仍要能问）
    if not images and _live_frames:
        _now = time.time()
        _recent = [(ts, b) for ts, b in _live_frames if _now - ts < 600]
        if _recent:
            images = ["data:image/jpeg;base64," + b for _ts, b in _recent[-3:]]
            stale_sec = int(_now - _recent[-1][0])
            log.info("ask: 用服务端缓存帧 %d 张顶上，最新一帧 %.1f 秒前", len(images), _now - _recent[-1][0])
    history = payload.get("history") or []

    if not question and not images:
        return web.json_response({"error": "没有内容" if zh else "empty request"}, status=400)

    content = []
    if question:
        content.append({"type": "text", "text": question})
    else:
        content.append({"type": "text", "text": "看看这个，告诉我该怎么做。" if zh else "Look at this and tell me what to do."})
    frames_span = int(payload.get("frames_span") or 0)
    if len(images) > 1:
        if zh:
            _span = f"，覆盖最近约 {frames_span} 秒" if frames_span else ""
            _tail = "，摄像头现在已经关了，这是最近拍到的几张" if stale_sec else "，最后一张是提问这一刻，前面几张是我说话前后那段时间的"
            content.append({"type": "text", "text": f"（下面按时间顺序给了 {len(images)} 张画面{_span}{_tail}，可据此判断我的操作有没有变化）"})
        else:
            _span = f", covering roughly the last {frames_span}s" if frames_span else ""
            _tail = ", the camera is off now - these are the most recent frames" if stale_sec else ", the last one is this moment and the earlier ones are from right before/while I spoke"
            content.append({"type": "text", "text": f"({len(images)} frames in chronological order{_span}{_tail}; use them to tell whether my operation changed.)"})
    elif images and stale_sec:
        content.append({"type": "text", "text": (
            f"（这张画面大约是 {stale_sec} 秒前拍的，摄像头现在已经关了——如果看不清就说看不清，让他重开摄像头）" if zh else
            f"(This frame was captured about {stale_sec}s ago and the camera is off now - if it is unclear, say so and ask them to re-enable the camera.)")})
    for im in images:
        content.append({"type": "image_url", "image_url": {"url": im}})

    # 联网搜索：开关打开 + 命中触发词 → 先搜再答
    search_used = False
    search_snippet = ""
    if payload.get("search", True) and question:
        q_low = question
        if any(h in q_low for h in SEARCH_HINTS):
            search_snippet = await web_search(question)
            search_used = bool(search_snippet)

    lang = str(payload.get("lang") or os.environ.get("LAB_LANG") or "zh").lower()
    zh = not lang.startswith("en")
    msgs = [{"role": "system", "content": current_prompt(lang)}]
    _docs_ctx = docs_context(payload.get("group"))
    if _docs_ctx:
        msgs.append({"role": "user", "content": ("【实验资料】（请优先依据这些资料回答）\n" if zh else
                                                 "[Course material] (base your answer on this first)\n") + _docs_ctx})
    _n = _history_limit * 2 if _history_limit else 0
    for h in (history[-_n:] if _n else []):      # 按设定带上最近几轮
        if h.get("role") in ("user", "assistant") and h.get("content"):
            msgs.append({"role": h["role"], "content": h["content"]})
    msgs.append({"role": "user", "content": content})
    if search_snippet:
        msgs.append({"role": "user", "content": "【联网资料】\n" + search_snippet})

    # 思考档四档：
    #   无  → thinking:{type:disabled}（不走思维链，最快）
    #   低/高/满 → thinking:{type:enabled} + reasoning_effort: low/high/max
    #   （官方说明：medium / xhigh 会被映射到 high）
    effort = (payload.get("reasoning") or "none").lower()
    if effort not in ("none", "low", "high", "max"):
        effort = "none"

    # 思考档开着时思维链会先吃掉预算：max_tokens 必须给足，否则思维链吃满 → 答案变成空字符串
    body = {
        "model": MODEL,
        "messages": msgs,
        "max_tokens": {"none": 800, "low": 2600, "high": 4000, "max": 6000}[effort],
        "temperature": 0.3,
    }
    if effort == "none":
        body["thinking"] = {"type": "disabled"}
    else:
        body["thinking"] = {"type": "enabled"}
        body["reasoning_effort"] = effort

    log.info("ask: q=%r img=%s", question[:60], bool(image))
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                DEEPSEEK_URL,
                headers={"Authorization": f"Bearer {DEEPSEEK_KEY}"},
                json=body,
                timeout=aiohttp.ClientTimeout(total=90),
            ) as r:
                data = await r.json()
    except Exception as e:
        log.error("model call failed: %s", e)
        return web.json_response({"error": f"模型调用失败：{e}"}, status=502)

    if "choices" not in data:
        return web.json_response({"error": json.dumps(data)[:300]}, status=502)

    _u = data.get("usage") or {}
    _reason = (_u.get("completion_tokens_details") or {}).get("reasoning_tokens")
    answer = (data["choices"][0]["message"]["content"] or "").strip()
    if not answer:
        # 别让前端一片空白：思维链吃满预算时明确告诉学生怎么办
        log.warning("ask: 空答案（out=%s reason=%s, effort=%s）",
                    _u.get("completion_tokens"), _reason, effort)
        answer = "（这次只出了思考、没给答案——把「思考」档调到“无”再问一次，或问短一点。）"
    log.info("ask: q=%r img=%d | in=%s hit=%s miss=%s out=%s reason=%s | 搜=%s",
             question[:40], len(images),
             _u.get("prompt_tokens"), _u.get("prompt_cache_hit_tokens"),
             _u.get("prompt_cache_miss_tokens"), _u.get("completion_tokens"),
             _reason,
             search_used)
    audio = await tts(answer)
    return web.json_response({"answer": answer, "audio": audio, "searched": search_used})


async def video_probe(request: web.Request) -> web.Response:
    """对话里直接发一段视频提问：抽 4 帧 + 转写旁白，发给前端当这一轮的内容（**不进资料库**）。

    和 /docs 上传的区别：这个是一次性的，不落盘、不占资料分组的上下文。
    """
    tmp_path = None
    try:
        reader = await request.multipart()
        while True:
            part = await reader.next()
            if part is None:
                break
            if part.name != "files":
                continue
            fn = part.filename or "video.mp4"
            suffix = os.path.splitext(fn)[1] or ".mp4"
            tf = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
            while True:
                chunk = await part.read_chunk()
                if not chunk:
                    break
                tf.write(chunk)
            tf.close()
            tmp_path = tf.name
            break
        if not tmp_path:
            return web.json_response({"error": "没收到视频"}, status=400)

        frames, transcript = await asyncio.gather(
            video_frames(tmp_path, max_frames=6), video_transcript(tmp_path))
        log.info("对话视频：抽帧 %d 张，旁白 %d 字", len(frames), len(transcript))
        if not frames and not transcript:
            return web.json_response({"error": "视频里既没读到画面也没读到声音"}, status=422)
        return web.json_response({
            "frames": ["data:image/jpeg;base64," + b for _sec, b in frames],
            "transcript": transcript,
        })
    except Exception as e:
        log.error("video_probe failed: %s", e)
        return web.json_response({"error": str(e)[:200]}, status=500)
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


async def vad_wasm(request: web.Request) -> web.Response:
    """ort 的 wasm 有 11MB，弱网下基本下不来 —— 有预压缩的 .gz 就发它（11MB → 2.9MB）。"""
    base = HERE / "static" / "vad"
    gz, raw = base / "ort-wasm-simd-threaded.wasm.gz", base / "ort-wasm-simd-threaded.wasm"
    if gz.exists() and "gzip" in (request.headers.get("Accept-Encoding") or ""):
        log.info("wasm 走 gzip（%.1fMB）", gz.stat().st_size / 1e6)
        return web.FileResponse(gz, headers={"Content-Encoding": "gzip",
                                             "Content-Type": "application/wasm"})
    return web.FileResponse(raw, headers={"Content-Type": "application/wasm"})


def ssl_ctx():
    if CERT.exists() and KEY.exists():
        import ssl
        c = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        c.load_cert_chain(CERT, KEY)
        return c
    return None


def main():
    if not DEEPSEEK_KEY:
        log.error("缺少 DEEPSEEK_API_KEY")
        return
    app = web.Application(client_max_size=32 * 1024 * 1024)
    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_post("/ask", ask)
    app.router.add_post("/stt", stt)
    app.router.add_post("/enroll", enroll)
    app.router.add_post("/threshold", set_threshold)
    app.router.add_post("/test_score", test_score)
    app.router.add_post("/adapt", set_adapt)
    app.router.add_get("/sessions", sessions_list)
    app.router.add_get("/sessions/{sid}", session_get)
    app.router.add_post("/sessions", session_save)
    app.router.add_delete("/sessions/{sid}", session_delete)
    app.router.add_post("/quick_ack", quick_ack)
    app.router.add_post("/frame", live_frame)
    app.router.add_post("/video_probe", video_probe)
    app.router.add_post("/docs", docs_upload)
    app.router.add_get("/docs", docs_list)
    app.router.add_get("/docs/{did}", docs_get)
    app.router.add_post("/docs/activate", docs_activate)
    app.router.add_post("/docs/merge", docs_merge)
    app.router.add_delete("/docs/{did}", docs_delete)
    app.router.add_get("/prompt", prompt_get)
    app.router.add_post("/prompt", prompt_set)
    # 先注册 wasm 的 gzip 直发路由，再挂静态目录（aiohttp 按注册顺序匹配）
    app.router.add_get("/vad/ort-wasm-simd-threaded.wasm", vad_wasm)
    app.router.add_get("/i18n.js", lambda r: web.FileResponse(HERE / "static" / "i18n.js",
                                                              headers={"Content-Type": "application/javascript"}))
    app.router.add_static("/vad/", HERE / "static" / "vad")
    load_voiceprint()
    load_docs()
    load_prompt()
    ctx = ssl_ctx()
    log.info("lab assistant on %s://%s:%d  model=%s",
             "https" if ctx else "http", HOST, PORT, MODEL)
    web.run_app(app, host=HOST, port=PORT, ssl_context=ctx, print=None, access_log=None)


if __name__ == "__main__":
    main()
