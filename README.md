# gph2ccm

将 Software Cradle（scFLOW / scSTREAM）的 **GPH** 网格转换为
STAR-CCM+ 可导入的 legacy **CCM**（`.ccm`）文件。

```
GPH  →  gphdecoding（GPH 解析）→  libccmio / ccmio.dll（CCM 写出）
```

## 依赖

- Python 3.10+，`numpy`
- GPH 解析：同级仓库 `../gphdecoding`（含 `gph2cgns.py`），或用环境变量
  `GPH2CCM_GPHDECODING` 指定路径
- CCM 写出：`libccmio` 动态库。程序按以下顺序自动查找：
  1. 环境变量 `GPH2CCM_CCMIO_DLL`
  2. 本机 STAR-CCM+ 安装目录中的 `star\lib\win64\<arch>\lib\ccmio.dll`
  3. 系统 DLL 搜索路径中的 `ccmio.dll` / `libccmio.so` / `libccmio.dylib`

参考实现（不随本仓库分发）：`libccmio-2.6.1` 源码与文档
（`tests/libccmio-2.6.1/`，gitignored）。

## 用法

```bash
# 基本转换（默认在 GPH 旁生成 <stem>.ccm）
python -m gph2ccm mesh.gph

# 指定输出，并提供 CHT regions JSON（fluid_regions / solid_regions / boundary_types）
python -m gph2ccm mesh.gph out.ccm --regions mesh.json

# 校验生成文件（读回并检查拓扑一致性）
python -m gph2ccm mesh.gph out.ccm --verify
```

常用选项：

| 选项 | 说明 |
|------|------|
| `--regions JSON` | fluid/solid 区域与材料定义（同 gph2foam 的 regions JSON） |
| `--boundary-types JSON` | 边界区域 → CCM `BoundaryType` 覆盖，如 `{"open": "pressure"}` |
| `--force-material fluid\|solid` | 强制所有单元材料类型（便于单区域导入） |
| `--cell-topology none\|poly\|auto` | 显式写 `CellTopologyType`（默认 `poly=255`） |
| `--split-fluid-regions` | 多 fluid cell type 写成独立 STAR-CCM+ region（Region Cell
  Maps + `[Interface N]` 面 + `InterfaceDefinitions`），默认合并为一个 region |
| `--reorder rcm` | 写出前用 RCM 重排单元编号 |
| `--no-compress` | 跳过 `CCMIOCompress` |
| `--backup` | 已有输出时保留为 `.ccm.bak` 而非删除 |
| `--verify` | 转换后用 CCMIO 读回并做拓扑一致性校验 |

## 转换结果说明

转换器使用 GPH 的**全网格**数据（与 FLDUTIL 的 `FluidRegion` 一致）：

- 顶点/面/单元直接取自 `LS_Nodes` / `LS_Links`；坐标按 PROSTAR 惯例以
  mm 存储（scale=0.001）
- 单元类型来自 `LS_CvolIdOfElements` + `LS_Parts`，材料由 regions JSON 或
  名称启发式决定（fluid/solid）
- 边界区域来自 `LS_SurfaceRegions`；Cradle 会为同一组物理面输出多个重叠
  区域（如 `open` 与 `@PartSurface_air_domain`），转换时按
  “非 `@PartSurface_` 优先、其余按文件顺序”去重，保证每个边界面只出现一次
- 未覆盖的边界面进入 `Default_Boundary_Region`

## 已知限制

- **大网格曾卡在 STAR-CCM+ `Reordering` 阶段**：原因不是网格规模，而是
  CCMIO 写出的内部面 owner/neighbour 数组（`[2][n]` 二维数组）在分块写入
  时被本机 `ccmio.dll` 错位（每块按扁平偏移而不是按面偏移落盘），导致
  约一半单元没有任何面、另有一批单元只有 1~3 个面，STAR-CCM+ 重排时在
  坏拓扑上无法收敛。已修复：顶点坐标和内部面 owner/neighbour 改为单次
  写入（与 libccmio 参考 writer 一致），1-D 面流仍可分块。
  修复后 330 万单元 / 1044 万面网格可正常导入。
