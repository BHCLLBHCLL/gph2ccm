# gph2ccm 功能完整度与深度评估

> 评估对象：`gph2ccm/` 包（deps / model / ccmio / convert / verify / reorder）、
> `tools/` 工具链、`tests/`、`README.md`、`AGENTS.md`、`DEV_SUMMARY.md`、
> `conversion_issues_analysis.md`。
>
> 评估维度：**完整度**（功能覆盖广度）、**深度**（实现质量与验证强度）、
> **工程成熟度**（架构 / 测试 / 文档 / 工具链）。
> 基准：STAR-CCM+ 可正常导入的 legacy CCM 文件所需的实体与能力，对照
> `tests/bladerotating_dm2.ccm`（STAR-CCM+ 原生导出）逆向得到。

---

## 0. 总体定位

| 阶段 | 特征 | 当前位置 |
|---|---|---|
| ① 原型 | 小用例跑通 | 已越过 |
| ② 可用 | 真实网格可导入 | 已越过 |
| ③ 健壮 | 边界 / 错误 / 大网格稳定 | **处于中段** |
| ④ 生产级 | 完整功能 + 测试 + 文档 + 维护 | 未达 |

**一句话结论**：核心网格拓扑骨架（顶点 / 内部面 / 边界 / 单元类型 /
多 region + interface）**完整且经真实大网格验证**，本质是「网格搬运器」。
**物理与求解层**原为完全空白，现已推进到「描述性元数据载体」阶段：
边界条件（结构化类型 + 参数）、结果场定义、求解设置均可经 `regions` JSON
以 `gph2ccm.BC.*` / `gph2ccm.Field.*` / `gph2ccm.Solver.*` 命名空间写入
`.ccm`，**纯描述、不求解**（"keep boundary" 范围决策）。距离「求解器就绪
导出器」仍差真正的物理求解属性写入。

---

## 1. 功能完整度矩阵

状态图例：**✅ 已实现** · **⚠️ 部分 / 有保留** · **❌ 缺失**

| # | CCM 能力 | 状态 | 说明 |
|---|---|---|---|
| 1 | Vertices（坐标） | ✅ | float32，mm，scale=0.001；大坐标有精度上限 |
| 2 | Cells + CellType | ✅ | Label / MaterialType / GroupId / MaterialId |
| 3 | Internal Faces（owner/neigh） | ✅ | 2D 数组成块 bug 已规避（330 万单元验证） |
| 4 | Boundary Faces + Regions | ✅ | 重叠区域去重 + Default_Boundary_Region |
| 5 | CellTopologyType 显式写 | ✅ | poly=255，避免导入逐个判定 |
| 6 | BoundaryType | ✅ | 启发式 / JSON 覆盖类型名；周期配对经几何校验后写生效的 `InterfaceDefinitions`（`PeriodicInterface`，C1） |
| 7 | Region Cell Maps（split 模式） | ✅ | 每 region 一个 `Region Cell Map <label>` |
| 8 | Grid Interface（split 模式） | ✅ | 两侧边界 patch + `[Interface N]` + `InterfaceDefinitions` |
| 9 | CCMIOCompress | ✅ | 写出后压缩 |
| 10 | Reorder（RCM 单元重排） | ✅ | 可选，降低导入重排开销 |
| 11 | Verify 读回校验 | ✅ | H1 已修复：split 模式 interface 两侧共享面不再误报（relaxed 校验） |
| 12 | Multiple Processors（分布式） | ❌ | 单 processor（legacy CCM 固有限制）；已写入 `gph2ccm.Note.Processors`/`MultiProcessor` 自说明元数据 + 大网格告警 |
| 13 | Solution Fields（结果场） | ⚠️ | 不写实际场数据；可经 `regions["fields"]` 写入 `gph2ccm.Field.*` **描述性**场定义 |
| 14 | Boundary Conditions（含对称 / 周期配对） | ⚠️ | 结构化类型规范化 + `gph2ccm.BC.*` 描述性参数（`regions` JSON 驱动）；**周期配对已生效**（C1），其余仍非求解就绪 |
| 15 | Material / Region 求解属性 | ⚠️ | 新增 `gph2ccm.Solver.*` 描述性求解设置元数据（`regions` JSON 驱动），仍非实际属性 |
| 16 | MRF（旋转参考系） | ⚠️ | 不自动施加旋转条件；可经 `regions["mrf"]` 写入 `gph2ccm.MRF.*` **描述性**旋转参考系声明 |
| 17 | Periodic / Cyclic 配对 | ⚠️ | 不生成几何配对界面；可经 `regions["periodic"]` 写入 `gph2ccm.Periodic.*` **描述性**配对声明 |
| 18 | 2D 网格包裹 | ❌ | 不挤出壳层；新增 2D 检测 + `gph2ccm.Note.Dimension`/`TwoDWrapping` 自说明（包裹不在范围内） |
| 19 | 单元 / 节点场数据 | ❌ | 无 |
| 20 | 网格质量修复 | ⚠️ | 仍不修改网格；新增导出期内嵌 `gph2ccm.Qual.*` 摘要（未覆盖/退化面计数）+ `diagnose_quality` API；重检查仍在 `tools/topo_check.py` |
| 21 | 高阶单元（2 阶） | ❌ | 已调研（D4）：**legacy CCM 无高阶语义**——上游 GPH 线性、`CellTopologyType` 仅线性形状码、STAR-CCM+ 全文 0 处 curved 导入。非「未适配」，而是格式链两端均无高阶数据 |

