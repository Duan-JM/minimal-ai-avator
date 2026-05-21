# 前端独立部署镜像（nginx + 静态资源）。
# 在容器启动时通过 envsubst 把 BACKEND_API_URL / BACKEND_MEDIA_URL 注入 config.js，
# 这样同一份镜像可以被多个环境复用。
FROM nginx:1.27-alpine

RUN apk add --no-cache gettext

COPY frontend/static /usr/share/nginx/html
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY frontend/config.template.js /usr/share/nginx/html/config.template.js
COPY frontend/docker-entrypoint.sh /docker-entrypoint.d/40-render-config.sh
RUN chmod +x /docker-entrypoint.d/40-render-config.sh

ENV BACKEND_API_URL=""
ENV BACKEND_MEDIA_URL=""
ENV FRONTEND_ICE_SERVERS_JSON="[]"

EXPOSE 80
