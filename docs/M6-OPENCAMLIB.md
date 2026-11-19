# M6：Fusion 360 不用了 —— 免费、非 GUI、可脚本化的第三方 CAM 对照

> 日期：2026-11-06，2026-11-07 补充 §3.5、§5～§7　｜　脚本 `scripts/m6_opencamlib.py`
> ｜　数据 `reports/m6_ocl.json`、`reports/m6_ocl_gcode/*.nc`
> ｜　测试 `tests/test_m6_ocl.py` + `tests/test_m6_supplement.py`（全量 **177 passed**）
>
> 结论一句话：**Fusion 360 那组对照不需要你做任何事，也不需要 GUI。**
> 用 OpenCAMLib 2023.1.11（LGPL-2.1，`pip install opencamlib`，C++ CAM 内核 + Python 绑定）
> 在命令行里跑完，而且比人工导出 `.nc` 多给了两条证据、还查出一个我们自己的盲点。

---

## 1 为什么要换掉 Fusion 360

`docs/M6-EXTERNAL.md` §6 原来写的是「请你在 Fusion 360 GUI 里导入 STEP → Manufacture →
Post Process 导出一个 `.nc`」。这条有三个硬伤：

| 问题 | 后果 |
|---|---|
| 需要账号 + 图形界面 | 我跑不了，必须占用你的时间 |
| 版本 / 默认参数 / 后处理器会变 | 审稿人无法复现，「商用 CAM 对照」反而变成弱点 |
| 只产出一个 `.nc` | 只能比刀路，回答不了「验证器本身凭什么信」 |

按你的要求找了免费、非 GUI、我能直接跑的替代。候选核对如下（许可与安装方式以官方仓库为准）：

| 工具 | 许可 | GUI | 能脚本跑 | 定位 | 本项目 |
|---|---|---|---|---|---|
| **OpenCAMLib** | LGPL-2.1（2018 起） | 不需要 | `pip install opencamlib`，C++/Python/Node/WASM 预编译轮子，Python 3.9–3.14 | **CAM 算法内核**：drop-cutter、push-cutter/waterline、5 种刀具 | ✅ **本次采用** |
| FreeCAD Path | LGPL-2.0+ | 不需要（`freecadcmd.exe`） | 是 | 完整 CAM 工作台 | 已跑，A 路 **2/20** 负结果，见 `M3-CAM-REFERENCE.md` |
| CAMotics | GPL | 不需要（`camsim.exe`） | 是 | **G-code 仿真器**，不生成刀路 | 已跑，体积差 1.15%，见 `M6-EXTERNAL.md` §5 |
| pycam | GPLv3 | 不需要 | 是 | Python 刀路生成 | 停更多年、Python 3 兼容未验证，本轮未用 |
| Fusion 360 | 商用 | **必须** | 否 | 商用一体化 CAD/CAM | ❌ 弃用 |

选 OpenCAMLib 的关键理由：它是**和我们的算法完全不同的第三方 CAM 内核**
（连续三角形–刀具求交，vs 我们的栅格圆盘卷积），版本能钉死、纯命令行、装一条 pip 就够，
并且没有 FreeCAD 那套 `Path::Job` / `MillFace` 的 API 兼容问题（那些坑见 `scripts/cam_probe*.py`）。

> 一句话分工：**FreeCAD Path = 负结果对照，CAMotics = 独立仿真，OpenCAMLib = 独立 CAM 内核。**
> 三者都不需要 GUI，都不用 Fusion。

---

## 2 方法：四条互相独立的检查

输入只用 `reports/annotation/geom/*.json`（FreeCAD 从 STEP 剖分出的三角网格缓存），
直接 `STLSurf.addTriangle` 喂给 OCL，不落 STL 文件、不再调 FreeCAD、不开 CAMotics。