**完整度统计**：21 项中已实现 12、部分 7、缺失 2（#12 多处理器、#21 高阶单元仍为 ❌）→
**核心网格层完整覆盖，物理 / 求解层进入「描述性元数据 + 诊断」阶段（可携带/校验信息但非求解就绪）**。

---

## 2. 功能深度（实现质量与验证强度）

### 2.1 已做到生产级深度的部分

- **2D 分块写入 bug 的规避**：根因定位准确（dll 2D 分块按扁平偏移落盘），
  顶点 / face_cells 改为单次写入，经 330 万单元 / 1044 万面真实网格验证导入正常。
- **多 region + interface**：不是猜测，而是**逆向 STAR-CCM+ 原生导出文件**
  （`bladerotating_dm2.ccm`）得到的 `GroupId + Region Cell Map + 两侧 patch +
  InterfaceDefinitions` 结构；真实 simplec 文件导入得到两个独立 region +
  `INTERFACE`，两侧接口面各 412,644。
- **验证方法论成熟**：`dump_ccm.py`（实体树对比）→ `topo_check.py`（每单元面数 /
  退化面 / 闭合性）→ `ImportCcmCheck.java`（STAR-CCM+ batch 端到端导入）。
  这是「结构改动必须过 STAR-CCM+ 导入闭环」的正确工程实践。
- **边界去重逻辑**考虑了 Cradle 重叠导出语义（非 `@PartSurface_` 优先）。

### 2.2 深度不足的部分

- **精度**：顶点 float32 对大尺度坐标有精度上限（已知限制）。
- ~~**法向定向**：`cell_centroids` 用面心算术均值近似（非体积加权），畸形切单元
  上可能误判 interface 法向（L3）~~ —— ✅ 已改散度定理体积加权质心（`f0ea777`）。
- ~~**接口虚拟 face id 的 `max_id` 语义**（H2）~~ —— ✅ 已与原生导出 dump 对照：
  差异确认（原生用重复真实 id，本工具用 `+k·n_faces` 虚拟 id），保留现实现并把
  理由与残余风险记录在 `conversion_issues_analysis.md`。
- ~~**异常安全**：`write()` 无 `try/finally`，异常时文件句柄泄漏（M4）~~ —— ✅ 已修复（`3f8c18c`）。
- ~~**参数失效**：`--chunk-vertices` 因 2D 分块 bug 实际不生效（M1）~~ —— ✅ 已移除（`b6d36e0`）。
- ~~**元数据只写不读**：`gph2ccm.*` 节点写入后没有官方途径读回~~ —— ✅ 已补
  `python -m gph2ccm inspect` 读回子命令（`0d53a20`）。

---

## 3. 工程成熟度

