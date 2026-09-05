# 性能基线（D3）

本文件记录 GPH→CCM 转换管线的耗时与内存峰值基线，用于**防止回归**——
当前测试套件只覆盖正确性（24+ 用例），不覆盖性能。每次改动写路径后，
用 `tools/benchmark.py` 复跑并与下表对照。

## 测量方法

- 脚本：`tools/benchmark.py`（自包含，无外部依赖）。
- 输入：**合成结构化六面体块**（`n^3` 单元、`(n+1)^3` 顶点、`3n^2(n+1)` 面）。
  合成网格可复现、可进 CI，不依赖仓库内不存在的大网格文件。
- 三阶段计时：
  - `build_model` —— numpy/CPU 的模型组装（cell table、boundary regions、面流）。
  - `write` —— `CcmMeshWriter.write` 的 CCMIO 写出（`chunk_faces=500000`）。
  - `compress` —— `CCMIOCompress` 最终压缩。
- 内存：进程 **PeakWorkingSetSize**（Windows `K32GetProcessMemoryInfo`；
  POSIX 为 `ru_maxrss`）。峰值单调递增，故逐阶段上报即给出该阶段的
  「高水位」。

## 基线

机器：本地 Windows + STAR-CCM+ 20.02.007-R8 的 `ccmio.dll`（自动发现）；
Python 3.13（managed）。

| 单元数 | 顶点数 | 面数 | build (s) | build 峰值 (MB) | write (s) | write 峰值 (MB) | compress (s) | 输出 (MB) |
|---|---|---|---|---|---|---|---|---|
| 1,000,000 (`--n 100`) | 1,030,301 | 3,030,000 | 0.18 | 436 | 0.97 | 1028 | 0.13 | 121.2 |
| 3,307,949 (`--n 149`) | 3,375,000 | 9,990,450 | 0.60 | 1355 | 3.41 | 3324 | 0.41 | 399.7 |

> 真实目标网格 `laptop_thermal_steady_scaled_v3_fanonly` 为
> **3,335,405 单元 / 3,770,537 顶点 / 10,443,114 面**（多面体/切割单元，
> `tools/laptop_topo.json`），与 `--n 149` 的合成块同数量级，故合成块基线
> 可作为其量级参考。

## 真实 FPH 端到端记录（2026-09-04，C2 全流程验证）

输入 `laptop_thermal_steady_scaled_v3_10.fph`（1.36 GB，Cradle scFLOW 结果
文件，网格 + `LS_SPHFile` 求解数据），`python -m gph2ccm <fph> out.ccm --verify`：

| 阶段 | 耗时 | 说明 |
|---|---|---|
| FPH 解析（mmap） | 104.9 s | 6,831,117 cells / 7,723,969 verts / 21,389,752 faces |
| CCM 写出 | 22.1 s | 含 **10 个解算场**（7 标量 + 3 向量 ×3 分量，16 组 cell 数据 × 6.8M float32 ≈ 437 MB） |
| compress + verify | 其余 | 输出 **1330.5 MB**；verify 读回全部一致 |
| **端到端合计** | **2 m 41 s** | 单命令 `--verify` 内完成 |

场数据读回比对：16 个 FPH 变量的 min 值逐场精确一致；7 个变量含
`1e20` 哨兵值，CCM 侧全部清零（max 恢复物理范围，如 PRES 1e20→68.16、
TURK→0.00768）。

STAR-CCM+ 20.02.007-R8 batch 导入（`ImportCcmCheck.java`，license
`ccmpsuite` 正常检出，约 3 min）：

- `IMPORT_DONE`；区域 `air_domain`（6,183,269 cells）+ `case2`
  （647,848 cells）＝ **6,831,117 与源精确一致**；
- 边界 `open` 17,746 / `impeller1_s` 113,933 / `impeller2_s` 113,933
  ＝ **245,612 与源一致**；
- 导入器自动创建 **Interface-1-2**（两侧各 516,906 面，fluid/solid 共轭
  界面）——多材料网格的区域/界面语义被正确识别。

此记录同时是「真实 6.8M 单元 + 场数据」的回归参照：后续改写路径后，
parse/write 耗时偏离此量级 >30% 需排查。

## 场写入剖析（E5，2026-09-05）

**问题**：早期小样本测得「4 场 2.2 s / 16 MB」，疑似场写入偏慢，需判断是否值得优化。

**方法**：`tools/profile_fields.py` —— 同一合成网格分别带 0 / 4 / 16 个
解算场写出，差分出**每场边际成本**；并与裸 `numpy → file` memcpy 参照对比。
（早期 2.2 s 实为整次转换摊入的网格写出 + 一次性开销，并非场数据本身。）

| 规模 | 每场边际成本 | 场数据吞吐 | 裸磁盘 memcpy | ADF 开销倍数 |
|---|---|---|---|---|
| 216k 单元（n=60） | 2.4 ms | 5,728 MB/s | 1,515 MB/s | x4.2 |
| 1,000,000（n=100） | 9.9 ms | 6,497 MB/s | 1,673 MB/s | x4.1 |
| 3,307,949（n=149） | 21.7 ms | 9,747 MB/s* | 1,614 MB/s | x2.6 |

\* 大于裸磁盘参考是因为数据大部分停留在 OS write cache，未真实落盘；
真实场景（16 组 × 6.8M float32 ≈ 437 MB）在 FPH 端到端中与网格写出
合计 22.1 s（见上节），场数据部分推算仅 ~1-2 s 量级。

**结论**：`CCMIOWriteFieldDataf` 已是单次 bulk 调用（`write_field_dataf`
无逐单元循环、无分块），吞吐在 GB/s 量级，**无需优化**。真实管线的瓶颈
在 FPH 解析（104.9 s，占端到端 ~65%），不在此项范围内。

## 复跑

```bash
python tools/benchmark.py --n 100            # 1M 单元快速基线（CI 友好，约 1.5s）
python tools/benchmark.py --n 149            # 3.3M 单元（D3 目标）
python tools/benchmark.py --gph tests/<x>.gph  # 真实 GPH 端到端
python tools/profile_fields.py --n 100       # E5: 场写入吞吐剖析
python tools/benchmark.py --n 149 --json     # 机器可读
```

## 回归判定（经验阈值）

- `write` 耗时 / 输出大小随单元数**线性**放大；偏离线性 > 30% 需排查
  （例如面流向量化被破坏、意外逐面 Python 循环、chunk 尺寸失效）。
- 峰值内存随单元数近似线性；`write` 峰值出现阶跃（翻倍）需排查是否新增了
  全量中间数组（例如把面流一次性物化而非分 chunk）。
- 合成块本身无歧义：面数 = `3n^2(n+1)`，单元/面/顶点恒一致，任何解析或
  计数回归会立即在 `build_model` 阶段暴露。

## 备注

- 合成块的 6 个外表面归入单个 `xmin` surface region，其余边界面落入
  `Default_Boundary_Region`（基准不依赖边界命名正确性）。
- `write` 峰值（约 3.3 GB @ 3.3M 单元）主要来自面流的 int32 物化 + owner/
  neighbor 的 int64 数组；这既是真实管线同一路径，也说明 3.3M 级网格需
  ≥ 4 GB 空闲内存。
