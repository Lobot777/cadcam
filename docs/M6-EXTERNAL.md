# M6 外部验证：130 件 MFCAD 件 + 第三方交叉验证

> 日期：2026-11-06（§6 于同日改写）　｜　数据：`reports/m3_external_eval.json`、
> `reports/m3_camsim_xcheck.json`、`reports/m3_b3_baseline.json`、`reports/m6_ocl.json`
> 目的：回答三个审稿人一定会问的问题——「只有 10 件？」「你的验证器凭什么信？」
> 「零过切是掩码的功劳，还是别的？」

## 1 数据集：从 10 件到 130 件

主实验用的 RPTU Zenodo 只有 10 件带标注。为了给"是不是只对这 10 件有效"一个答案，
我们从开源数据集补了外部验证集：

| 数据集 | 规模 | 许可 | 用途 |
|---|---|---|---|
| **MFCAD**（Cao et al. 2020） | 15 488 个 STEP + 面级标签（pickle） | MIT | **本次用**：筛选 130 件 |
| Durham（Miles et al. 2023） | 3 000 个 STEP + CSV 标签 | CC BY 4.0 | 备选，标签列定义未公开，暂未纳入 |
| MFCAD++ | 59 665 个 STEP | 需申请 | 未用（体积与申请成本） |

MFCAD 的文件名本身就是特征序列（`0-0-0-0-0-23` = 5 个矩形腔），
`.face_truth` 是每面一个 0–15 的特征编号。映射到本项目的 family：

```
pocket 0/1/2/3   → pocket
step  10/11/12   → step
hole  13/14      → hole
stock 15         → 不建特征
```

**筛选流水线**（`scripts/external_select.py`）：

```
15 488 件
  → 1 947 件   文件名特征全在可映射集合、且 ≥3 个特征
  →   130 件   跑几何 + 高度图，保留：层数 3–10、面数 8–60、最小边 ≥6 mm
```

留下来的 130 件按原始层数分布均匀：3 层 18 件、4 层 17、5 层 10、6–10 层各 17 件。
**层数是这个项目的主难度因子**（见 `docs/M3-FINAL.md`），所以刻意做成均匀分布。

## 2 口径：缩放到同一量级

MFCAD 的件只有 10 mm 量级（体积 ~800 mm³），而我们的刀具是 Ø6、阈值是 0.5 mm——
原尺寸下刀具比零件还大，指标不可比。所以统一**按长边缩放到 80 mm**
（`scripts/external_scale.py`，只改几何缓存坐标，STEP 原件不动）。

缩放后刀径、栅格（0.5 mm）、双线阈值（0.5 mm）都能直接沿用锚点集的口径。

## 3 结果：130/130 通过

| 指标 | 值 |
|---|---|
| 评测件数 | 130（无出错） |
| 双线通过 | **130 / 130 = 100%** |
| 安全线（max_gouge ≤ 0.5 mm） | **130 / 130**，最坏 **0.000 mm** |
| 精度线（mean ≤ 0.5 mm） | **130 / 130**，最坏 **0.306 mm** |

按层数分组，每一档都是满通过：

| 层数 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|---|---|---|
| 通过 | 18/18 | 17/17 | 10/10 | 17/17 | 17/17 | 17/17 | 17/17 | 17/17 |

**这个 100% 要带着前提读**：MFCAD 是 PythonOCC 合成的规则件（矩形/三角/六边腔 + 台阶 + 孔），
几何比真实的注塑模/航空结构件简单得多，且**没有凸台（boss）**。
它证明的是"方法不依赖那 10 件"，不是"方法能处理任意零件"。

## 4 零过切来自哪里：B3 基线

`heightmap.generate` 的区域掩码有一步"按刀半径膨胀"。我们把它单独关掉（`inflate=False`），
其余（分层、步距、刀具、后处理）完全相同：

| | B2（膨胀，默认） | B3（不膨胀） |
|---|---|---|
| 双线通过 | **10 / 10** | **0 / 10** |
| 最坏过切 | **0.000 mm** | **18.000 mm** |
| 平均偏差（均） | 0.072 mm | 0.914 mm |

分件看，B3 的过切恰好等于"刀半径 + 该处层高"——刀心贴到台阶边缘，刀径多切一圈。

