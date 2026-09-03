# 版本行为对照表（D2）

记录 `gphdecoding`、`libccmio` 与 STAR-CCM+ 自带 `ccmio.dll` 的已知行为
差异与踩坑点，避免换版本后重新排查。所有条目均来自实测（本仓库测试、
`tools/dump_ccm.py` 与 STAR-CCM+ batch 验证）。

| # | 组件 | 现象 | 影响 / 规避 |
|---|------|------|-------------|
| 1 | STAR-CCM+ `ccmio.dll`（20.02.007-R8） | **2D 数组分块写入偏移错位**：`CCMIOWriteVerticesf` / `CCMIOWriteFaceCells` 等 2D 数组按块写入时，把 `start/end` 当作**扁平元素偏移**而非按行偏移（写 `[3][n]` 顶点，块起点 s 落盘到偏移 s 而非 3·s） | 顶点坐标与内部面 owner/neighbour 必须**单次调用**整体写入（`convert.py:_write_vertices` / `_write_face_group`）；1-D 面流（`CCMIOWriteFaces`）分块安全 |
| 2 | STAR-CCM+ `ccmio.dll` | **`CCMIOReadNodestr` 的 `char**` 签名**：与 libccmio-2.6.1 参考实现不同，本 dll 通过 `char**` 返回自分配缓冲，`char*` 单指针会导致段错误 | 读节点字符串用 `ctypes.c_char_p()` 传指针到指针（`tests/test_writer.py:_str`） |
| 3 | libccmio / ccmio.dll | **opt 节点名上限 `K_CCMIO_MAX_STRING_LENGTH = 32`**：超长报 `kCCMIOBadParameterErr (10)` | `gph2ccm.` 前缀 + 名称总长 ≤ 32；regions JSON 校验器在转换前拦截（`regions_schema.py:_check_name_length`） |
| 4 | libccmio-2.6.1 | **公开 API 无通用子节点枚举**：`CCMIONextEntity` 只能按实体类型遍历；无法枚举任意命名的 opt 子节点 | 写入侧必须自带索引节点（`gph2ccm.FieldNames` / `SolverKeys` / `MRFNames` / `PeriodicNames` / `BCKeys`），`gph2ccm inspect`（B1）才能读回 |
| 5 | STAR-CCM+ 2502 | **`Simulation` 无 `getBoundaryManager()`**：边界管理器挂在 `Region` 上，且 `getRegions()` 返回 `Collection`（无 `get(int)`） | 宏生成器（B2）用 `findBoundary` 遍历所有 region 查边界；不能用 `sim.getBoundaryManager()` |
| 6 | STAR-CCM+ 2502 | **`ReferenceFrameManager` 仅在已初始化会话注册**：全新 `starccm+ -new -batch` 空会话中 `sim.getReferenceFrameManager()` 返回 `AbstractReferenceFrameManager` 基类，`createReferenceFrame` 抛 `Manager not found in ManagerManager` | MRF 宏块在 GUI / 已导入网格的会话中运行；空会话中 try/catch 告警跳过（README 已注明） |
| 7 | STAR-CCM+ 2502 | **`InterfaceManager.createDirectInterface(Boundary,Boundary)` 已过时**（deprecation warning，仍可用） | 宏继续使用并接受 warning；如未来版本移除，改 `createBoundaryInterface(Boundary,Boundary,String)` |
| 8 | STAR-CCM+ 原生导出 | **interface 的 face-id 编码**：原生单文件全局 face-id 空间，interface 两侧是重复真实 id（ami_out/ami_in 各占真实 id 段），maxID 不超真实面数 | 本工具用 `fids+1+k·n_faces` 虚拟 id（maxID 达 ~2.5·n_faces），已实测 20.02.007-R8 可导入；残余风险见 `conversion_issues_analysis.md` H2 |
| 9 | STAR-CCM+ 原生导出 | **interface 定义存于 `InterfaceDefinitions` 节点**（root 子节点，`Boundary0/Boundary1/Configuration/ConditionType`），而非 `kCCMIOInterfaces` 实体（原生文件该实体数为 0） | 本工具写同一节点结构（grid= `InternalInterface`，周期= `PeriodicInterface`，C1） |
| 10 | STAR-CCM+ 2502 启动器 | **sh 启动器对含空格路径解析失败**（`starccm+` 报 "Corrupted installation"，把 `/c/Program Files` 截断） | 用 junction 镜像无空格安装布局（`C:\sc8` = star+boost 等全部分包，`C:\jdk`），或直接调 `.bat` |
| 11 | STAR-CCM+ 2502 启动器 | **`.bat` 内部调用 `wmic.exe`**（被部分沙箱/安全策略拦截）；headless batch 需 license（`license.dat`，`ccmpsuite`） | 无 license 环境无法 batch 验证；ccmio.dll 写文件本身不需要 license |
| 12 | gphdecoding | **GPH 网格无结果场**；同体系 **FPH 文件（魔数 `CRDL-FLD`）携带 FlowSolution**（`fph2cgns.py` 已解析） | C2（初始场写入）依赖 FPH 输入扩展，见评估文档 §6-C2 结论 |
| 13 | gphdecoding | 结果 dict 布局：`link_data`（`npe/face_nodes/face_offsets/owner/neighbor/boundary_faces`）+ `vertices` + `cvol_id/parts_with_cvol/volume_regions/surface_regions` | `_face_unique_vertices` 等必须用 `face_offsets` 索引 `face_nodes`（`_face_starts` 是 CCM 流偏移，不可混用） |
| 14 | libccmio | `CCMIOWriteProcessor` 的 `verticesFile/topologyFile/initialFieldFile/solutionFile` 参数：并行 CCM = 主文件 + 每分区独立文件 | C3（多 processor）采用每分区单文件方案，见评估文档 §6-C3 结论 |
| 15 | legacy CCM / STAR-CCM+ | **无高阶（二次）单元语义**：上游 GPH 是线性网格（无 mid-side node）；libccmio 面流任意 nVerts 只是多边形面、无「边中点/曲线」语义；`CellTopologyType`（PROSTAR 形状码）只有线性形状（tet/hex/wedge/pyramid/polygon/polyhedron=255），无二次形状码；STAR-CCM+ 的 `STAR_QUADRATIC_*`（21–26/29）属有限元求解器、与 legacy CCM FV 导入无关，UserGuide 全文 0 处「curved mesh」导入 | 高阶单元导出**不可行**（#21 永久 ❌），见评估文档 §6-D4；若未来上游出现真高阶数据源，须改用 STAR-CCM+ 原生格式而非 legacy CCM |
| 16 | libccmio-2.6.1 / ccmio.dll | **Field 写入配方（C2 实测）**：`FieldSet`（root 子节点）→ `CCMIONewIndexedEntity(FieldPhase, idx)` → `CCMIONewField(name, shortName, dim)` → `CCMIONewEntity(FieldData)` + `CCMIOWriteFieldDataf(mapID, kCCMIOCell, …)`；向量场按官方 writeexample 拆 X/Y/Z 标量分子场后 `CCMIOWriteMultiDimensionalFieldData` 链接；最后 FieldSet 经 `CCMIOWriteProcessor(solution=…)` 挂 processor。读回必须走 `CCMIONextEntity`（FieldData 子实体 `CCMIOGetEntity` 不可靠），向量分量经 `CCMIOReadMultiDimensionalFieldData` | field name ≤32 字符、prostar 短名 ≤8 字符（分量短名占末位 X/Y/Z）；`model.solution_fields` 归一化时 fail-fast 拦截（`model.py:_normalize_solution_fields`）。1M 单元 ×4 场实测：+16 MB 文件、+2.2 s 写入、读回数值正确 |

## 维护约定

- 换用新的 STAR-CCM+ / libccmio 版本时，**先跑 `python tests/test_writer.py`**；
  若本机装有 STAR-CCM+，再跑一次 B2 宏的 batch 验证（`starccm+ -batch`）。
- 新发现的行为差异**必须**追加到此表（带版本号与复现条件），并同步
  更新 `conversion_issues_analysis.md` 与 README 相关段落。
