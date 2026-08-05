# gph2ccm 开发总结

> 将 Software Cradle（scFLOW / scSTREAM）导出的 **GPH** 网格转换为
> STAR-CCM+ 可导入的 legacy **CCM**（`.ccm`）文件。
>
> 本文档汇总整体转换思路、开发中遇到的关键问题及最终解决方案，供后续
> 维护与二次开发参考。配套文档：`README.md`（用法）、`AGENTS.md`
> （协作约定）、`conversion_issues_analysis.md`（代码问题清单）。

---

## 1. 项目概述

### 1.1 目标

- 输入：Cradle GPH 网格（`CRDL-FLD` 二进制格式），可选 regions JSON
  （fluid/solid 分区、MRF、边界类型）。
- 输出：STAR-CCM+ 可正常导入的 legacy CCM 网格文件（含单元类型、边界
  区域、多 region/interface 支持）。
- 约束：**不重新实现 GPH 二进制解析**（复用 `gphdecoding`），
  **不重新实现 CCM 二进制格式**（驱动 `ccmio.dll` / `libccmio`）。

### 1.2 仓库结构

```
gph2ccm/
  deps.py      # 定位并导入同级 gphdecoding（GPH 解析）
  ccmio.py     # ctypes 绑定 ccmio.dll / libccmio（CCM 读写 API）
  model.py     # GPH -> CCM 网格模型（单元、面、边界、接口）
  convert.py   # 编排：模型组装 + CCMIO 写入 + 压缩 + 校验
  reorder.py   # RCM 单元重排（可选）
  verify.py    # 读回一致性校验
  __main__.py  # CLI
tools/
  dump_ccm.py            # CCM 实体树/结构转储（对比诊断）
  topo_check.py          # 拓扑健康检查（单元面数/退化面/闭合性）
  make_two_region_ccm.py # 合成两 fluid region 验证网格
  make_demo_ccm.py       # 合成笛卡尔六面体演示网格
  extract_subset.py      # 从真实 GPH 提取子网格
  ImportCcmCheck.java    # STAR-CCM+ 批量导入宏（打印 region/interface）
tests/                   # 大样本与回归测试（gitignored）
```

---

## 2. 整体转换思路

### 2.1 数据流

```text
GPH ──> gphdecoding.parse_gph_mesh() ──> gph2ccm.model.build_model()
      ──> CcmModel ──> CcmMeshWriter（ccmio.dll）──> .ccm ──> CCMIOCompress
      ──> verify_ccm（可选）──> STAR-CCM+ 导入验证
```

### 2.2 GPH 数据到 CCM 实体的映射

| GPH 数据 | CCM 实体 |
|---|---|
| `LS_Nodes`（顶点） | `Vertices`（float32，mm，scale=0.001） |
| `LS_CvolIdOfElements` + `LS_Parts` | `Cells` + `CellType`（Label/MaterialType/GroupId/MaterialId） |
| `LS_Links`（npe/face_nodes/owner/neighbor） | `InternalFaces` / `BoundaryFaces` |
| `LS_SurfaceRegions` | `BoundaryRegion`（Label/BoundaryType），重叠区域去重 |
| 跨 cell type 的内部面（split 模式） | 两侧边界 patch + `[Interface N]` 面 + `InterfaceDefinitions` |
| 每 region 单元集合（split 模式） | `Region Cell Map <label>` |
| — | `State` -> `Processor` -> `Vertices`/`Topology` 引用 |

### 2.3 关键技术决策

1. **用 CCMIO 库而不是手写 CCM 二进制**：CCM 是 ADF 容器格式，结构复杂；
   本机 STAR-CCM+ 安装自带 `ccmio.dll`（完整读写 API），与 libccmio 2.6.1
   源码（`tests/libccmio-2.6.1/`）对照，用 ctypes 绑定即可可靠读写。
2. **参考 OpenFOAM `ccmWriter`**：写入顺序（Map -> Vertices -> Topology ->
   Cells -> InternalFaces -> BoundaryFaces -> ProblemDescription -> State ->
   Processor）与 libccmio 参考 writer 保持一致。
3. **以 STAR-CCM+ 原生导出文件为基准**：多 region/interface 的实现不是
   猜测，而是逆向 `tests/bladerotating_dm2.ccm`（STAR-CCM+ 导出、双 region、
   可正常导入）得到的结构。

---

## 3. 关键问题与解决方案

### P1：STAR-CCM+ 导入在 `Reordering` 阶段卡死（最重要的 bug）

**现象**：330 万单元 / 1044 万面的 Cradle cut-cell 网格，STAR-CCM+ 能读完
网格，但导入在 `Reordering` 阶段持续占用单核 CPU，长时间不结束。10 万
单元约 15 s、30 万单元约 4 min，呈超线性增长，一度被误判为“大网格固有
耗时”。