| # | 检查 | 问的问题 | OCL 侧做什么 |
|---|---|---|---|
| 1 | `geometry` | 目标高度图对不对 | Ø0.02「点刀」对每个栅格点 dropCutter → 独立表面高度图，逐柱比 `dexel.rasterize` |
| 2 | `oracle` | 过切判据是不是循环论证 | OCL 给每个刀心点的**最低安全 Z**（= 刀盘内表面最高点）；G-code 每条 G1 按 0.5mm 采样，`z_cut < safe_z` 即过切 |
| 3 | `ocl_toolpath` | 验证器认不认第三方的刀路 | 纯 OCL 刀位曲面反推 constant-Z 分层刀路（**不读我们的 `goal`**），再用我们的 dexel + 双线判据评估 |
| 4 | `resolution_sweep` | 2 查出的偏差是离散化伪影还是真错误 | 同一条 B2 规则只改栅格 `res`，看过切率是否随 `res→0` 下降 |

两个必须说明的口径：

- **OCL 的 `dropCutter` 是从下往上顶**：`CLPoint` 的初始 z 必须低于所有表面，
  `run()` 后 z 被抬到接触高度。早期探针给 200.0，一律 `hit=False`、`calls=0`，
  白折腾了 5 轮。已写进 `tests/test_m6_ocl.py::test_ocl_dropcutter_is_bottom_up` 锁住。
- **相切不算过切**：刀盘与台阶边恰好相切时接触面积为 0，不产生材料去除，
  但 OCL 按 `<= radius` 会报接触。判据里把刀半径减 `TOUCH_EPS=0.02mm`
  （远小于 0.5mm 栅格）。件 1 上这一步把 B2 的 322 个假阳性全部去掉，
  而 B3 仍留 966 个真过切点。

---

## 3 结果（10 件锚点集，58 秒跑完）

### 3.1 目标几何：第三方内核确认

| | 值 |
|---|---|
| 比对柱数 | 256 000（10 件 × 25 600） |
| 覆盖 IoU | **1.0000**（10 件全部） |
| 逐柱完全一致（差 ≤ 0.01mm） | **≥ 99.828%** |
| 差 > 0.5mm 的柱 | **120 / 256 000 = 0.047%** |
| 最大差 | 8.0 mm（件 9/10，圆弧台阶边界） |

120 个差异柱逐件只有 0–44 个，坐标全部落在圆弧轮廓上（如 `x=±33.8, y=±9.2`），
是「点刀半径 0.01mm 擦到邻层三角面」的边界效应，不是系统性偏差。
**我们的 `rasterize` 目标高度图被一个完全独立的 C++ 内核确认了。**

### 3.2 过切判据：B2 / B3 被第三方独立复现

| 刀路 | OCL 判定过切刀心点 | 采样点 | 过切率 | 最深 |
|---|---:|---:|---:|---:|
| **B2**（区域掩码，膨胀） | 2 822 | 371 230 | 0.76% | — |
| **B2 + 半格余量** | **41** | — | −98.5% | — |
| **B3**（不膨胀） | **43 260** | 408 646 | **10.59%** | **18.0 mm** |
| **OCL 自生成刀路** | **0** | — | 0 | 0 |

第三方内核给出的排序和我们自己的 dexel 完全一致：**B2 远好于 B3，差 15 倍**。
这比 CAMotics 的体积比对更严：CAMotics 只回答「切掉多少」，OCL 回答「每一个刀心位置
在连续几何上会不会碰材料」。

### 3.3 第三方刀路：验证器的灵敏度与特异性

OCL 自己生成的 constant-Z 刀路（只由 OCL 刀位曲面驱动）：

- OCL 自判过切 **0 点**，我们的 dexel 也判 **0 过切** → 两个实现一致；
- 双线判据 **9/10 通过**；件 10（8 层，最复杂）安全线仍是 0 过切，
  但精度线掉下来：均偏差 **0.538 mm > 0.5**，残留 3 445 mm³（窄缝/内圆角够不到）。

即：**坏刀路（B3）被拒、好刀路（OCL）被收**，验证器不是「只对自己的生成器说 yes」。
同时它也说明，第三方标准刀路在复杂件上会有真实残留 —— B2 的两遍刀具 + 步距覆盖是有代价换来的。