**结论：零过切来自"按刀半径膨胀"这一步，而不只是"知道哪里要保留"。**
这是对 B2 机制的单独验证，也是审稿人最容易忽略、我们最该主动说清的一点。

## 5 第三方交叉验证：CAMotics

我们的验证器（Z-dexel）是自己写的。CAMotics 是独立的开源 G-code 仿真器
（表面网格切削，算法与 dexel 完全不同）。同一份 G-code、同一个毛坯、同一组刀具：

```bash
camsim --resolution 0.5 part.camotics part.stl     # CAMotics 1.2.0
```

比的是**被切后工件的体积**（一阶量，不受高度场分辨率影响）：

| 件 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|---|---|---|---|---|
| 相对差 | 0.10% | 0.37% | 0.72% | 0.42% | 0.28% | 0.93% | 1.93% | 1.04% | 2.50% | 3.22% |

**均值 1.15%，最大 3.22%，P95 2.90%。**

误差随层数增加（件 10 是 8 层，差 3.22%），符合预期：层数越多、台阶边界越长，
两个实现在边界处的离散化差异越大。

这条证据的价值不在"1.15% 很小"，而在**两个完全独立的实现给出同一个结论**：
G-code 确实切掉了那么多材料，我们的 dexel 没有系统性偏差。

## 6 第三方 CAM 对照：**已改用 OpenCAMLib，Fusion 360 不需要了**

原计划是请你在 Fusion 360 GUI 里导入 STEP → Manufacture → Post Process 导出一个 `.nc`。
这个做法要账号、要点鼠标、版本与默认参数不可控，审稿人无法复现，而且只能比一条刀路。
按你的要求换成**免费、非 GUI、我能直接跑**的 OpenCAMLib 2023.1.11
（LGPL-2.1，`pip install opencamlib`，C++ CAM 内核 + Python 绑定）：

- 独立内核确认目标高度图：256 000 柱逐柱一致 ≥ **99.828%**，覆盖 IoU **1.0000**；
  仅 120 柱（0.047%）在圆弧台阶边界有差。
- 独立过切判据：B2 **2 822 / 371 230**（0.76%）、B3 **43 260 / 408 646**（10.59%，
  最深 18.0 mm）、OCL 自生成刀路 **0** —— 与我们的 dexel 排序一致，且不需要我们的验证器参与。
- OCL 自生成刀路经我们的双线判据 **9/10 通过**（件 10 精度线 0.538 mm 未过，安全线仍 0 过切）。
- **额外查出一个我们自己的盲点**：离散膨胀按柱心到柱心算，在圆弧边界少盖半格，
  而生成器和验证器共用同一套圆盘所以互相看不见；分辨率收敛 1.332% → 0.432% → 0.143%
  证明是栅格伪影；加 `guard_mm = res/2` 可把过切点打到 41，但残留涨到 3.4 倍、双线掉到 9/10。

完整方法、候选工具对比表、五条限制、复现命令见 **`docs/M6-OPENCAMLIB.md`**；
脚本 `scripts/m6_opencamlib.py`，结果 `reports/m6_ocl.json`，
OCL 刀路 `reports/m6_ocl_gcode/1.nc … 10.nc`。

**2026-11-07 补充：第三方判据扩到 130 件外部集**（`scripts/m6_ocl_external.py`，17 min 42 s）。
B2/B3 过切率 **3.275% / 14.468%**（锚点集 0.76% / 10.59%），我们的双线仍是
**130/130 / 0/130**；但稀疏大三角与斜边台阶把离散化盲点放大了：B2 的
316 398 个过切点里 **24.5% 深于 0.5 mm**、最深 **71.104 mm**；43.5% 落在网格未覆盖柱
（验证器假阴性），56.5% 落在覆盖完整件的斜边边界（柱心膨胀失效）。
`guard=0.25 mm` 让过切降 98.4%，代价是双线 **107/130**、残留 **4.42×**。
几何最大差 71 mm 同时被证明是 Ø0.02 点刀擦竖直壁的探针伪影（缩到 Ø0.002 后超差柱 199→2）。
**口径收紧：「零过切」只在 Z-dexel 离散口径内成立**，不得写连续几何零过切。
逐件数据与图 6 见 `docs/M6-OCL-EXTERNAL.md`。

