# M3.2 第 1 项：FreeCAD CAM 参考刀路（A 路）

> 日期：2026-11-05｜状态：**通路已打通，正式验收未通过**
> 复现：`scripts/cam_reference.py`（freecadcmd）→ `scripts/m3_cam_eval.py`（宿主）
> 产物：`reports/m3_gcode_ref/<n>.nc`、`reports/cam_reference_meta.json`、`reports/m3_cam_eval.json`
>
> **⚠️ 本文 §6 / §7 是初版记录（只配 MillFace 时写的），结论已被后续实验部分修正。
> 本轮最终结论见下方「2026-11-05 补记」与 `docs/M3-FINAL.md` §3。**

---

## 2026-11-05 补记：Drilling + BoundaryShape 与真正的根因

本轮在初版基础上做了两处改进，并重跑了 10 件评估（`reports/m3_cam_eval.json`）：

1. **补 Drilling**：`Drilling.Create` 后必须先 `job.Operations.addObject(op)` 再
   `doc.recompute()`，Proxy 才拿得到 job 与 model，从而自动找出所有可钻圆柱面
   （件 2 找到 4 个孔，生成 4 个 `G81`）。**不要覆盖 `FinalDepth`**，让 CAM 按孔深自动算，
   否则会一刀钻穿到零件底。
   效果：件 9 过切 18769→4819 mm³、件 7 17514→8754、件 2 200→0。
2. **`BoundaryShape = "Face Region"`**：默认值是 `Boundbox`，会把每个小面扩成整个零件
   包围盒。改后件 10 的 z=48 四角肩面走刀区由 `X[-24.7,-3.5]` 收窄到 `X[-24.7,-17.2]`。

**但件 8 / 10 完全没改善，≤0.5mm 仍是 0/10**（总过切 224466→215127 mm³）。

`scripts/cam_probe6.py` 做了四组合实验，定位到真正的根因。对件 10 的
`Face32`（z=34、面积 316mm²，目标里只占约 5% 的柱）：

| BoundaryShape | ExcludeRaisedAreas | 走刀 X 范围 | 命令数 |
|---|---|---|---|
| Boundbox | False / True | [-37.5, 37.5] / 无刀路 | 358 / 2 |
| Face Region | False / True | [-37.5, 37.5] / 无刀路 | 358 / 0 |
| Perimeter | False / True | [-37.5, 37.5] / 无刀路 | 358 / 2 |
| Stock | False / True | [-38.5, 38.5] / 无刀路 | 366 / 2 |

**四种模式都约束不住**。这不是配置错误，而是 MillFace 的语义：它是
「从毛坯顶面**铣平**到 z」的操作；对最深的 z=34 层，必然要清掉上方 19mm 的全部材料，
而目标几何里其余 95% 的柱应该留在 44/48/52。要正确加工多级腔，
必须用 `Pocket` / `Profile` 按封闭轮廓 + 显式 `ToolController` + 深度链做完整 CAM 编程。

**因此 §6 里「只配 MillFace 是主要原因」需要修正**：补齐钻孔有效但不足以达标，
根因是面铣操作本身与多级腔几何不匹配。§7 第 1 项（补 Drilling）已完成，
第 2–3 项（Pocket/Profile/ToolController/区域约束）**本轮不做**。

**这个 negative result 的用途**：通用工业面铣在这批零件上会产生大面积过切，
而本项目 `cam/heightmap.py` 的区域掩码方法在几何上保证零过切（10/10），
是 C3「验证接地」的直接证据。项目决定不继续投 A 路，如实写进 limitation。

---

## 0 一句话结论

**headless FreeCAD CAM 能生成刀路**（10/10 件、白名单合规、可解析），
但「参考刀路复现误差 ≤ 0.5mm」这条 M3 验收按原 max 口径 **0/10 通过**。
> 2026-11-05 21:20 更新：验收口径已按 ADR-001 改为双线，A 路在新口径下为 **2/10**
> （`reports/m3_cam_eval.json` → `summary.pass_count`）。本文表格仍是原 max 口径的历史记录，
> 用于说明为何要改口径；最新口径见 `docs/M3-ACCEPTANCE-CRITERION.md`。
简单件（1、2）平均偏差仅 0.03–0.04mm，复杂件（8、10）平均偏差 10.8–12.2mm。
原因清楚：**只配置了 MillFace 一种操作**，没有 Pocket / Drilling / Contour 的完整工序链。
（本轮补了 Drilling 与 Face Region 后仍未达标，更深层根因见上方补记。）

