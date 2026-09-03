# 手动验证清单（Manual Verification）

> 目的：收敛**无法自动化 / 需要人工判断**的验证项。需要 STAR-CCM+ GUI、
> license、人眼比对、或一次性机器操作的验证都在这里；每项给出触发时机、
> 步骤、预期与失败排查。
>
> 自动化已覆盖（不需要手动做）：pytest 全量用例（self-hosted runner）、
> 1M 单元性能冒烟、`import-check` workflow（手动触发，见
> `docs/self_hosted_ci.md`）。

## 速查表

| 编号 | 验证项 | 触发时机 | 依赖 |
|---|---|---|---|
| M1 | self-hosted runner 注册 + 全量套件 | 一次性；换机/重装/掉线后 | GitHub token、STAR-CCM+ 机器 |
| M2 | STAR-CCM+ batch 端到端导入（本机） | 结构改动后；无 runner 时 | license（ccmpsuite） |
| M3 | GUI 导入人工检查 | 新实体合入后；发布前抽样 | GUI + 人眼 |
| M4 | 周期界面（C1）生效验证 | 携带 `periodic` 配对转换后 | GUI |
| M5 | Java 宏数值人工确认 | `macro` 子命令生成 setup.java 后 | GUI/batch + 人眼 |
| M6 | 性能基线复核 | 换机/换版本/疑似回归 | 本机 ccmio.dll |
| M7 | STAR-CCM+ 版本升级行为表复核 | 升级 STAR-CCM+ 后 | 新版本安装 |

---

## M1 — self-hosted runner 注册与全量套件

**时机**：首次搭建（一次性）；换机器、重装 STAR-CCM+、runner 掉线后。

**步骤**：

1. 按 `docs/self_hosted_ci.md` 注册 runner（第 0–4 步，含一键脚本）。
2. GitHub → **Actions → self-hosted → Run workflow** 手动触发一次。
3. 观察 `full-suite` 两个步骤：`Full test suite` 与 `Performance regression smoke`。

**通过标准**：

- `Full test suite`：**27 passed, 0 skipped**（托管 runner 上会 skip 的
  ccmio 写出/读回用例全部真正执行）。
- `Performance regression smoke`：正常输出 1M 单元三阶段计时，无异常退出。
- Runners 页面该 runner 标签含 `self-hosted, windows, starccm` 且状态 `Idle`。

**失败排查**：见 `docs/self_hosted_ci.md`「常见问题」。

---

## M2 — STAR-CCM+ batch 端到端导入（本机手动）

**时机**：任何写入结构改动（新实体、周期界面、字段写入等）合入后；或
runner 尚未注册时替代 CI 的 import-check。

**步骤**（Git Bash / cmd 均可，注意启动器空格路径坑，见
`docs/version_behavior_table.md` #10/#11）：

```bash
# 1) 造一个小网格（或用真实转换产物）
python tools/benchmark.py --n 30 --out bench.ccm

# 2) 指定网格并 batch 导入
export GPH2CCM_MESH=bench.ccm
# 无空格布局启动器（推荐）：
/c/sc8/star/bin/starccm+.bat -batch tools/ImportCcmCheck.java
# 或标准安装路径的 .bat：
# "/c/Program Files/Siemens/20.02.007-R8/STAR-CCM+20.02.007-R8/star/bin/starccm+.bat" -batch tools/ImportCcmCheck.java
```

**通过标准**：

- 日志出现 `IMPORT_DONE`，且 `CELLS / VERTS / BOUNDARIES / INTERFACES` 统计行
  与输入网格一致（合成 `--n 30` 块：27000 单元 / 29791 顶点）。
- 无异常堆栈、无 license 报错。

**依赖**：headless batch 需要 license（`ccmpsuite`）；`ccmio.dll` 写文件本身
**不需要** license，所以只跑转换/测试时不涉及。

---

## M3 — GUI 导入人工检查

**时机**：首次支持的新实体（新 BoundaryType、interface 类型、region 结构）
合入后；发布前对真实样本抽样。

