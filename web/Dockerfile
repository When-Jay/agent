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
EXPOSE 80
