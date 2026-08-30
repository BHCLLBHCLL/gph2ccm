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
| 6 | BoundaryType | ⚠️ | 启发式 / JSON 覆盖类型名，但**无对称 / 周期 / 旋转的附加定义** |
| 7 | Region Cell Maps（split 模式） | ✅ | 每 region 一个 `Region Cell Map <label>` |
| 8 | Grid Interface（split 模式） | ✅ | 两侧边界 patch + `[Interface N]` + `InterfaceDefinitions` |
| 9 | CCMIOCompress | ✅ | 写出后压缩 |
| 10 | Reorder（RCM 单元重排） | ✅ | 可选，降低导入重排开销 |
| 11 | Verify 读回校验 | ✅ | H1 已修复：split 模式 interface 两侧共享面不再误报（relaxed 校验） |
| 12 | Multiple Processors（分布式） | ❌ | 单 processor（legacy CCM 固有限制）；已写入 `gph2ccm.Note.Processors`/`MultiProcessor` 自说明元数据 + 大网格告警 |
| 13 | Solution Fields（结果场） | ⚠️ | 不写实际场数据；可经 `regions["fields"]` 写入 `gph2ccm.Field.*` **描述性**场定义 |
| 14 | Boundary Conditions（含对称 / 周期配对） | ⚠️ | 结构化类型规范化 + `gph2ccm.BC.*` 描述性参数（`regions` JSON 驱动），仍非求解就绪 |
| 15 | Material / Region 求解属性 | ⚠️ | 新增 `gph2ccm.Solver.*` 描述性求解设置元数据（`regions` JSON 驱动），仍非实际属性 |
| 16 | MRF（旋转参考系） | ⚠️ | 不自动施加旋转条件；可经 `regions["mrf"]` 写入 `gph2ccm.MRF.*` **描述性**旋转参考系声明 |
| 17 | Periodic / Cyclic 配对 | ⚠️ | 不生成几何配对界面；可经 `regions["periodic"]` 写入 `gph2ccm.Periodic.*` **描述性**配对声明 |
| 18 | 2D 网格包裹 | ❌ | 不挤出壳层；新增 2D 检测 + `gph2ccm.Note.Dimension`/`TwoDWrapping` 自说明（包裹不在范围内） |
| 19 | 单元 / 节点场数据 | ❌ | 无 |
| 20 | 网格质量修复 | ❌ | 仅 `topo_check.py` 诊断，不修复 |
| 21 | 高阶单元（2 阶） | ❌ | legacy CCM 一般不支持，未做适配 |

**完整度统计**：21 项中已实现 12、部分 6、缺失 3 → **核心网格层完整覆盖，
物理 / 求解层进入「描述性元数据」阶段（可携带信息但非求解就绪）**。

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

- **测试自动化薄弱**：`tests/test_writer.py` 仅 2 个用例，覆盖基础读写与
  model 组装；**split_regions / interface / verify-split 路径无自动化测试**，
  依赖手动 STAR-CCM+ 导入验证。改 `ccmio.py` 无回归保护。
- **精度**：顶点 float32 对大尺度坐标有精度上限（已知限制）。
- **法向定向**：`cell_centroids` 用面心算术均值近似（非体积加权），畸形切单元
  上可能误判 interface 法向（L3）。
- **接口虚拟 face id 的 `max_id` 语义**（H2）仍建议与 STAR-CCM+ 原生再核对。
- **异常安全**：`write()` 无 `try/finally`，异常时文件句柄泄漏（M4）。
- **参数失效**：`--chunk-vertices` 因 2D 分块 bug 实际不生效（M1）。

---

## 3. 工程成熟度

| 维度 | 评价 |
|---|---|
| 架构分层 | ✅ 清晰：deps（解析）/ model（模型）/ ccmio（绑定）/ convert（编排）/ verify / reorder；复用 `gphdecoding` 与 `ccmio.dll`，不自造轮子 |
| 文档 | ✅ 四件套（README / AGENTS / DEV_SUMMARY / 问题清单）齐全且**基本与代码一致**（split 已实现，文档未超前） |
| 工具链 | ✅ 完整：dump / topo_check / make_demo / make_two_region / extract_subset / ImportCcmCheck.java + 4 份真实样本 dump |
| 测试 | ⚠️ 仅 2 个用例，缺 split / interface / verify 路径；无 CI |
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

> 执行进度（对应原始 8 项清单）：#8 测试 ✅、#2 边界条件结构化 ✅、#1 结果场/求解设置 ⚠️（描述性元数据载体）、#3 MRF ⚠️（描述性声明）、#4 周期/滑移配对 ⚠️（描述性声明）、#5 多 processor ⚠️（限制自说明）、#6 2D 包裹 ⚠️（检测+自说明）、#7 待执行。

1. **无结果场 / 求解设置** —— ⚠️ 已有描述性载体：经 `regions["fields"]` / `regions["solver_settings"]` 写入 `gph2ccm.Field.*` / `gph2ccm.Solver.*` 命名空间节点；**非实际场数据、不求解**（"keep boundary" 范围）。
2. **边界条件仅类型名** —— ⚠️ 已结构化：`boundary_conditions` 经 `_normalize_bctype` 规范化类型 + `gph2ccm.BC.*` 描述性参数（regions JSON 驱动），仍非求解就绪。
3. **MRF 仅 region 划分** —— ⚠️ 已有描述性旋转参考系声明：经 `regions["mrf"]` 写入 `gph2ccm.MRF.*`（region/type/axis/origin/omega/units），**非真实旋转条件**。
4. **周期 / 滑移界面配对** —— ⚠️ 已有描述性配对声明：经 `regions["periodic"]` 写入 `gph2ccm.Periodic.*`（region/shadow/type/axis/angle），**未生成几何配对界面**。
5. **多 processor / 分布式** —— ⚠️ 已自说明：legacy CCM 单 processor 固有限制，写入 `gph2ccm.Note.Processors`/`MultiProcessor` 元数据 + 大网格（>200 万 cell）告警；**无分布式分区写入**。
6. **2D 网格包裹** —— ⚠️ 已检测并自说明：识别 collapsed-axis 的 2D 网格，写入 `gph2ccm.Note.Dimension`/`TwoDWrapping` 元数据；**不挤出壳层**（超出 keep-boundary 范围）。
7. **网格质量修复** —— 仅诊断，不修退化面 / 悬挂节点（待 #7）。
8. **测试与 CI 薄弱** —— ✅ 已补 `tests/test_writer.py` 10 个回归用例（写回读验证、split/interface、verify 放宽、结构化 BC、字段/求解/MRF/周期元数据）。

---

## 6. 建议路线

- **短期（保质量，低成本）**：✅ 已完成 —— H1 修复 + `verify` 在 `split_regions` 下放宽接口面唯一性 + 对应单测；结构化边界条件 + 描述性结果场/求解元数据（数据驱动、纯描述）。
- **中期（扩广度）**：结果场/MRF/周期 **描述性元数据载体已落地**（数据驱动、纯描述）；
  真正的求解属性写入（待上游 GPH 提供实际数据）、多 processor 分块写入
  （需先解决 2D 分块 bug 的 vertex 路径）。
- **长期（工程化）**：CI 接入 `test_writer.py` + 至少 1 个合成多 region 网格；
  在 README 明确「导入后须在 STAR-CCM+ 侧补充」的清单（材料 / 边界条件 / 场）；
  建立 `gphdecoding` 解析层与 `ccmio.dll` 行为的版本对照表。