**定位过程**：

1. 用 `tools/dump_ccm.py` 对比问题文件与正常参考
   `bladerotating_dm2.ccm`（41.6 万单元、全部为闭合四面体）。
2. 用 `tools/topo_check.py` 检查拓扑：问题文件 **155.4 万单元（46.6%）没有任何
   面**、另有 6 万单元只有 1~3 个面（开放单元）；而 GPH 源数据每单元
   6/9/12/15/18/21 个面、完全健康。结论：**问题出在 CCM 写出环节**。
3. 逐块抽查内部面 owner/neighbour：前 50 万面与 GPH 完全一致，之后全部
   错位；错位规律为“每块写到一半偏移”，恰好在 `chunk_faces=500000`
   分块边界处。

**根因**：`ccmio.dll` 的 2D 数组分块写入与 libccmio 2.6.1 源码语义不一致。
`CCMIOWriteFaces` 的 `start/end` 是 1D 流的元素偏移（分块安全）；但
`CCMIOWriteVerticesf`（`[3][n]`）与内部面 `CCMIOWriteFaceCells`
（`[2][n]`）按“概念单元（面/顶点索引）”传 `start/end` 时，DLL 实际按
扁平元素偏移落盘（少乘第一维长度），导致每块错位 1/2 或 1/3。结果：
大量单元丢失面、面被错误挂到别的单元，STAR-CCM+ 重排无法收敛。

**解决**：

- 顶点坐标、内部面 owner/neighbour 改为**单次 CCMIO 写入**
  （`start=0, end=END`），与 OpenFOAM `ccmWriter` 一致；
- 1D 面流（`FacesVertexData`）仍可分块写入（安全）；
- 修复后重新生成，`topo_check` 每单元面数分布与 GPH 完全一致，闭合检查
  0 异常，STAR-CCM+ 导入正常完成。

**教训**：CCMIO 的 `start/end` 语义要按“数组维数”区分；2D 数组分块写入
在当前 `ccmio.dll` 上不可用，已在 AGENTS.md 中固化约束。

### P2：多个 fluid cell type 被 STAR-CCM+ 合并成一个 region

**现象**：air_domain 与 rotation1 都是 fluid，转换后导入只有一个
`air_domain` region，两个区域被合并；用户要求两个独立 region 并通过
interface 面连接。

**定位过程**：逐项对比 STAR-CCM+ 原生导出文件 `bladerotating_dm2.ccm`：

1. **CellType 节点多出 `GroupId` / `MaterialId`**：airZone=1、bladeZone=2，
   而 material 都是 fluid。STAR-CCM+ 按 GroupId 建 region（默认按
   material 分组才会合并）。
2. **根节点多出 `Region Cell Map <label>`**：每 region 一个，值为该
   region 的全局单元 id。
3. **根节点多出 `InterfaceDefinitions`**：子节点 `Interface-N`，字段为
   `Name`、`Boundary0`/`Boundary1`（指向两侧带单元数据的边界区域 id）、
   `Configuration=IN_PLACE`、`ConditionType=InternalInterface`。
4. **`[Interface N]` 边界区域**：无 `FacesCellData`，导入日志显示
   “Skipping boundary id ... with no associated cells”，但仍用于生成
   Grid Interface（导入后被消费为 0 面）。
5. **跨 region 面**：两 region 之间没有内部面；每个接口面在两侧各作为
   一个边界 patch（带 owner cell）出现。

> 逆向 `InterfaceDefinitions` 时，CCMIO 高层 API 读不到该节点内容，
> 最终按 libadf 源码用 Python 直接解析 ADF 二进制（子节点表 16 字节头 +
> 44 字节条目、`DaTa`/`dEnDNoDe` 数据块）才拿到字段与取值。

**解决**（`--split-fluid-regions` 模式）：

- CellType 写 `GroupId`（每 region 唯一）与 `MaterialId`（fluid=1）；
- 写 `Region Cell Map <label>`；
- 识别跨 cell type 的内部面（simplec 中 412,644 个四边形），从
  `InternalFaces` 移除，写成两侧边界 patch（`air_to_rotation1` /
  `rotation1_to_air`，带 owner cell），并写 `[Interface N]` 无单元数据面；
- 用 Newell 法向 + 单元面心均值做两侧面定向（normal 指向对侧单元）；
- 根节点写 `InterfaceDefinitions`。

**验证**：合成两块体网格（`tools/make_two_region_ccm.py`）导入得到两个
region + `Interface 1`；真实 simplec 文件导入得到
`air_domain`（1,650,528 cells）+ `rotation1`（1,419,370 cells）+
`INTERFACE BoundaryInterface Interface 1`，两侧接口面各 412,644。

