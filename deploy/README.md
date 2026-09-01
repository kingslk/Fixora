# Linux 部署边界

1. 创建 `fixora` 与 `fixora-runner` 系统用户及共享组；`fixora-runner` 不得读取 `/etc/fixora/fixora.env`。
2. 安装 PostgreSQL、Redis、Git、Node 22、Corepack、Python 3.12、uv、Playwright Chromium 与 systemd。
3. 将 `fixora-tmpfiles.conf` 放入 `/etc/tmpfiles.d/`，执行 `systemd-tmpfiles --create`。
4. API 与 Worker 使用 `fixora`；仓库测试由 Worker 调用 transient `systemd-run --uid=fixora-runner`。
5. 反向代理只允许可信内网访问。Fixora v1 没有登录，不能直接暴露到公网。
6. 首次保存页面登录态时自动生成 `/var/lib/fixora/.secret-key`；此文件权限为 `0600`，需要和数据库一起备份，否则已保存登录态无法解密。
7. 生产环境显式设置 `FIXORA_DATA_ROOT=/var/lib/fixora`；源码默认 `./data` 仅供本地开发。

测试进程允许全部外网，这是已接受风险；专用用户和 systemd 只能隔离宿主机密钥，不能阻止测试代码主动外传源码。
