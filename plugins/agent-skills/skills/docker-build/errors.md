
## 2026-09-17：new-api 镜像验证的 SQLite 所有权

`make verify-image` 以普通用户运行时，候选容器以 root 创建临时 SQLite，停止后宿主验证脚本写夹具会报 `attempt to write a readonly database`。不涉及生产状态。使用 `sudo make verify-image`，显式传入 IMAGE、RELEASE_VERSION、VCS_REF 和 REPORT；不要改变生产目录权限。构建 revision 由 Makefile 的 `git rev-parse HEAD` 自动取得，避免手工录入完整 SHA。
