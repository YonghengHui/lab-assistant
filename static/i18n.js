/* 实验助手 · 多语言（中文 / English）
 *
 * 设计：以后加语言只要在这个文件里加一份表（ZH2EN 的兄弟表）+ 一条 RULES。
 * 两层：
 *   1) 静态界面：applyI18n() 走 DOM，把中文文本/placeholder/title/aria-label 换成目标语言；
 *   2) 动态消息：say()/sys() 出口统一过 tr()，用正则表把中文句式换成目标语言。
 */
(function () {
  const SUPPORTED = ['zh', 'en'];

  function pickLang() {
    const q = new URLSearchParams(location.search).get('lang');
    if (q && SUPPORTED.includes(q)) { try { localStorage.setItem('lab_lang', q); } catch (e) {} return q; }
    try { const s = localStorage.getItem('lab_lang'); if (s && SUPPORTED.includes(s)) return s; } catch (e) {}
    const n = (navigator.language || 'zh').toLowerCase();
    return n.startsWith('zh') ? 'zh' : 'en';
  }

  const LANG = pickLang();
  window.LAB_LANG = LANG;

  // ── 静态界面词典（中文 → 英文）──────────────────────────────
  const ZH2EN = {
    '实验助手': 'Lab Assistant',
    '就绪': 'Ready',
    '手动挡': 'Manual',
    '自动挡': 'Auto',
    '思考：无': 'Think: off', '思考：低': 'Think: low', '思考：高': 'Think: high', '思考：满': 'Think: max',
    '声纹：关': 'Voice ID: off', '声纹：只认我': 'Voice ID: me only',
    '宽松': 'Loose', '标准': 'Normal', '严格': 'Strict',
    '录声纹': 'Enroll voice', '测分数': 'Test score', '重置声纹': 'Reset voice',
    '在听': 'Listening', '视频：开': 'Video: on', '视频：关': 'Video: off',
    // 带 emoji 前缀的实际文本（DOM 里是整串匹配）
    '🎤 在听': '🎤 Listening', '🔇 已静音': '🔇 Muted',
    '🎥 视频：开': '🎥 Video: on', '🎥 视频：关': '🎥 Video: off',
    '📎 资料': '📎 Library', '⚙ 提示词': '⚙ Prompt', '🌐 联网：开': '🌐 Web: on',
    '🌐 联网：关': '🌐 Web: off', '💬 垫场：开': '💬 Filler: on', '💬 垫场：关': '💬 Filler: off',
    '🖥 投屏：开': '🖥 Screen: on', '🖥 投屏：关': '🖥 Screen: off',
    '＋ 新对话': '＋ New chat', '＋新实验': '＋ New group',
    '实验助手': 'Lab Assistant', '就绪': 'Ready',
    '资料': 'Library', '提示词': 'Prompt', '联网：开': 'Web: on', '联网：关': 'Web: off',
    '垫场：开': 'Filler: on', '垫场：关': 'Filler: off', '自适应': 'Adaptive',
    '投屏：开': 'Screen: on', '投屏：关': 'Screen: off',
    '上下文': 'History', '回溯': 'Recall',
    '0 轮': '0 turns', '2 轮': '2 turns', '4 轮': '4 turns', '6 轮': '6 turns',
    '12 轮': '12 turns', '20 轮': '20 turns', '自定义…': 'Custom…',
    '10 秒': '10 s', '30 秒': '30 s', '60 秒': '60 s',
    '实验资料': 'Course material',
    '当前实验：': 'Current experiment:',
    '＋新实验': '+ New group',
    '＋ 加资料（照片 / 视频 / PDF / Word / PPT）': '+ Add files (photo / video / PDF / Word / PPT)',
    '一次实验一组。只有「当前实验」的资料会喂给模型，避免混淆。':
      'One group per experiment. Only the current group is fed to the model.',
    '纸质指导书拍照就能上传': 'Photograph the manual and upload it',
    '（自动 OCR 成文字）；多页可以一次选多张。': ' (auto-OCR to text); select multiple pages at once.',
    '录一段视频也能传': 'You can also upload a video',
    '：自动抽 8 帧看画面，': ': samples 8 frames and',
    '并把你视频里的旁白转成文字': 'transcribes your narration',
    '——一边拍一边说最省事。': ' — narrate while filming, easiest.',
    '系统提示词': 'System prompt',
    '保存': 'Save', '恢复默认': 'Reset to default',
    '这里决定它"是谁、怎么回答"。改完立刻生效（新的一轮问答）。':
      'Defines who it is and how it answers. Takes effect on the next turn.',
    '＋ 新对话': '+ New chat',
    '还没有历史对话': 'No conversations yet',
    '这个实验还没有资料': 'No material in this group yet',
    '摄像头自动开 · 直接说话提问': 'Camera on · just speak to ask',
    '说话或打字…（空着发=发画面）': 'Speak or type… (send empty = send the frame)',
    '当前实验': 'Current group',
  };

  // ── 动态消息规则（正则 → 英文模板）──────────────────────────
  const RULES = [
    [/^摄像头已就绪（(\d+)×(\d+)）/, (m) => `Camera ready (${m[1]}×${m[2]})`],
    [/^摄像头已就绪/, () => 'Camera ready'],
    [/^摄像头打不开：没找到可用摄像头/, () => 'Camera failed: no camera found'],
    [/^摄像头打不开：没有摄像头权限，请在浏览器地址栏授权/, () => 'Camera failed: permission denied, allow it in the address bar'],
    [/^视频已开：1 帧\/秒（画面没变就不发，省流量；提问时自动带上）/, () => 'Video on: 1 fps (unchanged frames skipped; attached when you ask)'],
    [/^视频已关：提问不带画面（再点一下「🎥 视频：关」就开回来）/, () => 'Video off: questions carry no frame (tap again to re-enable)'],
    [/^📷 回带最近 (\d+) 秒的 (\d+) 张画面（含提问这一刻）/, (m) => `📷 Recalling last ${m[1]}s — ${m[2]} frames (incl. this moment)`],
    [/^📷 摄像头已关，用 (\d+) 秒前拍下的 (\d+) 张画面/, (m) => `📷 Camera off — using ${m[2]} frames captured ${m[1]}s ago`],
    [/^⚠️ 这轮没带画面（点左下相机图标开启）/, () => '⚠️ No frame this turn (enable the camera)'],
    [/^思考中…/, () => 'Thinking…'],
    [/^识别中…/, () => 'Transcribing…'],
    [/^处理视频中…/, () => 'Processing video…'],
    [/^屏幕共享已开：之后的提问和推流都带屏幕画面（再点一下关闭）/, () => 'Screen sharing on: questions and feed use the screen'],
    [/^屏幕共享已关：恢复用摄像头画面/, () => 'Screen sharing off: back to the camera'],
    [/^这个浏览器不支持屏幕共享.*/, () => 'This browser does not support screen sharing (try desktop Chrome)'],
    [/^画面回带窗口：提问时带上最近 (\d+) 秒的画面（说话前那段）/, (m) => `Recall window: last ${m[1]}s of video is attached to each question`],
    [/^已按上次设置恢复：/, () => 'Restored last settings: '],
    [/^自动挡已开：直接说话，说完停一下就行/, () => 'Auto mode on: just speak, pause when done'],
    [/^语音库加载失败（第 (\d+) 次）：/, (m) => `Voice-lib load failed (attempt ${m[1]}): `],
    [/^语音库没下下来.*/, () => 'Voice lib download failed (needs ~11MB engine; retry later). Manual mode unaffected.'],
    [/^（不是你的声音，已忽略）/, () => '(not your voice, ignored) '],
    [/^没听清（可打字）/, () => 'Did not catch that (you can type)'],
    [/^缩放 (\d+)×（镜头原生）——发出去的画面就是放大后的/, (m) => `Zoom ${m[1]}× (native lens) — the sent frame is zoomed too`],
    [/^缩放 (\d+)×（裁切放大）——发出去的画面就是放大后的/, (m) => `Zoom ${m[1]}× (center crop) — the sent frame is zoomed too`],
    [/^视频处理中：抽帧 \+ 把你说的话转成文字（约 10 秒，([\d.]+)MB）…/, (m) => `Processing video: frames + your narration (~10s, ${m[1]}MB)…`],
  ];

  function tr(text) {
    if (LANG === 'zh' || typeof text !== 'string' || !text) return text;
    for (const [re, fn] of RULES) {
      const m = re.exec(text);
      if (m) return fn(m);
    }
    return ZH2EN[text] || text;
  }

  function applyI18n(root) {
    if (LANG === 'zh') return;
    const scope = root || document.body;
    // 文本节点
    const walker = document.createTreeWalker(scope, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    for (const n of nodes) {
      const key = n.nodeValue.trim();
      if (ZH2EN[key]) {
        n.nodeValue = n.nodeValue.replace(key, ZH2EN[key]);
      }
    }
    // 属性
    for (const el of scope.querySelectorAll('[placeholder],[title],[aria-label]')) {
      for (const attr of ['placeholder', 'title', 'aria-label']) {
        const v = el.getAttribute && el.getAttribute(attr);
        if (v && ZH2EN[v]) el.setAttribute(attr, ZH2EN[v]);
      }
    }
  }

  // 给外部用
  window.__tr = tr;
  window.__applyI18n = applyI18n;
  window.__i18nReady = true;

  // 页面加载后自动翻一遍（动态渲染的内容由调用方再调 applyI18n）
  document.addEventListener('DOMContentLoaded', () => applyI18n());
})();