---

## 1 headless API：三个必须记下的坑

FreeCAD 1.1.4 把 Path 工作台改名为 CAM，但 Python 包名仍是 `Path`。
三个探针（`scripts/cam_probe{,2,3}.py`）逐步定位到：

| 坑 | 现象 | 正解 |
|---|---|---|
| Job 类型 | `addObject("Path::Job")` → `not a document object type` | Job 是 `Path::FeaturePython` + Proxy：`Path.Main.Job.Create("Job", [model])` |
| 操作创建 | `MillFace.Create("Op", job)` → `Property ... CycleTime already exists` | 第二参数 `obj` 是承载对象，不是 Job；用 `MillFace.Create("Op", parentJob=job)`，再 `job.Operations.addObject(op)` |
| 多面合并 | `op.Base = [(model, ["Face1","Face2"])]` 只取第一个面的范围，退化成包围盒走刀 | **每个面单独一道 op**；实测件 2 过切从 13030 → 200 mm³ |

后处理：不需要调 `Path.Post`，直接把 `op.Path.Commands` 序列化成受限 G-code
（`G0/G1/G2/G3` + `X/Y/Z/I/J/K/F`），复用 `ngc.py` 白名单校验，格式更可控。
默认刀具 `OpToolDiameter = 5.0 mm`；`Command (Label)` 是注释命令，序列化时转 `(Label)`。

## 2 生成策略

对每个 **朝上的水平平面**（`Part.Plane` 且法向 z>0.9、面积>1mm²）建一道 MillFace：

```
StartDepth  = STEP 顶面 z + 1mm（毛坯余量，与 M3.1 对齐）
FinalDepth  = 该面自身 z
StepDown    = 2mm
Base        = [(model, ["Face<i>"])]   # 单个面，不合并
```

MillFace 按面的真实轮廓（含内环/孔）生成偏置刀路，**自带「区域掩码」**——
这正是 M3.1 模板按矩形 bbox 走刀所缺的东西。

| 件 | 水平面 | 工序 | G-code 行 |
|---|---|---|---|
| 1 | 3 | 3 | 254 |
| 2 | 5 | 5 | 166 |
| 3–7 | 3–7 | 3–7 | 281–401 |
| 8 | 9 | 9 | 966 |
| 9 | 14 | 14 | ~1000 |
| 10 | 11 | 11 | 966 |

10/10 生成成功，`validate()` 全 OK、`unknown_codes = []`，
`ngc.parse` 得到 145–901 段/件。

## 3 一个必须说清的数据问题：真实 `.ngc` 不能当几何真值

RPTU 数据集里每件都有真实 `.ngc`，但它用的是**机床坐标系**，与 STEP 不对齐：

| | 件 1 STEP | 件 1 `.ngc` |
|---|---|---|
| X | −40 … 40 | −3.11 … 84.4 |
| Y | −40 … 40 | −0.526 … 77.63 |
| Z | 2 … 52 | −7 … 15 |

XY 大致差 +40 偏移，但 Z 范围完全不匹配（22mm vs 50mm），说明工件原点/装夹方向不同。
**因此本轮不做 `.ngc` 与 STEP 的几何配准，也不拿它算复现误差**；
它只适合做工序/刀具/转速的统计对照（如 `(T1 D=6. ... ZMIN=-7)`、`S18000`）。
若论文要引用真实 `.ngc` 作外部效度，必须先做坐标配准并单独报告配准误差。

## 4 评估口径

```
目标     = STEP 三角网格栅格化的高度图 target_top（0.5mm）
A 路     = FreeCAD CAM MillFace 刀路 → ngc.parse → dexel.simulate
B 路     = M3.1 模板刀路，同样仿真（对照）
复现误差 = 有材料柱上 |cur − target_top| 的最大值
```

`max` 对单个够不到的窄缝柱极敏感，所以同时报平均、P95/P99 与「超 0.5mm 柱占比」。
`dexel.simulate` 已加这些字段，M3.1 的报告同步受益。

## 5 结果

