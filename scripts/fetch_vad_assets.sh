#!/usr/bin/env bash
# 自动挡（浏览器端 VAD）需要的静态资源：从 npm 取，不要提交进仓库
# 用法: bash scripts/fetch_vad_assets.sh
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
WORK=$(mktemp -d)
cd "$WORK"
npm pack @ricky0123/vad-web onnxruntime-web
mkdir -p "$DIR/static/vad"
tar xzf vad-web-*.tgz --strip-components=1 -C "$DIR/static/vad" --wildcards 'package/dist/*'
mv "$DIR/static/vad/dist"/* "$DIR/static/vad/" 2>/dev/null || true
tar xzf onnxruntime-web-*.tgz --strip-components=1 -C "$WORK/ort" --wildcards 'package/dist/*'
cp "$WORK/ort/dist/ort.min.js" "$WORK/ort/dist/ort-wasm-simd-threaded.wasm" \
   "$WORK/ort/dist/ort-wasm-simd-threaded.mjs" "$DIR/static/vad/"
gzip -9 -c "$DIR/static/vad/ort-wasm-simd-threaded.wasm" > "$DIR/static/vad/ort-wasm-simd-threaded.wasm.gz"
echo "静态资源就位：$DIR/static/vad （含 wasm 的 .gz 预压缩版）"
