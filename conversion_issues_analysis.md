# gph2ccm 转换代码问题分析

分析范围：`gph2ccm/` 包（convert / model / ccmio / verify / reorder / deps / __main__）。
结论按严重性分组，每条标注文件:行号、原因、影响、建议。

## 问题分布概览

<svg viewBox="0 0 680 470" width="100%" role="img" aria-label="gph2ccm 转换代码问题按严重性分布">
  <title>gph2ccm 转换代码问题分布</title>
  <desc>按高、中、低三档严重性分组列出转换代码中的问题及其位置</desc>
  <defs>
    <marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path d="M2 1L8 5L2 9" fill="none" stroke="context-stroke" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
    </marker>
  </defs>
  <text x="340" y="30" text-anchor="middle" font-size="15" font-weight="500" fill="#2C2C2A">gph2ccm 转换代码问题分布</text>
  <text x="340" y="50" text-anchor="middle" font-size="12" fill="#888780">10 项问题 · 按严重性分组 · 标注文件位置</text>

  <rect x="40" y="72" width="190" height="30" rx="6" fill="#FCEBEB" stroke="#E24B4A" stroke-width="0.5"/>
  <text x="135" y="91" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#791F1F">高 · 影响正确性</text>

  <rect x="245" y="72" width="190" height="30" rx="6" fill="#FAEEDA" stroke="#EF9F27" stroke-width="0.5"/>
  <text x="340" y="91" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#854F0B">中 · 功能 / 精度</text>

  <rect x="450" y="72" width="190" height="30" rx="6" fill="#E6F1FB" stroke="#378ADD" stroke-width="0.5"/>
  <text x="545" y="91" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#0C447C">低 · 设计 / 健壮</text>

  <rect x="40" y="114" width="190" height="58" rx="8" fill="#FCEBEB" stroke="#E24B4A" stroke-width="0.5"/>
  <text x="52" y="136" dominant-baseline="central" font-size="13" font-weight="500" fill="#791F1F">H1 verify 在 split 模式误报</text>
  <text x="52" y="156" dominant-baseline="central" font-size="11" fill="#A32D2D">convert.py:330 · verify.py:134</text>

  <rect x="40" y="180" width="190" height="58" rx="8" fill="#FCEBEB" stroke="#E24B4A" stroke-width="0.5"/>
  <text x="52" y="202" dominant-baseline="central" font-size="13" font-weight="500" fill="#791F1F">H2 interface 虚拟 id 语义存疑</text>
  <text x="52" y="222" dominant-baseline="central" font-size="11" fill="#A32D2D">convert.py:349,357 · max_id</text>

  <rect x="245" y="114" width="190" height="58" rx="8" fill="#FAEEDA" stroke="#EF9F27" stroke-width="0.5"/>
  <text x="257" y="136" dominant-baseline="central" font-size="13" font-weight="500" fill="#854F0B">M1 chunk_vertices 参数失效</text>
  <text x="257" y="156" dominant-baseline="central" font-size="11" fill="#BA7517">convert.py:198 · CLI 误导</text>

  <rect x="245" y="180" width="190" height="58" rx="8" fill="#FAEEDA" stroke="#EF9F27" stroke-width="0.5"/>
  <text x="257" y="202" dominant-baseline="central" font-size="13" font-weight="500" fill="#854F0B">M2 顶点 float32 精度损失</text>
  <text x="257" y="222" dominant-baseline="central" font-size="11" fill="#BA7517">convert.py:389 · 大尺度网格</text>

  <rect x="245" y="246" width="190" height="58" rx="8" fill="#FAEEDA" stroke="#EF9F27" stroke-width="0.5"/>
  <text x="257" y="268" dominant-baseline="central" font-size="13" font-weight="500" fill="#854F0B">M3 label 截断可能碰撞</text>
  <text x="257" y="288" dominant-baseline="central" font-size="11" fill="#BA7517">convert.py:159 · interface</text>

  <rect x="245" y="312" width="190" height="58" rx="8" fill="#FAEEDA" stroke="#EF9F27" stroke-width="0.5"/>
  <text x="257" y="334" dominant-baseline="central" font-size="13" font-weight="500" fill="#854F0B">M4 异常时句柄泄漏</text>
  <text x="257" y="354" dominant-baseline="central" font-size="11" fill="#BA7517">convert.py:361 · 无 finally</text>

  <rect x="450" y="114" width="190" height="58" rx="8" fill="#E6F1FB" stroke="#378ADD" stroke-width="0.5"/>
  <text x="462" y="136" dominant-baseline="central" font-size="13" font-weight="500" fill="#0C447C">L1 face_cells 写入顺序</text>
  <text x="462" y="156" dominant-baseline="central" font-size="11" fill="#185FA5">convert.py:222 · 先于 faces</text>

  <rect x="450" y="180" width="190" height="58" rx="8" fill="#E6F1FB" stroke="#378ADD" stroke-width="0.5"/>
  <text x="462" y="202" dominant-baseline="central" font-size="13" font-weight="500" fill="#0C447C">L2 cvol 覆盖无告警</text>
  <text x="462" y="222" dominant-baseline="central" font-size="11" fill="#185FA5">model.py:125 · 静默</text>

  <rect x="450" y="246" width="190" height="58" rx="8" fill="#E6F1FB" stroke="#378ADD" stroke-width="0.5"/>
  <text x="462" y="268" dominant-baseline="central" font-size="13" font-weight="500" fill="#0C447C">L3 质心用面心均值</text>
  <text x="462" y="288" dominant-baseline="central" font-size="11" fill="#185FA5">convert.py:110 · 近似</text>

  <rect x="450" y="312" width="190" height="58" rx="8" fill="#E6F1FB" stroke="#378ADD" stroke-width="0.5"/>
  <text x="462" y="334" dominant-baseline="central" font-size="13" font-weight="500" fill="#0C447C">L4 processor 逻辑冗余</text>
  <text x="462" y="354" dominant-baseline="central" font-size="11" fill="#185FA5">ccmio.py:553 · 可读性</text>

  <line x1="40" y1="400" x2="640" y2="400" stroke="#B4B2A9" stroke-width="0.5" stroke-dasharray="3 3"/>
  <text x="40" y="420" dominant-baseline="central" font-size="12" fill="#5F5E5A">已正确处理（无需修改）</text>
  <text x="40" y="440" dominant-baseline="central" font-size="11" fill="#888780">顶点/face_cells 单次写入 · 边界 region 去重 · face_stream 1-D 分块 · RCM 重排</text>
