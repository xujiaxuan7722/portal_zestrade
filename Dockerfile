# ZesTrade 企业门户 — 单镜像构建（照 pm_system 部署口径：FastAPI 直接伺服前端静态文件）
# 构建：./docker/docker-build.sh   （自动透传 shell 里的代理变量，国内网络需要）
FROM python:3.13-slim

ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY
ARG http_proxy
ARG https_proxy
ARG no_proxy
# 国内直连 PyPI 慢时可换镜像源：--build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PIP_INDEX_URL=https://pypi.org/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt ./
RUN export HTTP_PROXY="${HTTP_PROXY:-$http_proxy}" \
    HTTPS_PROXY="${HTTPS_PROXY:-$https_proxy}" \
    NO_PROXY="${NO_PROXY:-$no_proxy}" && \
    python -m pip install -i "${PIP_INDEX_URL}" -r requirements.txt

COPY app /app/app
COPY frontend /app/frontend

COPY docker/entrypoint.sh /app/docker/entrypoint.sh
RUN chmod +x /app/docker/entrypoint.sh

EXPOSE 8200

ENTRYPOINT ["/app/docker/entrypoint.sh"]
