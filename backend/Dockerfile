# 后端镜像：FastAPI + LangGraph，单进程 uvicorn。构建上下文为仓库根目录（需要 prompts/）。
#
# 中国大陆服务器拉 Docker Hub / PyPI 常失败或很慢：基础镜像与 pip 源都可用构建参数换成国内镜像，
# 见 deploy/compose.env.example 与 deploy/README.md。
ARG PYTHON_IMAGE=python:3.11-slim
FROM ${PYTHON_IMAGE}

ARG PIP_INDEX_URL=https://pypi.org/simple
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 非 root 运行：案件数据目录由该用户持有（代码会把目录收紧为 0700、文件 0600）
RUN useradd --system --uid 10001 --home-dir /srv --shell /usr/sbin/nologin app

WORKDIR /srv/backend
COPY backend/requirements.txt .
RUN pip install --index-url "${PIP_INDEX_URL}" -r requirements.txt

# prompt_loader 按「backend 的上一级 / prompts」查找模板，故放在 /srv/prompts
COPY prompts/ /srv/prompts/
COPY backend/app/ ./app/
COPY backend/scripts/ ./scripts/
# data/ 挂载为命名卷：新卷会继承这里的属主与权限
RUN mkdir -p data && chown app:app data && chmod 700 data

USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/', timeout=4).status == 200 else 1)"
# 必须单 worker：上传 / 分析进度、进行中的分析任务都在进程内存里
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