</svg>

---

## 一、高严重性 — 影响正确性或可能导致导入失败

### H1. `split_regions` + `--verify` 必然误报
- **位置**：`convert.py:330-340`（interface 两侧 volume patch 都用真实 face id）vs `verify.py:130-136`
- **原因**：`_write_interfaces` 为每个 interface 写两份 volume patch（side A / side B），
  两份的 boundary-face map 都传 `face_ids=fids`，`_write_boundary_patch` 内
  `map_data = face_ids + 1`，于是 A、B 两个 region 的 map_ids 完全相同。
  而 `verify_ccm` 第 134 行断言「boundary face ids must be unique across regions」。
- **影响**：同时使用 `--split-fluid-regions --verify` 时，校验一定抛
  `AssertionError: duplicate boundary face ids across regions`，即使文件本身正确。
  interface 两侧共享同一物理面是预期行为，verify 的唯一性检查未区分这种情况。
- **建议**：verify 对 `split_regions` 模式应跳过 interface region 的唯一性检查，
  或按「每物理面允许出现在恰好两个 region」的语义放宽断言。

### H2. grid-interface 虚拟 face id 的 `max_id` 语义 — ✅ 已验证，记录差异（2026-09-01）
- **位置**：`convert.py` `_write_interfaces`（surface patch 用 `fids + 1 + n_faces` 与
  `fids + 1 + 2*n_faces` 作为 map 值，`_add_map` 据此把 `max_id` 设为接近
  `2*n_faces` / `3*n_faces`）；文件实际只有 `n_faces` 个面。
- **验证方法**：分别 dump 两份 `.ccm` 的全部 Map 实体——
  1. STAR-CCM+ 原生导出 `tests/bladerotating_dm2.ccm`（含 ami_out/ami_in 滑移界面）；
  2. 本工具输出 `two_region.ccm`（2000 cell / 5300 内部面 / 1000 边界面 / 200 接口面，
     n_faces=6500，1 个 air_domain↔rotation1 接口）。
