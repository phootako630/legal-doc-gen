# 前端镜像：构建 React 静态文件，由 nginx 提供，并把 /api 反向代理到后端。构建上下文为仓库根目录。
#
# nginx 配置不打进镜像，由 docker-compose 挂载 deploy/nginx/（切换 http / https 无需重新构建）。
# 基础镜像与 npm 源可用构建参数换成国内镜像，见 deploy/compose.env.example。
ARG NODE_IMAGE=node:22-alpine
ARG NGINX_IMAGE=nginx:1.27-alpine

FROM ${NODE_IMAGE} AS build
ARG NPM_REGISTRY=https://registry.npmjs.org
WORKDIR /src
RUN npm install -g pnpm@10.33.0 --registry="${NPM_REGISTRY}"
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile --registry="${NPM_REGISTRY}"
COPY frontend/ ./
RUN pnpm build

FROM ${NGINX_IMAGE}
COPY --from=build /src/dist /usr/share/nginx/html
EXPOSE 80 443