- **定位是「网格 + 描述」导出器，不是求解就绪导出器**：上游 GPH 只有几何
  与拓扑，没有结果场、材料属性、湍流/能量等物理模型。因此场变量、求解
  设置、MRF、周期/滑移配对等信息只以 `gph2ccm.*` 描述性元数据节点写入
  CCM（见下节），**不会**自动变成 STAR-CCM+ 里生效的物理条件。
- **单 processor**：只写单一 processor（单分区）网格，不做分区/并行分解。
- **不修网格**：只做只读质量诊断（`gph2ccm/diagnose.py`），不自动修复退化
  面、未覆盖边界面等问题。

## 描述性元数据（`gph2ccm.*` 节点）

当提供 regions JSON（或通过 API 直接传入）时，转换器把上游可知的物理信息
以 **命名空间化的 CCM opt 节点**写进 CCM 文件。这些节点只是"说明书"：
STAR-CCM+ 导入时会忽略它们，网格不受影响；但可以用
`ccmio` / `tools/` 下的脚本或 STAR-CCM+ 的 Java 宏读出来，**在导入后按图索骥
补齐设置**。

> 命名约束：libccmio 的 opt 节点名上限为 `K_CCMIO_MAX_STRING_LENGTH = 32`
> 个字符（含 `gph2ccm.` 前缀），超长会报
> `kCCMIOBadParameterErr (10)`。这也是质量节点用 `gph2ccm.Qual.*` 而不是
> `gph2ccm.Quality.*` 的原因。

| regions JSON 键 | 写入节点 | 编码格式 |
|------|------|------|
| `fields` | `gph2ccm.Field.<name>` | `<location>\|<type>\|<units>` |
| —（索引） | `gph2ccm.FieldNames` | 逗号分隔的场名列表 |
| `solver_settings` | `gph2ccm.Solver.<key>` | `str(value)` |
| —（索引） | `gph2ccm.SolverKeys` | 逗号分隔的键名列表 |
| `mrf` | `gph2ccm.MRF.<name>` | `<region>\|<type>\|<axis>\|<origin>\|<omega>\|<units>` |
| —（索引） | `gph2ccm.MRFNames` | 逗号分隔的 MRF 名列表 |
| `periodic` | `gph2ccm.Periodic.<name>` | `<region>\|<shadow>\|<type>\|<axis>\|<angle>` |
| —（索引） | `gph2ccm.PeriodicNames` | 逗号分隔的周期对名列表 |
| 边界区域 `params` | 边界区域上的参数字典 | 结构化的边界条件描述（仅类型与参数名，不含数值场） |
| 自动生成 | `gph2ccm.Note.Processors` | `1` |
| 自动生成 | `gph2ccm.Note.MultiProcessor` | `unsupported` |
| 自动生成 | `gph2ccm.Note.Dimension` | `2D` / `3D`（按顶点包围盒各方向跨度判断） |
| 自动生成 | `gph2ccm.Note.TwoDWrapping` | `unsupported`（2D 时）/ `n/a` |
| 自动生成 | `gph2ccm.Qual.Summary` | 网格规模与问题计数摘要 |
| 自动生成 | `gph2ccm.Qual.Uncovered` | 未归入任何边界区域的边界面数 |
| 自动生成 | `gph2ccm.Qual.Degenerate` | 退化边界面（顶点数 < 3）数 |
| 自动生成 | `gph2ccm.Qual.Issues` | 诊断出的问题列表 |

未提供 regions JSON 时，上述 `Field.*` / `Solver.*` / `MRF.*` / `Periodic.*`
节点不会写出，行为与旧版完全一致。

## 导入后须在 STAR-CCM+ 侧补充的清单

CCM 导入 STAR-CCM+ 后，下面这些**必须**人工补齐（转换器不会自动创建）：