- **dump 结果**：
  - 原生文件：单一全局 face-id 空间。边界面 id 1..185902（各 region 连续分段），
    内部面 id 185903..984566（Map-8 n=798664 maxID=984566）。
    **interface 两侧（ami_out/ami_in，各 12566 面）是同一物理面的重复真实 id
    （各占真实 id 段 39977..52542 / 53523..66088），不存在超出真实面数的虚拟 id。**
  - 本工具：`air_to_rotation1` / `rotation1_to_air` 两个 wall 型重复 region 用
    **真实接口面 id（两侧相同，2721..3348）**——与原生语义一致；
    额外的 `air_to_rotation1 [Interface 1]` / `rotation1_to_air [Interface 1]`
    两个 surface patch 用**虚拟 id** `fids+1+n_faces`（9221..9848）与
    `fids+1+2*n_faces`（15721..16348），maxID 最大达 16348 ≈ 2.5×n_faces。
- **结论**：差异确认存在——原生不声明虚拟 id，本工具为 `[Interface k]` 补丁
  声明了 `+k·n_faces` 偏移的虚拟 id。**保留现有实现的理由**：
  1. 该文件已在 STAR-CCM+ 20.02.007-R8（2502）实测可正常导入并生成 interface；
  2. 虚拟 id 让每个 interface 侧在 map 空间里有独立连续的 id 段，便于 STAR-CCM+
     把两个 `[Interface k]` patch 直接配成一对（原生则依赖重复真实 id + 名称配对）；
  3. 改回原生语义需重排全局 face-id 空间，改动面大、收益不明确。
- **残余风险**：maxID 超出真实面数属于对格式语义的"宽容解读"，更严格的第三方
  读取器（或旧版 STAR-CCM+）可能告警或拒绝；未经其他 STAR-CCM+ 版本验证。
  若未来遇到导入异常，优先排查此虚拟 id 方案（替代方案：像原生那样只用
  重复真实 id + interface region 名称配对，去掉 `[Interface k]` 补丁或改用真实 id）。

---

## 二、中严重性 — 功能失效 / 精度 / 健壮性

### M1. `chunk_vertices` 参数完全失效
- **位置**：`convert.py:198-205`（`_write_vertices` 单次写入）、`convert.py:174,184`
  （`__init__` 存了 `self.chunk_vertices` 但从不读取）、`__main__.py:91-96`（CLI 暴露）
- **原因**：为规避 ccmio.dll 的 2D 分块错位 bug，`_write_vertices` 把整个顶点数组
  reshape 成 1-D 后一次性 `CCMIOWriteVerticesf`，`start=0, end=None`。
  `self.chunk_vertices` 与 CLI 的 `--chunk-vertices` 形同虚设。
- **影响**：① 用户以为可调小分块控制内存，实际无效，误导。② 千万级顶点网格
  单次调用需一次性提交整个 float32 数组，内存峰值高；若 ccmio.dll 对单次写入
  有内部缓冲上限可能失败。
- **建议**：要么真正实现「按顶点分块、但以 3*s 校正偏移」的安全分块
  （绕过 dll bug），要么移除 `--chunk-vertices` 选项并注明顶点必须单次写入。

### M2. 顶点 float32 精度损失
- **位置**：`convert.py:389` `model.vertices.astype(np.float32) * 1000.0`
- **原因**：`model.vertices` 是 float64（米），直接 `astype(np.float32)` 后
  有效数字约 7 位。虽乘 1000 转 mm 提升了小尺度精度，但大坐标网格
  （如建筑/城市尺度，坐标上百米）仍可能丢失亚毫米精度。
- **影响**：CCM 格式只支持 float32，无法根除；但转换尺度选择影响精度上限。
- **建议**：确认 GPH 原始坐标单位，在最接近原始精度的尺度做 float32 转换；
  或在文档中标注精度上限（约 1e-4 × 坐标量级）。

### M3. `_short_label` 截断可能碰撞
- **位置**：`convert.py:159-162`
- **原因**：`s[:16]` 硬截断到 16 字符，用于构造 interface label
  （`{short_a}_to_{short_b}`）。不同 region 名截断后可能相同。
