#!/usr/bin/env bash
# 自动挡（浏览器端 VAD）需要的静态资源：从 npm 取，不要提交进仓库
# 用法: bash scripts/fetch_vad_assets.sh   （需要本机有 node + npm）
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"

# 锁版本：不锁的话每次拿到的 onnxruntime-web 都是最新版（wasm 越出越大，文档里的体积数字也会漂）
VAD_VER="0.0.31"
ORT_VER="1.18.0"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
cd "$WORK"

npm pack "@ricky0123/vad-web@$VAD_VER" "onnxruntime-web@$ORT_VER"

mkdir -p "$DIR/static/vad" "$WORK/ort"
# npm 对 scoped 包生成的 tgz 名带 scope 前缀：ricky0123-vad-web-<ver>.tgz
tar xzf "ricky0123-vad-web-$VAD_VER.tgz" --strip-components=1 -C "$DIR/static/vad" --wildcards 'package/dist/*'
mv "$DIR/static/vad/dist"/* "$DIR/static/vad/" 2>/dev/null || true
rmdir "$DIR/static/vad/dist" 2>/dev/null || true

tar xzf "onnxruntime-web-$ORT_VER.tgz" --strip-components=1 -C "$WORK/ort" --wildcards 'package/dist/*'
# 各版本 dist 里的文件不完全一样（1.18.0 没有 .mjs），有的才拷
for f in ort.min.js ort-wasm-simd-threaded.wasm ort-wasm-simd-threaded.mjs; do
  [ -f "$WORK/ort/dist/$f" ] && cp "$WORK/ort/dist/$f" "$DIR/static/vad/"
done

# 页面加载的是 /vad/vad.min.js，而 npm 包里的 UMD 文件叫 bundle.min.js（全局名同样是 window.vad）
cp "$DIR/static/vad/bundle.min.js" "$DIR/static/vad/vad.min.js"

gzip -9 -c "$DIR/static/vad/ort-wasm-simd-threaded.wasm" > "$DIR/static/vad/ort-wasm-simd-threaded.wasm.gz"
echo "静态资源就位：$DIR/static/vad （含 wasm 的 .gz 预压缩版）"