| 维度 | 评价 |
|---|---|
| 架构分层 | ✅ 清晰：deps（解析）/ model（模型）/ ccmio（绑定）/ convert（编排）/ verify / reorder；复用 `gphdecoding` 与 `ccmio.dll`，不自造轮子 |
| 文档 | ✅ 四件套（README / AGENTS / DEV_SUMMARY / 问题清单）齐全且**基本与代码一致**（split 已实现，文档未超前） |
| 工具链 | ✅ 完整：dump / topo_check / make_demo / make_two_region / extract_subset / ImportCcmCheck.java + 4 份真实样本 dump |
| 测试 | ✅ 14 个用例（含 split / interface / verify / 元数据 / 诊断），缺 ccmio 时自动 skip；CI 已接入（GitHub Actions，Ubuntu/Windows × 3.11/3.12） |
| 错误恢复 | ⚠️ 无 finally、无备份回滚之外的容错 |
| 可维护性 | ⚠️ 存在若干代码质量问题（M1 / M4 / H1 / H2 / L 系列，见问题清单） |

---

## 4. 与 STAR-CCM+ 原生导出的差距（对照 `bladerotating_dm2.ccm`）

**已实现**：`GroupId` / `MaterialId`、每 region `Region Cell Map`、
`InterfaceDefinitions`（`Interface-N`：Name / Boundary0 / Boundary1 /
Configuration=IN_PLACE / ConditionType=InternalInterface）、两侧带单元数据的
边界 patch。

**未对齐**：
- `PeriodicBoundaries` / `Interfaces` 节点（周期 / 滑移配对）
- `FieldSet` / `Field` / `FieldData`（任何结果场 / 初始场）
- `Material` / `Region` 下的求解属性（湍流模型、密度、粘度等）
- 多 processor / 分布式拓扑

---

## 5. 关键缺口（按影响排序）

> 执行进度（对应原始 8 项清单）：**#8 测试 ✅、#2 边界条件结构化 ✅、#1 结果场/求解设置 ⚠️、#3 MRF ⚠️、#4 周期/滑移配对 ✅（C1 已生效，描述性节点保留）、#5 多 processor ⚠️、#6 2D 包裹 ⚠️、#7 质量诊断 ⚠️ —— 除周期配对外，物理/求解层均为描述性/诊断性，非求解就绪**。

1. **无结果场 / 求解设置** —— ⚠️ 已有描述性载体：经 `regions["fields"]` / `regions["solver_settings"]` 写入 `gph2ccm.Field.*` / `gph2ccm.Solver.*` 命名空间节点；**非实际场数据、不求解**（"keep boundary" 范围）。
2. **边界条件仅类型名** —— ⚠️ 已结构化：`boundary_conditions` 经 `_normalize_bctype` 规范化类型 + `gph2ccm.BC.*` 描述性参数（regions JSON 驱动），仍非求解就绪。
3. **MRF 仅 region 划分** —— ⚠️ 已有描述性旋转参考系声明：经 `regions["mrf"]` 写入 `gph2ccm.MRF.*`（region/type/axis/origin/omega/units），**非真实旋转条件**。
4. **周期 / 滑移界面配对** —— ✅ 从「描述」升级为「生效」：`regions["periodic"]`
   经几何匹配校验（面数一致；平移型逐点重合、旋转型刚体全等）后，额外写入
   `InterfaceDefinitions` 节点（`ConditionType=PeriodicInterface`）；几何不匹配
   则转换报错 fail-fast。`gph2ccm.Periodic.*` 描述性节点仍保留（C1）。
5. **多 processor / 分布式** —— ⚠️ 已自说明：legacy CCM 单 processor 固有限制，写入 `gph2ccm.Note.Processors`/`MultiProcessor` 元数据 + 大网格（>200 万 cell）告警；**无分布式分区写入**。
6. **2D 网格包裹** —— ⚠️ 已检测并自说明：识别 collapsed-axis 的 2D 网格，写入 `gph2ccm.Note.Dimension`/`TwoDWrapping` 元数据；**不挤出壳层**（超出 keep-boundary 范围）。
7. **网格质量修复** —— ⚠️ 已内嵌导出期质量摘要（`gph2ccm.Qual.*`：未覆盖/退化面计数）+ `diagnose_quality` API；**仍不修改网格**（keep-boundary 范围；重检查见 `tools/topo_check.py`）。
8. **测试与 CI 薄弱** —— ✅ 已补 `tests/test_writer.py` **14 个**回归用例（写回读验证、split/interface、verify 放宽、结构化 BC、字段/求解/MRF/周期元数据、processor/dimension note、质量诊断）；✅ CI 已接入 `.github/workflows/tests.yml`（Ubuntu/Windows × Python 3.11/3.12），托管 runner 无 ccmio 时跳过写入/读回用例而非失败，self-hosted runner 配 `GPH2CCM_CCMIO_DLL` secret 可跑全量。

