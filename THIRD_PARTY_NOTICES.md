# 第三方组件与许可证（Third-Party Notices）

本项目（实验助手 / Lab Assistant）自身以 **MIT** 许可发布（见 [`LICENSE`](LICENSE)）。
下面是它**使用 / 加载**的第三方组件及各自许可证。本项目**未修改**这些组件的源码。

许可证依据：各组件官方元数据（PyPI `dist-info` / npm registry / 官方仓库 LICENSE / ModelScope 模型页），复核日期 2026-09-16。

---

## 一、Python 依赖（`requirements.txt`，`pip install` 自动安装）

| 组件 | 用途 | 许可证 |
|---|---|---|
| aiohttp | 服务端 HTTP / WebSocket | Apache-2.0 AND MIT |
| websockets | WebSocket 服务 | BSD-3-Clause |
| numpy | 数值计算 | BSD-3-Clause（另含 0BSD / MIT / Zlib / CC0 组件） |
| pillow | 图片处理、合成 PDF | MIT-CMU |
| **edge-tts** | 语音播报（TTS） | **LGPL-3.0** ← 见「特别说明」 |
| sherpa-onnx | 声纹识别 | Apache-2.0 |
| pypdfium2 | PDF 文本提取 | BSD-3-Clause AND Apache-2.0 |
| python-docx | 读取 .docx 资料 | MIT |
| python-pptx | 读取 .pptx 资料 | MIT |

随之上装的传递依赖（均为宽松许可）：aiohappyeyeballs、typing_extensions（PSF-2.0）；aiosignal、frozenlist、multidict、propcache、yarl（Apache-2.0）；attrs、tabulate（MIT）；idna、lxml（BSD-3-Clause）；certifi（MPL-2.0）；xlsxwriter（BSD-2-Clause）。

## 二、前端运行库（浏览器里加载）

| 组件 | 用途 | 许可证 | 获取方式 |
|---|---|---|---|
| pdfjs-dist（PDF.js）3.11.174 | 单文件版读 PDF | Apache-2.0 | CDN（jsdelivr / bootcdn），仅 `standalone.html` |
| jszip 3.10.1 | 单文件版解压 .docx / .pptx | MIT（另有 GPL-3.0 双许可，本项目按 **MIT** 使用） | CDN（jsdelivr / bootcdn），仅 `standalone.html` |
| onnxruntime-web 1.18.0 | 浏览器内跑 VAD 模型 | MIT | CDN（`standalone.html`）+ `scripts/fetch_vad_assets.sh` 下载 |
| @ricky0123/vad-web 0.0.31 | 浏览器 VAD（自动挡） | ISC | `scripts/fetch_vad_assets.sh`（npm） |

> `scripts/fetch_vad_assets.sh` 会把 onnxruntime-web 与 vad-web 的发行文件下载到 `static/vad/`（**该目录不入库**，`.gitignore` 已排除）。
> 该产物里自带 `bundle.min.js.LICENSE.txt`；**如果你要再分发 `static/vad/` 这份产物，请把其中的许可证文件一并保留。**

## 三、模型

| 模型 | 用途 | 许可证 | 获取 |
|---|---|---|---|
| Silero VAD（silero_vad*.onnx） | 语音活动检测 | MIT | 随 `@ricky0123/vad-web` 包分发 |
| CAM++ 说话人确认（`iic/speech_campplus_sv_zh_en_16k-common_advanced`） | 声纹 | Apache-2.0 | ModelScope，**用户自行下载、不入库** |
| 3D-Speaker（CAM++ 上游项目） | 模型来源 | Apache-2.0 | 上游仓库 |

## 特别说明

1. **PyMuPDF 已移除**：早期版本用 PyMuPDF（AGPL-3.0 或商业授权）做 PDF 文本提取，与 MIT 许可存在冲突；现改用 **pypdfium2 + Pillow**，PDF 抽取功能保持不变。
2. **edge-tts 是 LGPL-3.0**：本项目把 edge-tts 当作**独立依赖**使用（用户 `pip install` 后由 Python import / 调用），**未修改其源码、也未将其打包进本仓库**。若你要把本项目与 edge-tts **打成一个包再分发**，请注意 LGPL-3.0 关于「可替换/再链接 + 提供对应源码」的要求，或换用其他 TTS。
3. **Apache-2.0 组件**（pdf.js、sherpa-onnx、pypdfium2、CAM++ 模型等）：再分发时需附带其许可证副本（<https://www.apache.org/licenses/LICENSE-2.0>）并保留其 NOTICE 文件（如有）。
4. **BSD / MIT / ISC 组件**：再分发时保留其版权与许可声明即可。
5. **你自己的内容不在此列**：你上传的资料（PDF / docx / pptx）、填的 API key、注册的声纹数据都属于你自己。

## 想自己再核一遍

```bash
# Python 包许可证（以安装后的元数据为准）
python3 -c "import importlib.metadata as m; print(m.metadata('aiohttp')['License-Expression'] or m.metadata('aiohttp')['Classifier'])"
# npm 包
curl -s https://registry.npmmirror.com/pdfjs-dist/latest | python3 -c "import sys,json;print(json.load(sys.stdin)['license'])"
# 官方仓库
curl -s https://api.github.com/repos/snakers4/silero-vad/license | python3 -c "import sys,json;print(json.load(sys.stdin)['license']['spdx_id'])"
```
