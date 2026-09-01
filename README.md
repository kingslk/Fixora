# Fixora

Fixora 是一个任务驱动的 AI 代码修复平台。它读取 GitLab 默认分支，生成并验证虚拟修改，等待用户确认后才创建修复分支和 commit。

## 目录

- `backend/`：FastAPI、PostgreSQL、Redis/Dramatiq Worker、OpenAI Agents SDK。
- `web/`：React + TypeScript + Vite。
- `deploy/`：Linux systemd 配置示例。

## 本地开发

后端需要 PostgreSQL 与 Redis。Apple Container 只运行基础设施，API、Worker、Web 仍在 macOS 本机：

```bash
container build -t fixora-infra:local .
container volume create fixora-postgres-data
container run -d \
  --name fixora-infra \
  -e POSTGRES_USER=fixora \
  -e POSTGRES_PASSWORD=fixora \
  -e POSTGRES_DB=fixora \
  --mount type=volume,source=fixora-postgres-data,target=/var/lib/postgresql/data \
  -p 5433:5432 \
  -p 6380:6380 \
  fixora-infra:local
```

PostgreSQL 宿主机使用 `5433`、容器内保持官方默认 `5432`；Redis 容器和宿主机均使用 `6380`。复制环境配置并填写 `FIXORA_GITLAB_*` 与 `FIXORA_MODEL_*`：

```bash
cp backend/.env.example backend/.env
```

GitLab 与模型配置分别只读取 `backend/.env` 中的 `FIXORA_GITLAB_*`、`FIXORA_MODEL_*`；网页设置页只显示当前状态，不会修改这些配置。
与 MemLoci 一致，内网自签 GitLab 默认使用 `FIXORA_GITLAB_SSL_VERIFY=false`；该值同时控制 GitLab API 和 bare cache 的 Git fetch。
共享页面登录态首次写入时会自动生成 `backend/data/.secret-key`，无需配置环境变量；该文件用于解密已保存的 Cookie/localStorage，不要删除。
要避免把代码发送给 OpenAI，必须把 `FIXORA_MODEL_API_URL` 指向你的第三方 OpenAI-compatible 网关。URL 可填网关根地址、`/v1`，或完整的 `/chat/completions` / `/responses` endpoint；程序会统一拼接。通过 `FIXORA_MODEL_API_MODE=responses` 或 `FIXORA_MODEL_API_MODE=chat_completions` 切换协议，修改后重启 API 和 Worker。与 MemLoci 一致，`FIXORA_MODEL_SSL_VERIFY=false` 默认关闭 TLS 证书校验；它只解决自签证书连接问题，不提供数据隔离。
`FIXORA_MODEL_TRACING_ENABLED=false` 默认禁用 OpenAI Agents SDK Trace Exporter，第三方模型调用轨迹不会发往 OpenAI；不要为消除 warning 而设置 `OPENAI_API_KEY`。

启动后端：

```bash
cd backend
uv sync --all-extras
uv run alembic upgrade head
uv run uvicorn fixora.main:app --reload
```

另开终端启动 Worker：

```bash
cd backend
uv run dramatiq fixora.worker
```

前端：

```bash
cd web
pnpm install
pnpm dev
```

查看或停止基础设施：

```bash
container logs fixora-infra
container stop fixora-infra
container rm fixora-infra
```

生产测试命令仍通过 `systemd-run` 和专用 `fixora-runner` 用户执行；基础设施容器不执行仓库代码。
