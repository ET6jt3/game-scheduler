# 本机验收环境注记（race 门禁与便携工具链）

`scripts/ci-local.ps1 -Race` 会额外把 Go 测试套件跑在 race detector 下。
race detector 需要 CGO，而本仓库的产品构建刻意保持 CGO-free（modernc
纯 Go SQLite 驱动）——因此 race 轮通过**便携 mingw64**（`D:\tools\mingw64`，
含 `gcc.exe`）临时提供 CGO 工具链：脚本把该目录前置到 `PATH` 并置
`CGO_ENABLED=1`，跑完即恢复。

- 便携 mingw64 不存在时，race 轮**诚实 SKIP**（打印 SKIP 行），不视为失败；
  其余门禁（gofmt/vet/test/build/安全扫描/Rust）照常执行。
- 历史 -race 验证记录见 `docs/NIGHTLY_PROGRESS.md`（定向 race 轮多次全绿：
  internal/task、internal/native 等并发面）。
- CI 节点（win-devops 等）通常没有该目录与 Rust 工具链——节点上的 skip
  均会显式公告，本地 CI 是唯一完整验收门槛。

安全扫描器（govulncheck / gosec / staticcheck）同样从 `GOPATH\bin` 解析，
缺失时诚实 SKIP 并给出安装命令（见 `scripts/ci-local.ps1`）。