---

## 6. 下一步开发规划

> 依据：完整度矩阵中仍为 ⚠️/❌ 的项（#6 / #12 / #13–#21）、深度不足清单
> （M1 / M4 / L3 / H2 + 元数据只写不读）、与原生导出未对齐的实体。
> 原则：**先还质量债 → 再把元数据闭环（用户可见价值最大）→ 求解就绪
> 按依赖逐项解锁 → 工程化长期滚动**。

### 阶段 A：加固现有能力（低风险，无外部依赖）

| # | 任务 | 来源 | 验收标准 |
|---|---|---|---|
| A1 ✅ | `write()` 加异常清理：异常时保证句柄与临时文件清理 | M4 | 注入异常后无句柄/临时文件泄漏，附单测（`3f8c18c`） |
| A2 ✅ | `--chunk-vertices` 移除：CLI 保留弃用桩并告警，参数不再生效 | M1 | `--help` 与实际行为一致，README 同步（`b6d36e0`） |
| A3 ✅ | `cell_centroids` 改散度定理体积加权，降低 interface 法向误判 | L3 | 畸形切单元用例质心精确（误差 1e-16 vs 算术均值 0.063，`f0ea777`） |
| A4 ✅ | 接口虚拟 face id 的 `max_id` 语义与 `bladerotating_dm2.ccm` dump 逐项核对 | H2 | dump 对比完成：差异已确认并记录于 `conversion_issues_analysis.md`（保留现实现 + 残余风险说明） |

### 阶段 B：元数据闭环（把「说明书」变成「可执行的导入辅助」）

当前 `gph2ccm.*` 只写不读——用户拿到 `.ccm` 后没有官方途径读回清单，
README 的「导入后补充清单」只能靠手工对照。

| # | 任务 | 说明 | 验收标准 |
|---|---|---|---|
| B1 | `python -m gph2ccm inspect out.ccm` | 读回全部 `gph2ccm.*` 节点，输出对应 README 清单的人读报告 | 元数据往返单测：写 → inspect → 解析结果与输入相等 |
| B2 ✅ | STAR-CCM+ Java 宏生成器：`python -m gph2ccm macro out.ccm -o setup.java` | 生成读取元数据并自动创建 MRF / 周期 interface / BoundaryType 的宏**模板**（半自动，数值仍需人工确认） | STAR-CCM+ 2502 batch 编译通过；边界类型设置、mesh 导入、BC TODO 提醒均验证通过；MRF 使用官方 journal 范式，在已初始化会话中可正常运行 |
| B3 | regions JSON schema 校验 + 示例 | `docs/regions.example.json` + 手写 validator（报错信息可定位行号），非法键早失败 | 非法 JSON 在转换前报错并退出码 ≠ 0 |
| B4 | `diagnose_quality` 输出分级 | issues 按错误/警告分级并给修复建议（指向 STAR-CCM+ 的 repair 工具） | 3 级日志（error/warn/info），README 同步 |

### 阶段 C：求解就绪导出（中期，逐项有前置依赖）

| # | 任务 | 前置条件 | 说明 |
|---|---|---|---|
| C1 ✅ | `PeriodicBoundaries` / `Interfaces` 节点写入（周期配对从「描述」变「生效」） | 周期对几何匹配算法（主/影子面按转角/平移配对）；样本 `bladerotating_dm2.ccm` 已在手 | 矩阵 #6/#17，对叶轮机械用户价值最高 |
| C2 | 初始场写入（`FieldSet` / `Field` / `FieldData`） | **可行性结论（2026-09-02）**：✅ 技术可行——libccmio 有完整 Field API（`CCMIONewField` + `CCMIOWriteFieldDataf` 等，见 `ccmio.h:452,884-970`）；GPH 网格本身无场，但同体系的 **FPH 结果文件**（魔数 `CRDL-FLD`）携带 FlowSolution，`gphdecoding/fph2cgns.py` 已能解析并写 CGNS FlowSolution。**排期**：作为独立里程碑（FPH 输入扩展 + 场-网格 cell 掩码对应 + CCM FieldPhase/Field/FieldData 写入），中等工作量；用户无 FPH 结果文件时维持现状（`gph2ccm.Field.*` 描述层） | 矩阵 #13/#19；依赖 FPH 结果文件，需用户配合提供 |
| C3 | 多 processor 写入 | **可行性结论（2026-09-02）**：✅ 技术可行——`CCMIOWriteProcessor` 的 `verticesFile/topologyFile/initialFieldFile/solutionFile` 参数证实 STAR-CCM+ 并行 CCM 采用**每分区独立文件**组织（主 `.ccm` + 每 processor 一个分区文件），libccmio 完整支持；2D 分块 bug 与分区无关（单次写入已规避）。**排期**：靠后——主要工作量在网格分区算法（几何/METIS 级），且 STAR-CCM+ 导入单 processor CCM 后会自动分区，多 processor 文件的导入侧收益待确认；无明确需求前维持单 processor + `gph2ccm.Note.MultiProcessor` 自说明 | 矩阵 #12；价值取决于导入侧是否真需要预分区 |

