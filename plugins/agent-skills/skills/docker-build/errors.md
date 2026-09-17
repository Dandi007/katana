
## 2026-09-17：new-api 镜像验证的 SQLite 所有权

`make verify-image` 以普通用户运行时，候选容器以 root 创建临时 SQLite，停止后宿主验证脚本写夹具会报 `attempt to write a readonly database`。不涉及生产状态。使用 `sudo make verify-image`，显式传入 IMAGE、RELEASE_VERSION、VCS_REF 和 REPORT；不要改变生产目录权限。构建 revision 由 Makefile 的 `git rev-parse HEAD` 自动取得，避免手工录入完整 SHA。

## 2026-09-17：构建下载网络与代理作用域

new-api 最终镜像构建的 `go mod download` 直连 `proxy.golang.org`，多份 zip 在约 741 秒后报 `unexpected EOF`。宿主代理环境不会自动传入 Docker 构建；应先比较构建进程网络与宿主连通性，不无限等待。此次经已有本机代理恢复：保持同一源码 revision、依赖锁文件与 `make image` 入口，仅在临时 Docker CLI wrapper 中对 build 添加 host network、HTTP_PROXY/HTTPS_PROXY 构建参数。

全局代理同时影响 Bun 时，`js-binary-schema-parser` tarball 曾触发 `IntegrityCheckFailed`。不得跳过完整性检查或修改 lockfile；把直连正常的 npm registry 域名加入仅构建期 NO_PROXY 后，Bun 安装通过，Go 下载 128.2 秒完成，最终镜像构建与 verify-image 通过。构建后检查运行镜像 Config.Env 无代理变量，并删除 wrapper。代理地址与认证属于外部环境，不写入镜像或源码。