> 分工：**FreeCAD Path = 负结果对照（A 路 2/20），CAMotics = 独立 G-code 仿真（体积差 1.15%），
> OpenCAMLib = 独立 CAM 内核（逐点几何 + 第三方刀路）**。三者都不需要 GUI。

### 复现

```bash
/d/Anaconda3/python -m pip install opencamlib==2023.1.11
PYTHONPATH=src /d/Anaconda3/python scripts/m6_opencamlib.py     # 10 件，约 1 分钟
```

## 7 限制（如实写）

1. **MFCAD 是合成件**：规则几何、无凸台、特征以单层为主；真实零件（注塑模、航空结构件）
   的曲面、薄壁、倒扣都没有覆盖。
2. **130 件全通过 → 可能过易**。这些件原本是给"特征识别"任务设计的，
   加工难度低。它证明"方法可迁移"，不证明"方法能处理工业件"。
3. **缩放改变了尺度**：虽然长边统一到 80 mm，但原始件的特征比例被放大/缩小了，
   极小特征（<0.2 mm）在缩放前就被栅格化丢掉了。
4. **Durham 未纳入**：CSV 的 7 列语义没有公开说明，需要与作者确认或反推，
   本轮没有做；它是下一步最有价值的补充（24 类特征、2–10 个相交特征）。
5. CAMotics 只比体积，**没有比几何**（两套实现的网格不重合，逐点比对需要配准，
   不是这次的范围）。
6. **130 件全通过是在我们自己的 Z-dexel 口径内**。外部件是稀疏大三角/斜边台阶，
   栅格覆盖率最低 77.465%；第三方连续几何判据在这些件上检出额外的过切点
   （B2 3.275%，24.5% 深于 0.5 mm），见 `docs/M6-OCL-EXTERNAL.md` §3.3。
   「零过切」不能外推到连续几何或任意网格密度；这正是我们主动披露的 limitation。
7. **Ø0.02 点刀不是零半径**：会在竖直壁三角旁产生探针伪影（外部集最大差 71.104 mm），
   已用点刀直径扫描量化剥离（`reports/m6_ocl_point_cutter.json`）。

## 8 复现

```bash
# 1) 数据集（MIT）
git clone --depth 1 https://github.com/hducg/MFCAD.git data/inbox/MFCAD

# 2) 筛选 + 缩放（约 8 分钟 + 30 秒）
PYTHONPATH=src python scripts/external_select.py --dataset mfcad --target 130
PYTHONPATH=src python scripts/external_scale.py

# 3) 130 件跑全链（约 5 分钟）
PYTHONPATH=src python scripts/external_eval.py

# 4) B3 基线（10 件，约 20 秒）
PYTHONPATH=src python scripts/m3_b3_baseline.py

# 5) CAMotics 交叉验证（需装 CAMotics 1.2.0，约 1 分钟）
PYTHONPATH=src python scripts/m3_camsim_xcheck.py

# 6) OpenCAMLib 第三方 CAM 对照（替代 Fusion 360，约 1 分钟，纯命令行）
/d/Anaconda3/python -m pip install opencamlib==2023.1.11
PYTHONPATH=src python scripts/m6_opencamlib.py

# 7) 补：第三方判据扩到 130 件外部集（约 26 分钟，耗时长，不进 reproduce）
PYTHONPATH=src python scripts/m6_ocl_external.py        # 或 make ocl-external

# 8) 补：STEP→二进制 STL→OCL 独立几何链路（约 10 秒，需 freecadcmd）
PYTHONPATH=src python scripts/m6_step_stl_xcheck.py     # 或 make step-stl

# 9) 补：Wilson 95% 置信区间与全部数字台账
PYTHONPATH=src python scripts/bench.py --reuse          # 看 reports/bench.md §3b
```

第 7 / 8 步的报告分别是 `reports/m6_ocl_external.json` 与
`reports/m6_step_stl_xcheck.json`，方法、结果和限制见 `docs/M6-OCL-EXTERNAL.md`
与 `docs/M6-OPENCAMLIB.md` §3.5。
