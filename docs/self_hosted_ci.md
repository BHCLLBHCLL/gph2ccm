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

---

## 前置条件

- 一台 **Windows x64** 机器，已装 STAR-CCM+（本仓库验证版本 20.02.007-R8，
  安装于 `C:\Program Files\Siemens\20.02.007-R8\`）。
- 能在该机器上打开 **管理员 PowerShell**（第 4 步装服务需要管理员）。
- 能登录该 GitHub 仓库的账号（用于获取第 0 步的一次性注册 token）。

---

## 一次性注册（照抄即可）

### 第 0 步：取一次性注册 token

打开仓库页面 **Settings → Actions → Runners → New self-hosted runner**，
平台选 **Windows**、架构 **x64**。页面会给出形如下面的命令：

```bat
./config.cmd --url https://github.com/BHCLLBHCLL/gph2ccm --token AAAA...
```

其中的 `AAAA...` 就是注册 token。**注意**：

- 这是**注册专用的一次性 token，不是个人访问令牌（PAT）**，约 1 小时后失效；
  过期了回到该页面再点一次重新生成即可。
- 它只在第 2 步 `config.cmd` 里用一次，之后被写入本地 `.runner` / 凭据文件，
  无需重复输入。

### 第 1 步：下载并解压 runner

在**管理员 PowerShell** 里执行（版本 v2.337.0 为当前最新，如需更新版本号，
到 `https://github.com/actions/runner/releases` 看最新 `tag` 即可）：

```powershell
New-Item -ItemType Directory -Force C:\actions-runner | Out-Null
Set-Location C:\actions-runner
Invoke-WebRequest -Uri "https://github.com/actions/runner/releases/download/v2.337.0/actions-runner-win-x64-2.337.0.zip" -OutFile "actions-runner-win-x64-2.337.0.zip"
Expand-Archive -Path "actions-runner-win-x64-2.337.0.zip" -DestinationPath .
```

（可选）校验完整性——官方发布页附有 `*.zip.sha256`，下载后：

```powershell
(Get-FileHash actions-runner-win-x64-2.337.0.zip -Algorithm SHA256).Hash.ToLower()
```

与官方页面对照一致即可。

### 第 2 步：配置并打标签（含服务安装，一步完成）

`runs-on` 依赖三个标签：`self-hosted`（GitHub 自动加，注意本版本默认标签
为 `self-hosted,Windows,X64`，大小写不敏感匹配）+ `windows` + `starccm`
（自定义）。因此**只补自定义标签 `starccm`** 即可。

> **v2.337.0 变化（2026-09 实测）**：runner 包已**移除 `svc.cmd` /
> `security.cmd`**，服务安装统一走 `config.cmd --runasservice`（内部调用
> `bin\RunnerService.exe`）。旧教程里的 `svc.cmd install` 已失效。

**方式 A（推荐）：配置 + 服务一步到位**（需**管理员** PowerShell）：

```powershell
.\config.cmd --unattended --url https://github.com/BHCLLBHCLL/gph2ccm `
  --token AAAA... --name "$env:COMPUTERNAME-starccm" --work _work `
  --labels starccm --runasservice --windowslogonaccount "NETWORK SERVICE"
```

- `--unattended`：全部参数已给齐，不再弹交互提示。
- `--runasservice`：注册完成后立即安装并启动 Windows 服务（开机自启），
  无需再手动装服务。服务名形如 `actions.runner.<org>-<repo>.<name>`。
- 不想装服务就去掉 `--runasservice`，改用方式 B 手动跑。

**方式 B：前台运行（不装服务，调试用）**：

```powershell
.\config.cmd --unattended --url https://github.com/BHCLLBHCLL/gph2ccm `
  --token AAAA... --name "$env:COMPUTERNAME-starccm" --work _work --labels starccm
.\run.cmd        # 看到 "Listening for Jobs" 即连通成功
```

说明：

- `--token AAAA...` 换成第 0 步拿到的 token（也可用有 repo 权限的 PAT）。
- 别把 `self-hosted` / `windows` 传进 `--labels`：前者自动附加、后者已在
  默认标签里，重复传可能造成标签列表出现两条同名。
- 配置成功后，**Settings → Actions → Runners** 里应能看到该 runner 处于
  `Idle`，标签列含 `self-hosted, Windows, X64, starccm`。

### 第 3 步：服务管理（仅方式 A；v2.337.0 无 svc.cmd）

```powershell
Get-Service actions.runner.*                    # 查状态
Stop-Service actions.runner.*                   # 停
Start-Service actions.runner.*                  # 启
# 卸载服务并注销 runner：
.\config.cmd remove --token AAAA...
```

### 一键脚本（PowerShell，管理员，v2.337.0 实测可用）

把 `TOKEN` 替换后整体粘贴（已含下载校验、配置、服务安装）：

```powershell
$ErrorActionPreference = "Stop"
$TOKEN = "AAAA..."            # 第 0 步的注册 token
$NAME  = "$env:COMPUTERNAME-starccm"
$VER   = "2.337.0"

