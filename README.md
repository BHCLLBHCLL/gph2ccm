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
- 暂不写结果场/求解设置，仅网格与问题描述（单元类型、边界区域）。

## 测试

```bash
python tests/test_writer.py
```

测试使用 libccmio 官方 writeexample 的 8 单元盒体：写入 CCM → CCMIO 读回
校验顶点/面/单元/边界区域/单元类型，并验证生成的 CCM 可被 STAR-CCM+
（本机 20.02.007 / 2502）正常导入。

辅助工具：

- `tools/make_demo_ccm.py`：生成笛卡尔六面体演示网格并写 CCM
- `tools/extract_subset.py`：从真实 GPH 提取前 N 个单元的子网格（诊断/预览）
- `tools/ImportCcmCheck.java`：STAR-CCM+ 批处理宏，导入 CCM 并打印网格统计
