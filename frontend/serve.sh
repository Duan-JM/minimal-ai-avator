#!/usr/bin/env bash
# 仅托管 frontend/static 的开发用静态服务器，配合分离部署的后端使用。
# 用法：
#   ./frontend/serve.sh [port]
#
# 浏览器访问 http://127.0.0.1:<port>/index.html。
# 在访问前，编辑 frontend/static/config.js 把 apiBaseUrl 指向后端，例如：
#   window.APP_CONFIG = { apiBaseUrl: 'http://127.0.0.1:8010' };
set -euo pipefail

PORT="${1:-5173}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATIC_DIR="${HERE}/static"

if [ ! -d "${STATIC_DIR}" ]; then
    echo "未找到静态目录: ${STATIC_DIR}" >&2
    exit 1
fi

echo "Serving ${STATIC_DIR} on http://127.0.0.1:${PORT}"
echo "记得修改 ${STATIC_DIR}/config.js 的 apiBaseUrl 指向后端 API。"
exec python3 -m http.server "${PORT}" --directory "${STATIC_DIR}"
