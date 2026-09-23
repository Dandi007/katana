# katana MCP servers —— memory / wiki / work-folder 三域的统一镜像。
#
# 一个镜像三个服务：三者共用 kernel 与 shared，同仓同版本发布，拆成三个镜像只会
# 让三份 kernel 版本漂移。区别只在 entrypoint 与 env（见 deploy/docker-compose.yml）。
#
# 数据面：三域 data root 是 **named volume**，不 bind 任何宿主路径——宿主命名空间
# 里根本不存在那些路径，`--outdir` 手滑与顺手 `git commit` 这两类事故结构性不可能。
# 详见 docs/constitution/002-data-plane-privacy.md。
#
# 容器内 data root 路径**刻意保持与封仓前一致**（/data/<domain>）：data repo 内部的
# manifest / ledger / flat-layout.json 可能记录过绝对路径，换路径等于给自己埋一类
# 难查的迁移故障。路径是实现细节，client 从来看不到，没有换的收益。
#
# 构建：docker build -f mcp/Dockerfile -t katana-mcp:<rev> .   （context = 仓根）

# ---------------------------------------------------------------------------
# builder —— 装依赖与本仓包，产出一个自足的 venv
# ---------------------------------------------------------------------------
FROM python@sha256:00faa2debb87529f9f0764e9491d8ba400a3678976616c3bd7cb193745ac20d1 AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/opt/katana/venv \
    PATH=/opt/katana/venv/bin:$PATH

RUN python -m venv "$VIRTUAL_ENV"

# 第三方依赖单独一层：只有 lock 变了才重装，改源码不触发。
# --require-hashes：lock 里每个包都带 sha256，供应链被换包会构建失败而不是静默装上。
COPY mcp/requirements.lock.txt /tmp/requirements.lock.txt
RUN pip install --require-hashes -r /tmp/requirements.lock.txt

# 本仓包最后装，且 --no-deps —— 依赖面已由上面的 lock 钉死，
# 这里不许再解析出计划外的包。
COPY mcp/shared      /src/mcp/shared
COPY mcp/kernel      /src/mcp/kernel
COPY mcp/memory      /src/mcp/memory
COPY mcp/wiki        /src/mcp/wiki
COPY mcp/work-folder /src/mcp/work-folder
COPY mcp/search      /src/mcp/search
RUN pip install --no-deps \
      /src/mcp/shared /src/mcp/kernel /src/mcp/search \
      /src/mcp/memory /src/mcp/wiki /src/mcp/work-folder

# ---------------------------------------------------------------------------
# runtime —— 只带 venv 与 git，不带编译器、不带源码树
# ---------------------------------------------------------------------------
FROM python@sha256:00faa2debb87529f9f0764e9491d8ba400a3678976616c3bd7cb193745ac20d1 AS runtime

# git 是硬依赖不是可选项：governed kernel 直接 exec git 二进制做事务提交
# （mcp/kernel/katana_kernel/gitops.py）。没有它三个服务全部起不来。
# tini 负责收养僵尸进程——kernel 会 fork 大量 git 子进程。
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends git tini; \
    rm -rf /var/lib/apt/lists/*

# git 配置写进 /etc/gitconfig 而不是 $HOME/.gitconfig：容器以只读 rootfs 跑，
# 没有可写的 HOME。identity 同时由 compose 的 GIT_* env 覆盖，双保险。
#
# 用 `git config --file` 而不是 printf 拼文本：它自己校验语法。手写 heredoc
# 踩过——`printf '%s'` 不展开 `\t`，写出的是字面反斜杠加 t，git 直接
# `fatal: bad config line`，整个镜像里 git 不可用。让 git 自己写 git 的配置。
RUN set -eux; \
    git config --file /etc/gitconfig user.name  'katana-mcp'; \
    git config --file /etc/gitconfig user.email 'katana-mcp@localhost'; \
    git config --file /etc/gitconfig safe.directory '*'; \
    git --version

# 非 root 运行。uid 固定 10001 —— named volume 的属主要与之对应，
# 迁移脚本 deploy/migrate-data-to-volumes.sh 会 chown 到同一个 uid。
RUN groupadd --gid 10001 katana \
 && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin katana

COPY --from=builder --chown=root:root /opt/katana/venv /opt/katana/venv

# wiki 与 work-folder 经 katana_kb_mcp_shared.config 调这个 bash shim 解析配置
# （env > .katana > 默认）。它不在 mcp/ 包里，而 shared/config.py 的路径推断假设
# editable 安装布局（从 config.py 上溯 4 层找 repo 根），site-packages 装法必然
# 落空 —— 真机演练里两个服务因此 crash-loop，memory 不用它所以没暴露。
# 故：拷进镜像固定路径 + 在**镜像层**钉死 env，而不是让每个 compose 服务各自记得。
COPY plugins/wiki/hooks/katana-config.sh /opt/katana/katana-config.sh

ENV PATH=/opt/katana/venv/bin:$PATH \
    KATANA_CONFIG_SH=/opt/katana/katana-config.sh \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# 三域 data root 的挂载点。属主必须是运行用户，否则首次写入 EACCES。
RUN mkdir -p /data/work-records /data/wiki /data/memory \
 && chown katana:katana /data/work-records /data/wiki /data/memory

USER katana
WORKDIR /data

# 具体跑哪个 server 由 compose 的 command 指定——本镜像不预设默认服务，
# 免得漏配 command 时静默起了个不期望的进程。
ENTRYPOINT ["/usr/bin/tini", "--"]

ARG GIT_REVISION=unknown
LABEL org.opencontainers.image.title="katana-mcp" \
      org.opencontainers.image.description="katana memory/wiki/work-folder MCP servers" \
      org.opencontainers.image.source="https://github.com/Dandi007/katana" \
      org.opencontainers.image.revision="${GIT_REVISION}" \
      org.opencontainers.image.licenses="UNLICENSED"
