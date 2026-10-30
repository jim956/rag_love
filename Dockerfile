# 后端镜像：Python 3.11 + uv + Node.js
#
# 需要 Node 是因为 app/graph/mcp.py 里的 MCP 工具由 `npx -y <pkg>` 以子进程方式启动，
# 容器里没有 node/npm 时这些工具会整体降级掉（启动只打 warning，不报错）。
#
# 分层顺序的原则：越少变的越靠上。
#   基础镜像 → uv → 依赖清单(uv.lock) → npm 包缓存 → 业务代码
# 改业务代码只会让最后一个 COPY 层失效，上面所有层直接命中构建缓存，
# 不联网、不重装依赖、不重新下 npm 包，部署基本是秒级。
# 反过来说：不要把 COPY . . 放到 npx 预热或 uv sync 前面，否则每次改代码
# 都会把后面的联网步骤一起作废，重新下载一遍。

# 只从 node 镜像里拷 node/npm/npx 三个可执行文件，避免在 Python 镜像里再装一遍 Node
FROM node:22-bookworm-slim AS node

FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:/app:$PATH"

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -sf /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && node --version && npm --version

# uv 从 PyPI 装。
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
RUN pip install --no-cache-dir --index-url "$PIP_INDEX_URL" uv && uv --version

# 非 root 运行；npx 需要一个可写的 HOME 来缓存下载的 npm 包
RUN useradd --create-home --uid 10001 app
WORKDIR /app

# ① 第三方依赖：只有 pyproject.toml / uv.lock 变了才重装
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# ② 预热 npx 缓存。首次启动时 MCP 下载 npm 包若超过 30 秒
# （app/graph/mcp.py 的 MCP_LOAD_TIMEOUT_SECONDS），该服务器的工具会被跳过。
# 这一步放在拷代码之前，缓存就跟着镜像固定下来了，后面改代码不会重跑。
# 构建机没有 npm registry 访问时这几步会失败（日志里有 warmup failed 一行），
# 但不影响镜像可用。
#
# 包名必须和 app/graph/mcp.py 里逐字一致：npx 的缓存目录 ~/.npm/_npx/<hash>
# 是由包名 spec 算出来的，写成 12306-mcp@0.3.10 会落到另一个目录，
# 预热的东西运行期根本命中不了（要固定版本得两边一起改）。
# 不写版本也能复现：_npx 里的目录一旦存在就不再重新解析，镜像里的版本
# 就固定在构建那一刻，和运行期用的必然是同一份。
# </dev/null 不能省：这些包是 stdio server，没有 stdin 时它会一直等到被 timeout 杀掉。
#
# 构建缓存只按指令文本判定，加上 `|| echo` 兜底后这一层总是「成功」的，
# 失败一次就会被永久复用。要强制重跑预热，把 NPM_WARM_EPOCH 改个值
# （docker build --build-arg NPM_WARM_EPOCH=2 ...），ARG 参与缓存键靠下面 echo 引用它。
ARG NPM_WARM_EPOCH=1

USER app
RUN echo "npx warmup epoch ${NPM_WARM_EPOCH}" \
    && { timeout 180 npx -y bing-cn-mcp </dev/null >/dev/null \
         || echo "warmup failed: bing-cn-mcp"; } \
    && { timeout 180 npx -y 12306-mcp </dev/null >/dev/null \
         || echo "warmup failed: 12306-mcp"; }

# ③ 业务代码：唯一每次部署都会变的层。
# 用 COPY --chown 一步到位，省掉之前 `RUN chown -R /app` 那一层
# （那层每次都会把 .venv 整个复制一遍，纯浪费）。
# 运行期不需要写 .venv，root 拥有它不影响使用。
COPY --chown=app:app . .

USER app

EXPOSE 8000

# 单 worker：对话图与 MCP 会话挂在 app.state 上，多 worker 会各自重复建立连接
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]