### 3.4 最重要的发现：我们自己的盲点

2 里 B2 那 2 822 个点不是噪声，全部集中在**圆弧台阶边界**。根因：

> `heightmap.feasible_centers` 的膨胀按「**柱心 → 柱心**」的栅格距离算，
> 连续几何按「**柱心 → 台阶边**」算，两者最多差半个格。
> 我们自己的 dexel 用的是**同一套离散圆盘**，所以生成器和验证器共享这个误差，
> 谁也看不见谁 —— 正是循环论证。OCL 用连续三角形求交，把它照出来了。

三条证据说明这是**分辨率伪影，不是几何错误**：

1. **分辨率收敛**（件 10，同一条 B2 规则只改 `res`）：

   | res (mm) | 0.500 | 0.250 | 0.125 |
   |---|---:|---:|---:|
   | OCL 过切率 | 1.332% | **0.432%** | **0.143%** |

   单调下降 → `res→0` 时趋于消失。绝对点数不单调（1075 → 601 → 751）是因为
   采样密度涨了 6.5 倍，物理上的边界条带变窄了但被采得更密；**率**才是归一化量。

2. **半格安全余量能消掉**：给 `heightmap.generate` 加可选参数 `guard_mm`（默认 **0**，
   保证 M3 全部既有数字一字不变，已用测试锁住）。取 `guard_mm = res/2 = 0.25mm` 后，
   OCL 过切点 **2 822 → 41**（−98.5%），件 10 从 1 075 → **0**。

3. **但有代价，而且是真取舍**：

   | | 残留体积 | 双线通过 |
   |---|---:|---:|
   | B2（guard=0） | 4 606 mm³ | 10/10 |
   | B2 + 半格余量 | **15 886 mm³（3.4×）** | **9/10**（件 10 均偏差 0.702 > 0.5） |

   扫了 `guard ∈ [0, 0.5]`：0.05–0.20 几乎不动（离散圆盘是整格量化，没到下一圈就不变），
   0.25 一步跨到下一圈，过切清零但残留跳 3 倍。**在 0.5mm 栅格上，
   「连续几何零过切」和「双线精度线」不能同时满足**；要两者兼得必须换更细栅格或
   连续几何偏置，属于架构工作，本轮没做（见 §5）。

> 这条不是"结果变差了"，而是**第三方交叉验证本来就该干的事**：
> 它把「生成器和验证器共用同一套离散化」这个审稿人一定会问的问题，
> 从一句含糊的 limitation 变成一个可量化、可复现、有明确修法和明确代价的结论。

### 3.5 补上的第二条几何链路：STEP → 二进制 STL → OCL

§5 原第 1 条留了个尾巴：OCL 的曲面是我们用**自己的 geom JSON 缓存**逐三角
`addTriangle` 喂出来的。万一缓存或解析代码本身有偏差，"第三方验证"就还是自证。
2026-11-07 补了一条完全绕开 JSON 缓存的几何来源（`scripts/m6_step_stl_xcheck.py`）：

```
Zenodo STEP ──OCC/FreeCAD──> Part.Shape.tessellate(0.05)
            ──Mesh.Mesh──> 二进制 STL 文件 ──ocl.STLReader──> OCL STLSurf
```

由 `freecadcmd` 执行 `scripts/_freecad_stl_export.py`，10 件 STEP 各导出一个
二进制 STL（`84 + 50×facets` 字节，manifest 记录内核版本、面片数、bbox、体积）。
然后对每件比对三张高度图：JSON 缓存喂出的 OCL、STL 喂出的 OCL、我们的 `rasterize`。

