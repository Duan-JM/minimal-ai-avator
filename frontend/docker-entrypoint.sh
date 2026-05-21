#!/bin/sh
# 在 nginx 启动前用环境变量渲染 config.js。
# 这样同一份镜像可以被不同环境复用，无需重新构建。
set -e

TEMPLATE="/usr/share/nginx/html/config.template.js"
TARGET="/usr/share/nginx/html/config.js"

if [ ! -f "${TEMPLATE}" ]; then
    echo "[render-config] template not found at ${TEMPLATE}, skip"
    exit 0
fi

: "${BACKEND_API_URL:=}"
: "${BACKEND_MEDIA_URL:=}"
: "${FRONTEND_ICE_SERVERS_JSON:=[]}"

export BACKEND_API_URL BACKEND_MEDIA_URL FRONTEND_ICE_SERVERS_JSON

# 只替换我们关心的变量，避免污染 ${...} 形式的 JS 模板字符串。
envsubst '${BACKEND_API_URL} ${BACKEND_MEDIA_URL} ${FRONTEND_ICE_SERVERS_JSON}' \
    < "${TEMPLATE}" > "${TARGET}"

echo "[render-config] wrote ${TARGET}:"
cat "${TARGET}"
