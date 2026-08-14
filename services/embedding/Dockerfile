# katana embedding —— 共享无状态向量化服务。
#
# 与 MCP 镜像**刻意分开**：onnxruntime + fastembed 有几百 MB 依赖，塞进 MCP 镜像
# 会让三个 MCP 各背一份用不上的推理栈。
#
# 模型在**构建期**烘进镜像：运行时零外网依赖（HF 挂了/断网都不影响起服务），
# 启动即就绪，且部署产物自包含。
FROM python@sha256:00faa2debb87529f9f0764e9491d8ba400a3678976616c3bd7cb193745ac20d1 AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/opt/venv PATH=/opt/venv/bin:$PATH
RUN python -m venv "$VIRTUAL_ENV"
COPY requirements.lock.txt /tmp/requirements.lock.txt
RUN pip install --require-hashes -r /tmp/requirements.lock.txt

# 构建期下载模型进镜像。--no-cache-dir 之外还要清 HF 的中间缓存，否则镜像里
# 会同时留下 HF blob 与 fastembed 解包后的两份。
ARG EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5
RUN python -c "\
from fastembed import TextEmbedding; \
m = TextEmbedding(model_name='${EMBEDDING_MODEL}', cache_dir='/opt/models'); \
print('warm:', len(list(m.embed(['ok']))[0]), 'dims')" \
 && rm -rf /root/.cache/huggingface

FROM python@sha256:00faa2debb87529f9f0764e9491d8ba400a3678976616c3bd7cb193745ac20d1 AS runtime

RUN groupadd --gid 10002 katana && \
    useradd --uid 10002 --gid 10002 --no-create-home --shell /usr/sbin/nologin katana

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder --chown=katana:katana /opt/models /opt/models
COPY app.py /opt/app/app.py

ENV PATH=/opt/venv/bin:$PATH \
    FASTEMBED_CACHE_PATH=/opt/models \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HUB_OFFLINE=1

USER katana
WORKDIR /opt/app
EXPOSE 80
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "80", "--workers", "1"]

ARG GIT_REVISION=unknown
LABEL org.opencontainers.image.title="katana-embedding" \
      org.opencontainers.image.description="Shared stateless embedding service (ONNX Runtime, OpenAI-compatible)" \
      org.opencontainers.image.source="https://github.com/Dandi007/katana" \
      org.opencontainers.image.revision="${GIT_REVISION}"