**步骤**：STAR-CCM+ GUI 直接 Open 转换出的 `.ccm`（或经 Import Mesh），
按下表逐项核对：

| 检查点 | 预期 |
|---|---|
| Regions 数量与命名 | 与 GPH 的 region 数一致；split 模式下 interface 两侧 region 各自独立 |
| Boundaries | 名字与 GPH boundary 一致；类型（wall/velocity-inlet/pressure-outlet/…）与 regions JSON 声明或启发式结果一致 |
| Interfaces | split/周期场景出现对应 interface 节点，两侧 patch 正确 |
| 单元/顶点统计 | 与源 GPH 的单元/顶点总数一致（可用 `tools/topo_check.py` 输出对照） |
| 弹窗 | 无 error 弹窗；warning 可接受但需记录 |

**通过标准**：全部一致且无 error 弹窗。任何不一致 → 先跑
`python tools/dump_ccm.py` 对比实体树，再对照 `docs/version_behavior_table.md`。

---

## M4 — 周期界面（C1）生效验证

**时机**：`regions` JSON 携带 `periodic` 配对（旋转/平移）转换出 `.ccm` 后。

**步骤**：

1. 转换时确认日志出现几何校验通过的行（面数一致、平移型逐点重合 /
   旋转型刚体全等）；校验失败转换器会 fail-fast 报错，属预期行为。
2. GUI 导入该 `.ccm`，选中 interface 节点 → 属性窗口确认
   **Condition = Periodic Interface**（而非 Internal Interface）。
3. 可视化检查主/影面对面网格衔接：平移型应逐点重合；旋转型在转角对齐后
   网格闭合。

**通过标准**：interface 条件类型正确、两侧 patch 选择正确、网格在周期面
处视觉连续。

---

## M5 — Java 宏数值人工确认（B2）

**时机**：`python -m gph2ccm macro out.ccm -o setup.java` 生成宏后。

**步骤**：

1. **人工核对**宏中搬运的数值：MRF 的 origin/axis/omega（及单位）、
   BoundaryType 类型名、周期配对参数。宏只搬运 `.ccm` 里的
   `gph2ccm.*` 元数据，**数值正确性（是否与物理场景一致）必须人工确认**。
2. 运行宏：`starccm+ -batch setup.java`（或在已初始化的 GUI 会话中 play）。

**通过标准**：宏运行无异常；STAR-CCM+ 中生成的 MRF / interface / 边界类型
设置与 regions JSON 声明一致。

---

## M6 — 性能基线复核

**时机**：换机器、换 STAR-CCM+ 版本（新 ccmio.dll）、或疑似性能回归。

**步骤**：

```bash
python tools/benchmark.py --n 100        # 1M 单元，约 1.5 s
python tools/benchmark.py --n 149        # 3.3M 单元，与真实大网格同量级
```

**通过标准**（详见 `docs/performance_baseline.md`「回归判定」）：

- `write` 耗时 / 输出大小随单元数线性，偏离基线 > 30% 需排查。
- `write` 峰值内存无阶跃（翻倍）——出现则可能引入了全量中间数组。
- 基线参考（本地 20.02.007-R8 ccmio.dll）：1M 单元 write 0.97 s / 峰值
  1028 MB；3.3M 单元 write 3.41 s / 峰值 3324 MB。

---

## M7 — STAR-CCM+ 版本升级行为表复核

**时机**：升级 STAR-CCM+（换 ccmio.dll / 启动器）后。

**步骤**：逐条过 `docs/version_behavior_table.md`（15 条实测差异），重点复验
版本敏感条目：

- #2 `CCMIOReadNodestr` char\*\* 签名（ctypes 绑定是否仍兼容）
- #3 节点名 32 字符上限
- #10/#11 启动器空格路径与 `wmic.exe`（新安装布局下重新找无空格启动器）
- 若已在用字段/周期写入：Field 与 InterfaceDefinitions 相关条目

**记录**：新版本号 + 每条「复验通过 / 有新差异」；有新差异则追加到
`version_behavior_table.md` 并同步调整代码。