| 结果（10 件） | 值 |
|---|---|
| 两条 OCL 曲面逐柱一致 | **≥ 99.984%**，最大差 8.0 mm（仅圆弧台阶边界） |
| 其中纯平面件（1/2/3/6） | **逐柱 0.0000 mm 差** |
| 差 > 0.5 mm 的柱 | 两条曲面之间共 **15 柱**；STL vs `rasterize` 共 **122 柱**（JSON 链路为 120 柱） |
| bbox 一致 | 10/10 |
| OCL 判过切（走 STL 链路） | B2 **2 826/371 230**、B3 **43 262/408 646** |
| 与 JSON 缓存链路之差 | B2 +4 点、B3 +2 点（**< 0.15%**），B2≫B3 的结论不变 |

差异只出现在带圆弧面的 6 件上：两个三角化器（逐面剖分 vs `Mesh.Mesh(shape.tessellate)`）
在圆弧边界切法不同，少数栅格柱差一个层深（4/6/8 mm）。这恰好证明两条链路**确实不是
同一批三角形**，而不是把同一个网格序列化两遍。

> **独立性边界，论文必须照抄**：读 STEP 的 B-rep 内核仍是 OCC（与主链相同），
> 所以这**不是第二个 CAD 内核**。独立的是三角化器、文件序列化与几何读取链路，
> 检验的是「我们的 geom 缓存与 `rasterize` 有没有引入系统性偏差」。
> 写成"与两个独立 CAD 内核交叉验证"是错的，只能写"与独立开源 CAM/仿真内核交叉验证，
> 并用一条独立几何链路复核了输入网格"。

---

## 4 对论文怎么写

1. **§5.3b 新增一小节**：把「第三方交叉验证」从 CAMotics 一条扩成两条 ——
   体积（CAMotics）+ 逐点几何（OpenCAMLib），并明确「无需商用 CAM、无需 GUI、版本可钉死」。
2. **B3 待办关闭**：`NEXT-ACTIONS.md` 里「Fusion 360 待你导出」删掉，改成已用 OCL 替代；
   论文里不要写「与 Fusion 360 对比」，写「与两个独立开源 CAM/仿真内核交叉验证」。
3. **主动披露 §3.4**：建议放在 Limitations，而不是藏起来。措辞：
   「在 0.5 mm 栅格下，离散膨胀在圆弧边界最多少盖半格；第三方连续几何内核检出
   0.76% 的刀心位置存在亚格尺度过切，随分辨率单调收敛；加半格余量可消除但残留增至 3.4 倍。」
4. **不要把 OCL 9/10 写成"我们更牛"**：OCL 是单半径精加工刀路，和 B2 的两遍粗精加工
   不是同一工序策略；这个对比只用来证明验证器的灵敏度/特异性，不作性能排名。

---

## 5 限制（如实写）

1. **几何链路已补，但 B-rep 内核仍不是第二个**：2026-11-07 加了
   `STEP → Mesh → 二进制 STL → OCL STLReader`（§3.5），绕开了 geom JSON 缓存；
   但读 STEP 仍靠 OCC，与生成主链的同一个 CAD 内核。**不能称"第二 CAD 内核"**。
2. **OCL 点刀不是零半径**：Ø0.02mm 仍会在圆弧边界擦到邻层，那 120 个差异柱由此而来。
3. **过切判据只看平底刀的刀盘**，没有评估刀杆与侧壁的干涉、没有切削力/让刀/振动。
4. **OCL 刀路是精加工/偏置刀路**，不是工业粗加工工序（没有真实切深限制、进退刀、
   刀补、转速优化）；它只作为「第三方合法刀路」存在。
5. **半格余量是权宜之计**：离散圆盘整格量化，0.25mm 以下几乎无效、0.25mm 一步跳太多。
   真正的解法是连续几何偏置或更细栅格，未做。
6. **10 件锚点集**规模小，Wilson 95% 下界只有 0.7225（论文 §5.4）。
   2026-11-07 已把 OCL 交叉验证扩到 **130 件外部集**，见 `docs/M6-OCL-EXTERNAL.md`；
   该集把双线通过率下界抬到 0.9713，但同时暴露了稀疏网格上的第二类盲区，须一并披露。

---