### P3：Cradle 边界区域重叠导致同一物理面重复

Cradle 会把同一组物理面导出到多个 `LS_SurfaceRegions`（如 `open` 与
`@PartSurface_air_domain`）。CCM 要求每个边界面只属于一个 region。

**解决**：`build_boundary_regions` 按“非 `@PartSurface_` 优先、其余按文件
顺序”去重；未覆盖的边界面进 `Default_Boundary_Region`。

### P4：CCMIO API 语义陷阱

1. `CCMIOGetEntity(parent, type, index, id)` 的第 4 参是**实体 id** 而非
   序号（`Topology` 的 id 不一定是 0）；统一用 `CCMIONextEntity` 迭代。
2. `CCMIOWriteOpt1i` / `CCMIOWriteCells` 要传**数组总长** + 分块指针 +
   全局 start/end；`CCMIOWriteFaces` 传流总长 + 流内偏移。
3. 库内部使用静态节点缓存，跨实体交错分块写入会相互污染；因此同一实体
   的分块调用要连续完成。
4. `CCMIONewEntity` 的节点名由库自动生成（如 `BoundaryRegion-13`），
   不要手动 `CCMIOSetName` 改名——会破坏 id 解析（导入日志 id 变 0）。

### P5：STAR-CCM+ 批量验证环境问题

- `-licpath 'C:\Program Files\...'` 带空格的值会被 cmd 拆开导致
  “Invalid file type”，改用 8.3 短路径 `C:\PROGRA~1\Siemens\license.dat`。
- Java 宏枚举接口用 `InterfaceManager.getObjects()`（该版本没有
  `getInterfaces()`）。
- 批处理宏硬编码待导入文件路径，验证不同文件时需改
  `tools/ImportCcmCheck.java`。

### P6：libccmio 源码与 DLL 行为不一致

`tests/libccmio-2.6.1/` 是 CD-adapco 开源参考实现（含 `ccmio.h`、
`ccmioread.c`、ADF 源码），但本机 STAR-CCM+ 自带 `ccmio.dll` 的实现细节
（尤其 2D 分块写入）与之不同。开发中始终以 **DLL 实测行为** 为准，源码
仅用于查签名与结构。

---

## 4. 验证方法

1. **读回校验**：`verify_ccm` 读回顶点/面/单元/边界并检查一致性；
   `tests/test_writer.py` 用合成 8 单元盒体回归。
2. **结构对比**：`tools/dump_ccm.py` 输出实体树、map 范围、face-cells
   统计、ProstarFaceId 等，用于与 STAR-CCM+ 原生导出文件逐项对比。
3. **拓扑健康检查**：`tools/topo_check.py` 检查每单元面数、退化面、
   重复顶点、闭合性（每单元每条边恰好被两个面使用）。
4. **端到端导入**：`tools/ImportCcmCheck.java` 通过 STAR-CCM+ batch 导入
   并打印 regions / interfaces / boundaries，是最终验收标准。

---

## 5. 当前能力与限制

### 已支持

- GPH -> CCM（顶点/内部面/边界/单元类型/边界区域）；
- 重叠边界区域去重、`Default_Boundary_Region`；
- `--cell-topology`（poly=255）、`--reorder rcm`、`--verify`、压缩；
- `--split-fluid-regions`：多 fluid region + `[Interface N]` +
  `InterfaceDefinitions`；

### 限制 / 已知问题

- 只写网格与问题描述，不写结果场/求解设置（MRF、边界条件等需在
  STAR-CCM+ 中补充）；
- 顶点仅 float32（CCM 格式限制），大坐标网格存在精度上限；
- `--chunk-vertices` 因 2D 分块 bug 实际不生效（见
  `conversion_issues_analysis.md` M1）；
- `verify_ccm` 在 split 模式对 interface 两侧共享物理面会误报重复
  （H1），`write()` 异常时缺少 finally 清理（M4）等代码质量问题已单独
  记录，待按优先级修复；
- interface 虚拟 face id 的 `max_id` 语义（H2）建议后续与 STAR-CCM+
  原生导出再核对。

---

## 6. 关键结论

1. 大网格导入卡死在 `Reordering` 的根因是 **2D 数组分块写入错位**，不是
   网格规模；修复后 330 万单元网格可正常导入。
2. 多 fluid region + interface 的 CCM 表达是
   **GroupId + Region Cell Map + 两侧边界 patch + InterfaceDefinitions**，
   全部来自对 STAR-CCM+ 原生导出文件的逆向。
3. 任何结构改动都应通过“合成网格 + STAR-CCM+ batch 导入”闭环验证，
   不能只依赖 CCMIO 读回。
