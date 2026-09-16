# 一起折腾指南（Contributing）

先说结论：**这里没有门槛** —— 改一行、提个 bug、说一句"我这么用它"都算贡献。
这是一个学生为自己的实验课做的工具（alpha 状态），作者的期望很简单：**能帮到别人的实验课**。

## 三种参与方式（挑最省事的）

1. **报 bug / 提需求**：开 issue，写清〔设备 / 浏览器〕〔你做了什么〕〔期望什么〕〔实际什么〕
2. **改代码**：先开个 issue 说一句"我要改 X"，避免撞车；改完直接 PR
3. **说你用它**：什么课、什么设备、帮没帮上忙 —— 这决定这个项目还要不要继续做

## 跑起来（两种形态，选一个）

**A. 单文件版（第一次上手推荐这个）**

```bash
# 先把 standalone.html 下载到当前目录
python3 -m http.server 8000     # 然后打开 http://localhost:8000/standalone.html
```

→ 点 ⚙️ 填 API Key → 手机/电脑打开（**要 https 或 localhost**，否则摄像头不可用）。
资料 / 对话 / 声纹全部存在浏览器本地（IndexedDB + localStorage）。

**B. 服务器版**

前置：`python3` + venv、系统装 **ffmpeg**（视频抽帧/转写）、**node + npm**（跑下面的资源脚本要用）。

```bash
git clone https://github.com/YonghengHui/lab-assistant && cd lab-assistant
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt   # 慢就加 -i 清华镜像
bash scripts/fetch_vad_assets.sh          # 自动挡需要的浏览器端 VAD 资源（要 npm）
cp .env.example .env && vi .env           # 填 API key（启动时自动读取 .env）
./venv/bin/python lab_server.py           # 默认 127.0.0.1:8901（由 LAB_HOST/LAB_PORT 决定）
```

手机要用摄像头得 **HTTPS**：在仓库根目录放一张自签证书（`cert.pem`/`key.pem`），没有就自动降级成 HTTP。

## 代码结构（哪里改什么）

| 文件 | 作用 |
|---|---|
| `standalone.html` | **无服务器单文件版**：全部逻辑在一个 HTML 里（IndexedDB 资料库、本地抽帧、浏览器里跑声纹、语音播报） |
| `lab_server.py` | 服务器版后端（单文件 aiohttp）：`/ask /stt /docs /frame /video_probe /enroll` …（TTS 音频随 `/ask` 响应返回，没有单独的 `/tts` 路由） |
| `lab.html` | 服务器版前端（手机浏览器页面） |
| `app.py` + `index.html` | `:8900` 实时语音页（Qwen-Omni-Realtime 中转） |
| `static/i18n.js` | 界面文案；**加一门语言 = 加一份表**（一次提交就能搞定） |
| `scripts/fetch_vad_assets.sh` | 自动挡（浏览器端 VAD）的资源获取 |
| `THIRD_PARTY_NOTICES.md` | 第三方组件许可证清单；**加新依赖时顺手更新它** |

## 提交之前

- **跑一遍自检**（语法 + 敏感信息扫描，和 CI 用的是同一套）：

  ```bash
  bash scripts/check.sh
  ```

  也可以单独跑扫描、加自己的关键词（比如真名、学校）：

  ```bash
  python3 scripts/oss_audit.py . --history
  python3 scripts/oss_audit.py . --hints "你的真名,你的学校"
  ```

  本机跑起来后，扫描会把**自己生成的** `.env` / 证书 / `sessions/` 报成 BLOCKER —— 这几样都在
  `.gitignore` 里，属正常，**只要别提交就行**。

- **GitHub Actions（可选）**：把 `scripts/ci-example.yml` 复制到 `.github/workflows/checks.yml` 就能在 push/PR 时自动跑上面这两步
  （GitHub 网页上加文件 1 分钟；**用 API/脚本推的话 GitHub 要求 token 带 `workflow` 权限**）。

- **前端改动自测**：起个静态服务 + 无头浏览器把流程跑一遍（这个仓库的改动就是这么验证的），
  在 PR 里说一句"我怎么验的"即可
- commit 信息一句话说清**为什么**改，中英文都行
- 大改动先开 issue 聊一下，别闷头写三天

## 已知的坑（欢迎顺手修）

- 单文件版语音播报用手机自带 TTS：不同浏览器音色差很多，可考虑接 API TTS
- 声纹阈值默认 `0.62`，是按作者的设备 + 环境调的；换手机可能要微调（设置里可改）
- 抽帧间隔按时长自适应（≤20s 每 2s 一帧 / ≤60s 每 4s / 更长每 8s，最多 12 帧；对话内视频最多 6 帧）：
  动作太快或视频太长会漏细节（可以做成"变化大就多抽"）
- 服务器版是轮次问答，延迟 3~9 秒；想要低延迟得接实时语音模型
- `file://` 直接打开时部分浏览器会拦摄像头/模块脚本 → 用 https 或 localhost

## 作者最想要的四件事

1. **接别的供应商**（OpenAI / Gemini / 本地 Ollama）—— 让没百炼账号的人也能用
2. **多语言界面** —— 加一份 `static/i18n.js` 表
3. **真实课堂的试用反馈** —— 哪怕只是一句"我们老师这么用被卡住了"
4. **弱网优化** —— 学校实验楼的网，懂的都懂

别客气，也不需要写得多漂亮。**能跑起来帮到人，比代码优雅重要。**
