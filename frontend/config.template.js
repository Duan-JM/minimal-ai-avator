// config.template.js — 容器启动时由 docker-entrypoint.sh 通过 envsubst 渲染成 config.js。
// 变量来源（在 frontend/Dockerfile 的 nginx 镜像里）：
//   BACKEND_API_URL          → apiBaseUrl
//   BACKEND_MEDIA_URL        → mediaBaseUrl（缺省回落到 apiBaseUrl）
//   FRONTEND_ICE_SERVERS_JSON → iceServers，必须是合法 JSON 数组字符串
window.APP_CONFIG = {
    apiBaseUrl: "${BACKEND_API_URL}",
    mediaBaseUrl: "${BACKEND_MEDIA_URL}",
    iceServers: ${FRONTEND_ICE_SERVERS_JSON}
};
