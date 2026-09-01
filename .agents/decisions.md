# 设计决策记录

只记代码读不出来的“为什么”。每条 3–5 行；决策失效直接删。

## 虚拟变更后才写 GitLab

选择 human-in-the-loop：Agent 只改 VirtualWorkspace，确认后才建分支和 commit。放弃自动推默认分支，避免污染客户仓库。若产品改为无人值守提交，再复查这条。

## 模型走第三方网关，关闭 OpenAI tracing

客户代码不能发往 OpenAI。`FIXORA_MODEL_API_URL` 指向兼容网关；Agents SDK tracing 默认关闭。不要为消除 warning 填写 `OPENAI_API_KEY`。改用官方 OpenAI 且允许出网时再打开 tracing。

## 隐藏目录一律不进分析上下文

`.claude` 等目录按字母序排在 `git ls-tree` 最前，会淹没 `list_files`。选择过滤全部隐藏目录和构建产物，而不是只拦 `node_modules`。若任务就是修 `.github` CI，再对该目录开白名单。

## 阶段重建上下文，不用轮次压缩

locate / patch / 验证失败重试各自开新 Runner 会话，只带该阶段需要的状态（LocateResult、虚拟 diff、失败日志）。达到轮次上限返回未收敛。完整轨迹只留 agent-trace.md。若网关原生支持 compact 且模型名可用，再评估是否加回过滤器。

## 轨迹落盘，不进数据库

完整思考写入 data root 的 `artifacts/task-<id>/attempt-<no>/agent-trace.md`；`task_events` 只存短预览。避免大段推理撑爆 Postgres JSON。需要跨节点查看时再上对象存储。

## 设置页不保存 GitLab / 模型密钥

与 MemLoci 一致，密钥只存在环境变量。设置页没有保存按钮是决策，不是漏做。若改为多租户密钥，再让设置页可写。

## 扩展阶梯

入口超过 40 行时优先压缩；单个决策文件超过 60 行时按高影响域拆分；同流程真实重复至少三次才加 skill；任务跨会话才留进度文件。
