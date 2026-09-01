# Fixora Agent Guide

给编码 Agent 的最小上下文。代码结构、类型和调用链自己读源码推导；本文只写代码里看不出来的事实。

## 协作

- 全程使用中文。
- 复杂、方向性或高影响改动：先给 1–2 个方案等用户选择；已选定后在范围内自主完成，不重复索要确认。
- 开始前看 `git status --short`；已有改动属于用户，禁止覆盖、回滚或顺手整理。

## 产物边界

- `web/dist/`、`backend/data/`（git cache、artifacts、dependencies）是生成物，只改源码，不改产物。

## 硬边界

- 分析阶段只写虚拟变更；未获用户确认不得创建 GitLab 分支、commit 或 MR。
- GitLab / 模型配置只来自 `backend/.env` 的 `FIXORA_GITLAB_*`、`FIXORA_MODEL_*`；设置页只展示状态，禁止改成可写配置。
- 代码不得发往 OpenAI；`FIXORA_MODEL_API_URL` 必须指向第三方兼容网关。不要为消除 tracing warning 而设置 `OPENAI_API_KEY`。
- `FIXORA_GITLAB_SSL_VERIFY=false` 与 `FIXORA_MODEL_SSL_VERIFY=false` 是内网自签证书决策，不是缺陷。
- `backend/data/.secret-key` 用于解密已保存的页面登录态，禁止删除或提交到 git。
- 生产测试必须经 `systemd-run` 与 `fixora-runner` 用户执行；基础设施容器不跑仓库代码。

## 完成标准

- 验证与风险成比例；测试、lint、类型检查及风险所需构建应自主执行。未运行的验证如实说明，不把静态检查说成运行验证。
- 只读直接答；单文件低风险改动一行；多文件或高影响改动用结果、取舍、验证、风险四行卡片。

设计意图与历史决策（仅需要时读）：`.agents/decisions.md`