| 件 | A 平均偏差 | A P99 | A max | 超 0.5mm 柱 | A 过切 mm³ | B max | 验收 |
|---|---|---|---|---|---|---|---|
| 1 | **0.029** | 1.000 | 7.0 | 1.12% | 0 | 6.0 | FAIL |
| 2 | **0.041** | 1.000 | 4.0 | 1.46% | 200 | 0.0 | FAIL |
| 3 | 2.920 | 5.000 | 7.0 | 72.30% | 18486 | 4.0 | FAIL |
| 4 | 2.259 | 5.000 | 7.0 | 55.78% | 14258 | 4.0 | FAIL |
| 5 | 2.581 | 5.000 | 6.0 | 64.04% | 16322 | 4.0 | FAIL |
| 6 | 1.315 | 6.000 | 6.0 | 28.14% | 8219 | 6.0 | FAIL |
| 7 | 1.419 | 6.000 | 10.0 | 23.82% | 8777 | 10.0 | FAIL |
| 8 | 10.769 | 16.000 | 17.0 | 95.63% | 68304 | 8.0 | FAIL |
| 9 | 1.940 | 8.000 | 8.0 | 31.57% | 12349 | 8.0 | FAIL |
| 10 | 12.225 | 18.000 | 19.0 | 95.63% | 77552 | 18.0 | FAIL |

汇总：`pass_count = 0/10`，白名单 10/10，最坏平均 12.225mm、最坏 P99 18mm、
A 路总过切 224466 mm³（B 路 196727，A 反而多 14.1%）。

**怎么读这个表**：件 1/2 证明「CAM 刀路 → 解析 → dexel 仿真」这条链路是准的
（平均 3–4 丝），失败集中在多级腔/岛/让位件；`max` 的整齐整数（7/10/19mm）
是少数柱完全没被切到（53 − 目标 z）把最大值拉爆，不代表整体差那么多，
但平均偏差与超差柱占比同样不达标，**不是指标挑得不好，是刀路确实不够**。

## 6 为什么没达标（初版分析，部分已被补记修正）

1. **只有 MillFace**：孔靠 `Path.Op.Drilling`、型腔靠 `Pocket`、凸台靠 `Contour`，
   本版一律用面铣代替；复杂件的竖直壁、深腔、岛侧壁没有对应工序。
2. **默认刀具 Ø5 + 默认进退刀**：没有按特征选刀、没有刀具半径补偿策略，
   轮廓边缘残留/过切被放大。
3. **同高不同义的面**：件 10 的 z=44 同时有沉孔底、方腔底肩面、星形岛顶，
   全按 MillFace 处理会互相覆盖（M3.1 已发现同一问题）。
4. **0.5mm 栅格 + 单值 Z-dexel**：窄缝、倒扣、竖直壁无法精确表示。
5. **毛坯/装夹简化**：只加了 1mm 顶面余量，没有夹具与避让。

件 8/10 的 A 过切与 B 路几乎相同，是**饱和效应**：两套刀路都把大片区域削到最低 z，
`cur` 取 min 后结果一致，不是评估脚本读错文件。

## 7 下一步（初版清单，历史记录）

1. 补齐 `Path.Op.Pocket` / `Drilling` / `Contour`，按特征族派工序（复用 `cam/process.py` 的 DAG）。
2. 给每道工序显式 ToolController，按孔径/圆角半径选刀（知识库已有 `pick_tool`）。
3. 用目标高度图做**工序区域约束**：`op.Base` 只给对应特征的面，避免同高面互相覆盖。
4. 验收口径改成「P99 偏差 ≤0.5mm 且超差柱 ≤5%」更稳健，但**必须在完成 1–3 之后重新定线**，
   不能为了通过而改定义。
5. 真实 `.ngc` 的坐标配准单独做一个小实验，配准误差报出来再决定能否作外部效度。

## 8 复现

```bash
# 1) FreeCAD 侧生成 A 路
CADCAM_DATA=data/zenodo_rptu CADCAM_OUTDIR=reports/m3_gcode_ref \
CADCAM_META=reports/cam_reference_meta.json \
  "E:/FreeCAD_1.1.4-Windows-x86_64-py311/bin/freecadcmd.exe" scripts/cam_reference.py

# 2) 宿主侧评估
PYTHONPATH=src python scripts/m3_cam_eval.py
```
