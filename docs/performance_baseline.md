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

## 复跑

```bash
python tools/benchmark.py --n 100            # 1M 单元快速基线（CI 友好，约 1.5s）
python tools/benchmark.py --n 149            # 3.3M 单元（D3 目标）
python tools/benchmark.py --gph tests/<x>.gph  # 真实 GPH 端到端
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
