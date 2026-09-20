# 前端独立镜像：多阶段构建，产物由 nginx 托管并反代 /api（web-ui spec：前后端分离部署）。
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
# 官方镜像启动时用 envsubst 渲染 /etc/nginx/templates/*.template 到 conf.d/
COPY nginx.conf /etc/nginx/templates/default.conf.template
# 反代上游默认指向 compose 内的 api 服务，运行时可用 BACKEND_UPSTREAM 覆盖
ENV BACKEND_UPSTREAM=http://api:8000
# 边缘限流（limit_req_zone rate 必须是字面量，经 envsubst 渲染后注入）与
# 请求体上限；后者应与 API 的 MAX_REQUEST_BODY_MB 保持一致
ENV NGINX_API_RATE=30r/s
ENV NGINX_API_BURST=60
ENV NGINX_MAX_BODY_SIZE=10m
EXPOSE 80