New-Item -ItemType Directory -Force C:\actions-runner | Out-Null
Set-Location C:\actions-runner
Invoke-WebRequest -Uri "https://github.com/actions/runner/releases/download/v$VER/actions-runner-win-x64-$VER.zip" -OutFile "runner.zip"
Expand-Archive -Path "runner.zip" -DestinationPath . -Force
.\config.cmd --unattended --url https://github.com/BHCLLBHCLL/gph2ccm `
  --token $TOKEN --name $NAME --work _work `
  --labels starccm --runasservice --windowslogonaccount "NETWORK SERVICE"
Get-Service actions.runner.* | Format-Table Name, Status
```

---

## ccmio.dll 自动发现（full-suite 无需 secret）

`gph2ccm/ccmio.py::find_ccmio_library()` 按以下顺序找库，命中最先存在的：

1. 环境变量 `GPH2CCM_CCMIO_DLL`（若设）。
2. 以下 glob（Windows）：
   - `C:\Program Files\Siemens\*\STAR-CCM+*\star\lib\win64\*\lib\ccmio.dll`
   - `D:\Program Files\Siemens\*\STAR-CCM+*\star\lib\win64\*\lib\ccmio.dll`
   - `C:\Program Files (x86)\Siemens\*\STAR-CCM+*\star\lib\win64\*\lib\ccmio.dll`
   - `D:\Program Files (x86)\Siemens\*\STAR-CCM+*\star\lib\win64\*\lib\ccmio.dll`
   - `C:\Siemens\*\STAR-CCM+*\star\lib\win64\*\lib\ccmio.dll`
3. DLL 搜索路径上的 `ccmio.dll` / `libccmio.so` / `libccmio.dylib` / `ccmio.so`。

本机标准安装（`C:\Program Files\Siemens\20.02.007-R8\...`）命中第 2 条，因此
`full-suite` 跑在 runner 上时**无需任何 secret**，只要该机器装了 STAR-CCM+。

---

## 可选 secret（都不是必须）

设置位置：**Settings → Secrets and variables → Actions → Repository secrets**。

| secret | 用途 | 是否必须 |
|---|---|---|
| `GPH2CCM_CCMIO_DLL` | 固定 ccmio.dll 路径，覆盖自动发现 | 否——除非自动发现路径不对、或想锁定某版本 |
| `STARCCM_BIN` | `import-check` 用的 STAR-CCM+ 启动器路径 | 否——默认 `starccm+`，仅当 PATH 上无、或需用无空格 junction 布局时设 |

---

## STAR-CCM+ 启动器踩坑（import-check 用）

见 `docs/version_behavior_table.md` #10/#11：

- **#10**：sh 启动器对含空格路径解析失败（`/c/Program Files` 被截断）。用
  junction 镜像无空格布局，或直接调 `.bat`。
- **#11**：`.bat` 内部调 `wmic.exe`（部分安全策略拦截）；headless batch 需
  license（`license.dat`，`ccmpsuite`）。**ccmio.dll 写文件本身不需要
  license**，所以 `full-suite` 不依赖 license，只有 `import-check` 依赖。

本机实测的可用无空格布局：

- 启动器：`C:\sc8\star\bin\starccm+` 或 `C:\sc8\star\bin\starccm+.bat`
- license：`C:\Program Files\Siemens\license.dat`

若你机器上存在类似的 `C:\sc8\...\starccm+.bat`，把 `STARCCM_BIN` secret 设为
该路径（例如 `C:\sc8\star\bin\starccm+.bat`），`import-check` 即可在 headless
+ license 环境跑通。否则可只跑 `full-suite`，`import-check` 留待需要时再配。

---

## 验证清单

> 其余需要人工执行的验证（GUI 导入检查、周期界面生效确认、宏数值确认、
> 版本升级复核等）见 `docs/manual_verification.md`。

1. Actions 页面 **Settings → Actions → Runners**：runner 处于 `Idle`，标签含
   `windows`、`starccm`（外加自动的 `self-hosted`）。
2. 手动触发一次：**Actions → self-hosted → Run workflow → Run workflow**。
3. `full-suite`：应显示 **27 passed 0 skipped**（不再有 ccmio 相关的 skip），
   且 `Performance regression smoke` 步骤正常输出 1M 单元耗时。
4. （可选）再次触发并走 `import-check`，看日志出现 `IMPORT_DONE` 与
   `CELLS/VERTS/BC` 统计行。

## 常见问题

- **runner 一直 `Offline`**：看 `svc.cmd status` / `_diag` 目录日志；多半是
  服务没启动或 token 过期，重跑 `config.cmd` 刷新 token。
- **`full-suite` 仍出现 ccmio skip**：说明自动发现没命中，设
  `GPH2CCM_CCMIO_DLL` secret 指向真实 `ccmio.dll` 绝对路径。
- **`import-check` 报 license / 启动器错误**：回到上节「启动器踩坑」，用
  `C:\sc8` 无空格 junction 布局 + 设 `STARCCM_BIN`。