- **影响**：label 碰撞会导致 interface 配对错误（STAR-CCM+ 按 name 匹配 interface）。
- **建议**：截断时保留区分性，或对碰撞名追加数字后缀。

### M4. 异常时文件句柄泄漏
- **位置**：`convert.py:361-531`（`write()` 中 `open_file` 后无 try/finally）、
  `convert_model:642-655`
- **原因**：`ccmio.open_file(out)` 之后若任意 `CCMIO*` 调用抛 `CCMIOError`，
  `close_file` 不会执行。
- **影响**：Windows 上文件句柄未释放会阻止后续对该 `.ccm` 的删除/覆盖，
  表现为「下一次转换报 PermissionError」。
- **建议**：`write()` 用 try/finally 包裹，确保 `close_file` 一定调用；
  或在 `convert_model` 层做清理。

---

## 三、低严重性 — 设计假设 / 健壮性

### L1. `write_face_cells` 在 face 写入之前调用
- **位置**：`convert.py:222-223`（先写 face_cells）→ `224-234`（后写 faces）
- **原因**：`CCMIOWriteFaceCells` 不接受 total 参数，靠 `start/end` 推断数组维度。
  本实现先写 face_cells（end=K_CCMIO_END）再写 faces。
- **影响**：依赖 libccmio 对「face 实体尚未写入、face_cells 先到」的处理。
  当前 ccmio.dll 可工作（README 已验证），换用其他 CCMIO 实现时行为未知。
- **建议**：调换顺序先写 faces 再写 face_cells，更贴近参考 writer 习惯。

### L2. `build_cell_table` 同 cvol id 被多 part 覆盖无告警
- **位置**：`model.py:122-126` `cvol_to_id[v] = idx`
- **原因**：若多个 Part 共享同一 cvol id，后者静默覆盖前者。
- **影响**：通常 cvol id 唯一，但异常 GPH 会导致 cell 错误归类且无提示。
- **建议**：检测到覆盖时打印告警。

### L3. `cell_centroids` 用面心算术均值近似质心
- **位置**：`convert.py:110-121`
- **原因**：interface 法向判断用质心，但实现是面心均值而非体积加权质心。
- **影响**：畸形单元（如扁切单元）上可能误判 interface 法向，导致
  `orient_interface_streams` 反转错误，面方向不一致。
- **建议**：对 interface 法向判断改用体积加权质心，或直接用 owner→neigh
  几何向量替代质心差。

### L4. `new_processor` / `clear_processor` 逻辑冗余
- **位置**：`ccmio.py:553-557`、`convert.py:376-377`
- **原因**：`new_processor` 先 `next_entity` 复用已存在 processor；
  随后 `clear_processor` 又把它清空。对全新文件本无 existing processor，
  `next_entity` 返回 None 后新建，流程正确但语义混乱。
- **影响**：无功能问题，仅可读性。
- **建议**：注释说明「先获取/创建 processor 槽位，再 clear 以保证干净状态」。

---

## 四、已正确处理（无需修改，记录备查）

- **顶点 / 内部面 face_cells 单次写入**：规避 ccmio.dll 2D 分块错位 bug
  （README 已述，AGENTS.md 已强调）。
- **边界 region 去重**：`build_boundary_regions` 按「非 `@PartSurface_` 优先、
  文件顺序」去重，符合 Cradle 重叠导出语义。
- **face_stream 1-D 分块**：1-D 数组分块安全，`write_faces` 按 stream 偏移分块正确。
- **RCM 重排**：`reorder.py` 实现正确，`apply_cell_order` 同步重排 owner/neigh/cvol。
- **map max_id 对常规 region**：`face_ids+1` 的 max 等于最大全局 face id，正确。

---

## 优先级建议

1. ~~先修 **M1**（移除或实现 `--chunk-vertices`）与 **M4**（句柄泄漏）~~
   ——已完成（A2 / A1）。
2. ~~验证 **H2**（对照 STAR-CCM+ 原生导出）~~——已完成（见上方"已验证"结论，
   差异已记录，保留现实现）。
3. 修 **H1**（verify 在 split 模式放宽）——让 `--verify` 在 split 模式可用。
4. 其余按需处理。