- [ ] **材料与物理模型**：只读出 fluid/solid 单元分区，需要在
      `Continua → Physics` 里选材料（气体/液体/固体）、粘性/湍流模型、
      能量方程、多组分/多相。（对应 `gph2ccm.Field.*` 只描述有哪些场变量）
- [ ] **边界条件数值**：边界区域只有名字与 `BoundaryType`，具体数值
      （入口速度/流量、出口压力、壁面温度/热流、湍流量）需逐一填写。
- [ ] **初始条件**：CCM 不含结果场，需要在 `Initial Conditions` 里设定。
- [ ] **MRF 旋转条件**：`gph2ccm.MRF.*` 给出区域名、转轴、原点、角速度，
      但实际的 `Moving Reference Frame`（旋转区域、参考坐标系）要在
      STAR-CCM+ 里手动建立并指派到对应 region。（项 #3）
- [ ] **周期/滑移配对**：`gph2ccm.Periodic.*` 给出主/影子区域与转轴/角度，
      实际的 `Periodic Interface` / 滑移网格配对仍需手工创建。（项 #4）
- [ ] **交界面**：`--split-fluid-regions` 只写网格与
      `InterfaceDefinitions`（`IN_PLACE` / `InternalInterface`），
      STAR-CCM+ 通常会自动识别；若不识别，需手动创建 interface 并把两侧
      boundary 配对。
- [ ] **2D 处理**：`gph2ccm.Note.Dimension=2D` 仅作提示。若要用 2D 求解，
      需要把薄方向的两个面设为 `Symmetry`/`Empty` 并改用 2D 网格（当前
      不支持自动 2D 包裹）。（项 #6）
- [ ] **网格质量修复**：按 `gph2ccm.Qual.*` 的报告处理未覆盖边界面
      （`Uncovered`，会进 `Default_Boundary_Region`）与退化面
      （`Degenerate`）；转换器只诊断不修复。（项 #7）
- [ ] **求解器设置**：`gph2ccm.Solver.*` 列出上游的离散格式/松弛因子等键值，
      需要按 STAR-CCM+ 的对应项重新设置。（项 #1）
- [ ] **并行分区**：导出的 CCM 是单 processor 网格，导入后用 STAR-CCM+ 自身的
      分区器（或 `Reorder`）重新分解即可。（项 #5）

## 测试

```bash
# 直接跑（无需 pytest）
python tests/test_writer.py

# 或走 pytest
python -m pytest tests/test_writer.py -v
```

测试使用 libccmio 官方 writeexample 的 8 单元盒体：写入 CCM → CCMIO 读回
校验顶点/面/单元/边界区域/单元类型，并验证生成的 CCM 可被 STAR-CCM+
（本机 20.02.007 / 2502）正常导入。此外还覆盖 `gph2ccm.*` 描述性元数据、
processor/dimension note 与质量诊断。

**找不到 ccmio 动态库时不会失败，而是跳过**：依赖真实 CCMIO 的用例会
SKIP，纯逻辑用例照常执行，退出码仍为 0：

```bash
# 本机无 STAR-CCM+ / libccmio 时
GPH2CCM_CCMIO_DLL=/nonexistent/ccmio.dll python tests/test_writer.py
# → 3 passed, 0 failed, 11 skipped
```

### CI

`.github/workflows/tests.yml` 在 Ubuntu / Windows × Python 3.11 / 3.12 上
自动跑这套测试：托管 runner 没有 STAR-CCM+，写入/读回类用例会跳过，
导入检查与元数据/诊断用例仍会执行。若在装有 STAR-CCM+ 的 self-hosted
runner 上配置仓库 secret `GPH2CCM_CCMIO_DLL`（指向 `ccmio.dll` /
`libccmio.so`），则完整套件会全部启用。

辅助工具：

- `tools/make_demo_ccm.py`：生成笛卡尔六面体演示网格并写 CCM
- `tools/extract_subset.py`：从真实 GPH 提取前 N 个单元的子网格（诊断/预览）
- `tools/ImportCcmCheck.java`：STAR-CCM+ 批处理宏，导入 CCM 并打印网格统计
