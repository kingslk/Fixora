# Fixora

任务驱动的 AI 代码修复平台。读取 GitLab 默认分支，生成并验证**虚拟修改**，等人确认后才创建修复分支和 commit。

## 现状

- 只对接 **GitLab**（API + git fetch），没有 GitHub。
- 分析阶段不写真实仓库；确认后才建分支和 commit。
- 网页设置页只展示 GitLab / 模型状态，密钥只来自环境变量。
- v1 **没有登录**，不要直接暴露到公网。
- 模型必须走 OpenAI-compatible **第三方网关**；默认关闭 Agents SDK tracing，避免把调用轨迹发到 OpenAI。不要为消除 warning 设置 `OPENAI_API_KEY`。

## 目录

- `backend/`：FastAPI、PostgreSQL、Redis/Dramatiq Worker、OpenAI Agents SDK
- `web/`：React + TypeScript + Vite
- `deploy/`：Linux systemd 示例

## 快速开始

需要 Python 3.12、uv、Node 22、pnpm，以及 PostgreSQL 与 Redis。仓库里的 `Dockerfile` **只提供**这两项基础设施；API、Worker、Web 在本机跑。

用 Docker（macOS 上的 Apple Container 把 `docker` 换成 `container` 即可）：

```bash
docker build -t fixora-infra:local .
docker volume create fixora-postgres-data
docker run -d \
  --name fixora-infra \
  -e POSTGRES_USER=fixora \
  -e POSTGRES_PASSWORD=fixora \
  -e POSTGRES_DB=fixora \
  --mount type=volume,source=fixora-postgres-data,target=/var/lib/postgresql/data \
  -p 5433:5432 \
  -p 6380:6380 \
  fixora-infra:local
```

宿主机 Postgres `5433`、Redis `6380`（容器内 Postgres 仍是 `5432`）。

```bash
cp backend/.env.example backend/.env
# 填写 FIXORA_GITLAB_* 与 FIXORA_MODEL_*
```

后端：

```bash
cd backend
uv sync --all-extras
uv run alembic upgrade head
uv run uvicorn fixora.main:app --reload
```

另开终端：

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

```bash
docker logs fixora-infra
docker stop fixora-infra
docker rm fixora-infra
```

## 配置

| 变量 | 说明 |
| --- | --- |
| `FIXORA_GITLAB_BASE_URL` / `FIXORA_GITLAB_TOKEN` | GitLab 地址与 token |
| `FIXORA_GITLAB_SSL_VERIFY` | 同时控制 GitLab API 与 bare cache 的 git fetch。自签证书可 `false`；连 gitlab.com 等公网实例请设 `true` |
| `FIXORA_GITLAB_CA_BUNDLE` | 可选，自签 CA 路径 |
| `FIXORA_MODEL_API_URL` | 兼容网关。可填根地址、`/v1`，或完整 `/chat/completions`、`/responses`；程序会规范化 |
| `FIXORA_MODEL_API_MODE` | `responses` 或 `chat_completions`，改后重启 API 和 Worker |
| `FIXORA_MODEL_SSL_VERIFY` | 自签网关可 `false`；公网网关请设 `true`。关校验不等于数据隔离 |
| `FIXORA_MODEL_TRACING_ENABLED` | 默认 `false`，不把 trace 发往 OpenAI |

首次保存页面登录态时会生成 `backend/data/.secret-key`（开发机）或 `FIXORA_DATA_ROOT` 下的同名文件。用于解密已存 Cookie/localStorage，不要删除或提交到 git。

## 生产

见 [`deploy/README.md`](deploy/README.md)。生产测试经 `systemd-run` 与 `fixora-runner` 用户执行；基础设施容器不跑仓库代码。反向代理只给可信网络，不要把无登录的 v1 挂到公网。
