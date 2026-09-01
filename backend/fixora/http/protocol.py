"""Fixora HTTP 契约。

给写前端 / 对接 HTTP 的人看。实现在 `api.py`，响应形状在 `schemas.py`。
Worker 跑分析与提交；本文件只描述浏览器能打到的面。

前缀一律 `/api/v1`。JSON 失败走 FastAPI 真实 HTTP 状态 + `{"detail": "..."}`，
不是 e2e-agent 那套 `{code,result,message}` 包一层。截图、Markdown 轨迹、SSE
保持各自 Content-Type。

## 核心对象

- **Task**：稳定需求容器（描述、仓库、问题链接、至多一张问题截图）。一轮 Task 可有多次 Attempt。
- **Attempt**：一次完整的采集 / 定位 / 修改 / 验证 /（可选）提交。可重跑单位。
- **TaskView 顶层执行字段**（status、title、branch_name、change_sets 等）= **所查看的 Attempt**，
  不是 Task 行上的历史快照。`attempt_no` 是这次查看的编号；`current_attempt_no` 是正在指向的那次。
- `TaskView.image_*` 是 Task 级截图 metadata，`image_url` 指向截图文件；图片二进制不进数据库。

GitLab token 与模型密钥只来自进程环境变量 `FIXORA_GITLAB_*` / `FIXORA_MODEL_*`。
设置页只展示脱敏状态，不能写入这两类密钥。
`FIXORA_MODEL_API_URL` 可写网关根地址、`/v1`，或完整 `/chat/completions` / `/responses` endpoint；
`FIXORA_MODEL_API_MODE` 在 `responses` 与 `chat_completions` 间切换，修改后重启 API 和 Worker。

## HTTP 映射

    GET  /health
    GET  /settings/{gitlab|model|browser}     只读状态；gitlab/model 来自环境变量
    POST /settings/gitlab/test                用环境变量打 GitLab /user
    POST /settings/model/test                 用环境变量打网关
    PATCH /settings/browser                   唯一可写设置（采集超时/滚屏上限）

    GET  /browser-auth-profiles               无密文
    POST /browser-auth-profiles               raw Cookie / storage JSON；按 origin upsert
    DELETE /browser-auth-profiles/{id}

    GET  /repositories/discover?search=       GitLab 搜索或解析项目 URL
    GET  /repositories
    POST /repositories                        {gitlab_project_id}；已添加则原样返回
    POST /repositories/{id}/fetch             fetch 默认分支到 bare cache
    POST /repositories/{id}/detect-runtime    从 lockfile / package 推断
    PATCH /repositories/{id}/runtime          人工覆盖语言与测试命令

    POST /tasks                               202；建 Task + Attempt 1 并入队；可带 image_data_url
    GET  /tasks?limit=
    GET  /tasks/{id}                          当前 Attempt 的 TaskView
    GET  /tasks/{id}/source-screenshot        当前 Attempt 的 PNG
    GET  /tasks/{id}/input-image              Task 级用户问题截图
    GET  /tasks/{id}/agent-trace              当前 Attempt 的 Markdown 轨迹
    GET  /tasks/{id}/events        (SSE)      当前 Attempt 的事件流
    GET  /tasks/{id}/attempts/{n}             历史 Attempt 只读 TaskView
    GET  /tasks/{id}/attempts/{n}/events
    GET  /tasks/{id}/attempts/{n}/source-screenshot
    GET  /tasks/{id}/attempts/{n}/agent-trace
    POST /tasks/{id}/approve                  202；校验 change_set_id + patch_hash
    POST /tasks/{id}/force-approve            验证未通过时强制提交，必填 reason
    POST /tasks/{id}/reject                   waiting 态才能拒绝
    POST /tasks/{id}/cancel                   活动态；Worker 协程看到后停
    POST /tasks/{id}/rerun                    202；活动中 409；waiting 则旧 Attempt → superseded
    PUT  /tasks/{id}/attempts/{n}/feedback   保存完美/部分/错误评价；错误评价必须带原因
    DELETE /tasks/{id}                        204；须 execution_finished_at；不碰 GitLab

## 审批

只作用于 **当前 Attempt**。`change_set_id` 必须属于该 Attempt，且 `patch_hash` 与库中一致，
否则 409「变更已过期」。批准后状态变成 `committing`，SSE **不要当终点关掉**，还要收提交事件。

## SSE

`GET /tasks/{id}/events`

- 续传：请求头 `Last-Event-ID` 或 query `after`（都是 Attempt 内 seq）。
- 只推 **当前** Attempt；切 Attempt / 重跑后旧流里的 seq 对不上新 Attempt。
- `event:` 是事件 type，`data:` 是 payload JSON，`id:` 是 seq。
- 终点只看 Task 投影是否 terminal。`awaiting_approval` / `awaiting_force_approval` **不是**终点。
- 任务不存在时发 `event: error` 后结束。

## Attempt 状态

活动（不能删、不能重跑）:
    queued, capturing_source, syncing_repository, analyzing, validating, committing, stale

等待人:
    awaiting_approval, awaiting_force_approval

终点:
    completed, rejected, failed, cancelled, superseded

`stale`：同一 Attempt 内默认分支 SHA 变了，Worker 会重分析，不新建 Attempt。
`superseded`：人点了重跑，旧的 waiting Attempt 被作废。

删除闸门是 `execution_finished_at`：取消后 Worker 退出才写，避免活动写入打到已删行。

## 分支

Attempt 1: `fix/fixora-{task_id}-{slug}`
Attempt 2+: `fix/fixora-{task_id}-a{n}-{slug}`

确认前不建分支、不 commit。删除 Task 只清本地 artifacts 与 `/tmp/fixora/task-{id}*`，GitLab 分支留着。

## 事件（Attempt → 客户端）

控制面:

| type                     | payload 要点                          |
|--------------------------|---------------------------------------|
| task.started             | attempt                               |
| task.failed              | error；恢复中断时带 recovered         |
| task.cancelled           | —                                     |
| task.completed           | branch, sha                           |
| task.stale               | old_sha, new_sha, message             |
| source.captured          | url, title                            |
| source.failed            | error                                 |
| step.{running,completed,failed,waiting} | kind, summary              |
| approval.required        | change_set_id                         |
| approval.approved        | change_set_id                         |
| approval.force_approved  | change_set_id, reason                 |
| approval.rejected        | —                                     |
| commit.comment_failed    | error（分支已推，只是评论失败）       |

Agent 过程（完整思考只在 agent-trace.md，事件里是短预览）:

| type           | payload 要点                |
|----------------|-----------------------------|
| agent.phase    | phase=locate 或 patch, files? |
| agent.thought  | text                        |
| agent.message  | text                        |
| agent.tool     | tool, 以及路径等            |
| agent.compact  | 旧版兼容；新任务不再产生    |

## 产物路径

磁盘: `{data_root}/artifacts/task-{id}/attempt-{n}/`
Attempt 1 读时若没有新目录，回退旧的 `task-{id}/`。截图文件名 `source-page.png`，轨迹 `agent-trace.md`。
"""

from __future__ import annotations

from ..tasks.attempts import ACTIVE_STATUSES, TERMINAL_STATUSES, WAITING_STATUSES

EVENT_TYPES = frozenset(
    {
        "task.started",
        "task.failed",
        "task.cancelled",
        "task.completed",
        "task.stale",
        "source.captured",
        "source.failed",
        "step.running",
        "step.completed",
        "step.failed",
        "step.waiting",
        "approval.required",
        "approval.approved",
        "approval.force_approved",
        "approval.rejected",
        "commit.comment_failed",
        "agent.phase",
        "agent.thought",
        "agent.message",
        "agent.tool",
        "agent.compact",
    }
)

__all__ = [
    "ACTIVE_STATUSES",
    "EVENT_TYPES",
    "TERMINAL_STATUSES",
    "WAITING_STATUSES",
]
