# Self-hosted CI（D1）

托管 runner 上没有 `ccmio.dll`（它随 STAR-CCM+ 分发），所以
`tests/test_writer.py` 里所有「写出 / 读回」用例在托管 runner 上只能
**SKIP**（现只有 3 个纯逻辑用例真正跑）。在装有 STAR-CCM+ 的机器上挂一个
self-hosted runner，就能把 27 个用例全量跑通，并可选做 STAR-CCM+ 端到端
导入验证。

`.github/workflows/self-hosted.yml` 已就绪：

- `full-suite`（每次 push 到 `main` + 手动）：全量 27 用例 + 1M 单元性能冒烟。
- `import-check`（仅手动 `workflow_dispatch`）：生成小网格 → `starccm+ -batch`
  端到端导入并打印单元/顶点/边界统计。需要 license。

## 一次性注册（在装有 STAR-CCM+ 的 Windows 机器上）

1. GitHub 仓库 → **Settings → Actions → Runners → New self-hosted runner**，
   平台选 **Windows**、架构 **x64**，复制它给出的命令（含一次性 token）。

2. 在目标机器上执行（`actions-runner` 目录放哪都行，建议 `C:\actions-runner`）：

   ```bat
   mkdir C:\actions-runner
   cd /d C:\actions-runner
   # 下载并解压 runner（用仓库页面给的最新 URL）
   curl -o actions-runner-win-x64.zip -L <actions-runner-win-x64-<ver>.zip>
   tar -xf actions-runner-win-x64.zip
   ```

3. 配置并**加上标签**（workflow 的 `runs-on` 依赖这些标签）：

   ```bat
   config.cmd --url https://github.com/BHCLLBHCLL/gph2ccm --token <TOKEN> ^
     --name <机器名> --labels self-hosted,windows,starccm
   ```

   如果已配置过、漏了标签，直接编辑 `C:\actions-runner\.runner` 同目录的
   `run.cmd` 里的 `--labels`，或重跑 `config.cmd --replace`。

4. 安装为服务（开机自启）：

   ```bat
   run.cmd            # 前台跑一次，确认能连上（看到 "Listening for Jobs"）
   svc.cmd install    # 之后作为服务运行
   svc.cmd start
   ```

## 可选 secret（都不是必须）

| secret | 用途 | 是否必须 |
|---|---|---|
| `GPH2CCM_CCMIO_DLL` | 固定 ccmio.dll 路径 | 否——runner 上已能自动发现 `C:\Program Files\Siemens\*\STAR-CCM+*\star\lib\win64\*\lib\ccmio.dll` |
| `STARCCM_BIN` | `import-check` 用的启动器路径 | 否——默认 `starccm+`，仅当 PATH 上没有、或需用无空格 junction 布局时设 |

设置位置：**Settings → Secrets and variables → Actions → Repository secrets**。

## STAR-CCM+ 启动器踩坑（import-check 用）

见 `docs/version_behavior_table.md` #10/#11：

- **#10**：sh 启动器对含空格路径解析失败（`/c/Program Files` 被截断）。用
  junction 镜像无空格布局（`C:\sc8` = star+boost 等分包、`C:\jdk`），或直接
  调 `.bat`。
- **#11**：`.bat` 内部调 `wmic.exe`（部分安全策略拦截）；headless batch 需
  license（`license.dat`，`ccmpsuite`）。**ccmio.dll 写文件本身不需要
  license**，所以 `full-suite` 不依赖 license，只有 `import-check` 依赖。

如果本机启动器是 `C:\sc8\...\starccm+.bat` 这样的无空格布局，设
`STARCCM_BIN` 指向它，`import-check` 就能跑。

## 验证

1. 手动触发一次 workflow：**Actions → self-hosted → Run workflow**。
2. `full-suite` 应显示 27 passed 0 skipped（不再有 ccmio 相关的 skip）。
3. （可选）再触发一次并勾选 import-check，看 `IMPORT_DONE` 与 `CELLS/…` 输出。