### 阶段 D：工程化与长期维护（滚动进行）

- D1 ⚙️ **self-hosted CI runner**：workflow 与注册文档已就绪
  （`.github/workflows/self-hosted.yml` + `docs/self_hosted_ci.md`）——
  `full-suite` 每次 push 跑全量 27 用例（`runs-on: [self-hosted,windows,starccm]`）
  + 1M 单元性能冒烟；`import-check`（仅手动）生成小网格后 `starccm+ -batch`
  端到端导入。**待用户**：在装有 STAR-CCM+ 的机器上按文档注册 runner
  （需一次性 GitHub token），可选配 `GPH2CCM_CCMIO_DLL` / `STARCCM_BIN` secret。
- D2 ✅ **版本行为对照表**：`docs/version_behavior_table.md`（15 条实测差异：
  2D 分块偏移 bug、`CCMIOReadNodestr` char** 签名、32 字符节点名上限、
  无通用子节点枚举、Simulation 无 getBoundaryManager、MRF manager 注册时机、
  虚拟 face id、InterfaceDefinitions 节点、启动器空格路径/wmic、GPH 无场/FPH 有场、
  并行 CCM 每分区单文件、legacy CCM 无高阶单元语义），避免换版本后重新踩坑。
- D3 ✅ **性能基线**：`tools/benchmark.py`（合成结构化六面体生成器 +
  三阶段计时 + PeakWorkingSetSize 内存峰值）+ `docs/performance_baseline.md`
  （330 万单元：build 0.60s / 1.36 GB，write 3.41s / 3.32 GB，compress 0.41s，
  输出 399.7 MB）。防止性能回归（此前只有正确性回归）。
- D4 ✅ 高阶单元（#21）调研完成：**legacy CCM 无高阶语义**，结论永久成立——
  ① 上游 GPH 为线性网格（gphdecoding 全库无 quadratic/curved/mid-side，`LS_Nodes`
  只有顶点坐标、`LS_Links` 的 npe=3..11 是任意多边形/多面体面而非二次面）；
  ② libccmio 面流虽可装任意 nVerts，但只是「多边形面」，无「边中点/曲线几何」语义；
  ③ legacy CCM 单元形状由 `CellTopologyType`（PROSTAR 形状码）表达，只有线性形状
  （tet/hex/wedge/pyramid/polygon/polyhedron=255），无二次形状码；
  ④ STAR-CCM+ 的 `STAR_QUADRATIC_*`（21–26/29）是**有限元求解器**的单元类型，与
  legacy CCM 的 FV 导入路径无关，UserGuide 明说 mesher 不生成 mid-side node、
  全文 0 处「curved mesh/cell」导入。#21 保持 ❌ 是正确且永久的（非「未适配」）。

### 推荐执行顺序

```
A1 → A2 → B3 → B1 → B4 → A3/A4 → B2 → C1 → （C2/C3 可行性结论已出：✅ 均可行，C2 依赖 FPH 结果文件排为独立里程碑、C3 无导入侧需求证据前靠后）→ D 滚动
```

理由：A 还的是当前用户可感知的质量债；B1/B3 让「导入后清单」从文档变成
工具，是**投入产出比最高**的一步；C1 解锁叶轮机械场景的周期配对刚需；
C2/C3 都有硬前置，先做可行性结论再排期，避免空转。