## 6 复现

```bash
# 依赖（只需这一条，不需要 FreeCAD / CAMotics / Fusion / GUI）
/d/Anaconda3/python -m pip install opencamlib==2023.1.11

# 10 件全跑（约 1 分钟，含件 10 的分辨率收敛扫描）
PYTHONPATH=src /d/Anaconda3/python scripts/m6_opencamlib.py

# 只跑单件 / 跳过收敛扫描
PYTHONPATH=src /d/Anaconda3/python scripts/m6_opencamlib.py --parts 1,10 --sweep-part 0

# 补充 1：另一条几何链路（约 10 秒，需要 freecadcmd + opencamlib）
PYTHONPATH=src /d/Anaconda3/python scripts/m6_step_stl_xcheck.py
# 或 make step-stl

# 补充 2：第三方判据扩到 130 件外部集（约 18 分钟，耗时长，不进 reproduce）
PYTHONPATH=src /d/Anaconda3/python scripts/m6_ocl_external.py
# 或 make ocl-external

# 补充 3：点刀半径敏感性（约 30 秒，解释 71mm 探针伪影）
PYTHONPATH=src /d/Anaconda3/python scripts/m6_ocl_point_cutter.py
# 或 make ocl-point-cutter

# 相关测试
PYTHONPATH=src /d/Anaconda3/python -m pytest tests/test_m6_ocl.py tests/test_m6_supplement.py -q
```

产物：`reports/m6_ocl.json`（汇总 + 逐件 + 分辨率扫描）、
`reports/m6_ocl_gcode/1.nc … 10.nc`（OCL 自己生成的刀路，可直接喂 `dexel.simulate` 复核）、
`reports/m6_step_stl/*.stl` + `manifest.json`、`reports/m6_step_stl_xcheck.json`、
`reports/m6_ocl_external.json`。

---

## 7 文件清单

| 文件 | 作用 |
|---|---|
| `scripts/m6_opencamlib.py` | 四条检查的实现（新增） |
| `src/cadcam/modalities/cam/heightmap.py` | 新增 `guard_mm` 可选参数（默认 0，向后兼容） |
| `tests/test_m6_ocl.py` | 6 条：向后兼容、单调性、OCL 落刀语义、几何一致、判据一致性、台账自检 |
| `tests/test_m6_supplement.py` | 11 条：Wilson 区间、STL 二进制格式、两份新台账自洽、过切分类契约 |
| `src/cadcam/verify/stats.py` | Wilson score interval（论文 §5.4 用） |
| `scripts/m6_step_stl_xcheck.py` / `scripts/_freecad_stl_export.py` | STEP→STL 独立几何链路 |
| `scripts/m6_ocl_external.py` / `docs/M6-OCL-EXTERNAL.md` | 130 件外部集扩展 |
| `scripts/m6_ocl_point_cutter.py` | 点刀半径敏感性（Ø0.02 / 0.002 / 0.0002） |
| `reports/m6_ocl.json` / `reports/m6_ocl_gcode/` | 结果与 OCL 刀路 |
| `reports/m6_step_stl/` / `reports/m6_step_stl_xcheck.json` / `reports/m6_ocl_external.json` | 补充实验产物 |
| `docs/M6-OPENCAMLIB.md` | 本文 |
| `docs/M6-EXTERNAL.md` §6 | 原 Fusion 360 章节已改写为「已用 OpenCAMLib 替代」 |
| `scripts/bench.py` | 把 M6 四组报告纳入 `reports/bench.md` 台账（只读 JSON，不引入 OCL 硬依赖） |
| `Makefile` / `docs/REPRODUCE.md` | 新增 `make ocl` 目标与复现行 |
| `docs/PAPER-DRAFT.md` §5.3b、`NEXT-ACTIONS.md`、`DECISIONS.md` §6、`PLAN-VS-ACTUAL.md` C5、`DATA-CARD.md` §6、`README.md` | 论文口径、待办关闭、决策追溯、许可登记 |
