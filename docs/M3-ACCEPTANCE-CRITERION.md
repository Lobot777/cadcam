# M3 复现误差验收口径：决策分析

> 日期：2026-11-05　｜　状态：**Accepted（用户 2026-11-05 确认「按 C 改」）**
> 落地：`m3_cam_eval.py` / `m3_heightmap.py` 的 `acceptance_v2` 双线判定、`DEVELOPMENT-PLAN.md` §6、全部报告与文档
> 证据：`reports/m3_criterion_analysis.json`，可复现脚本 `scripts/m3_criterion_analysis.py`
> 关联：`M3-FINAL.md` §4.1、`NEXT-ACTIONS.md` 第 1 项、`docs/decisions/ADR-001-reproduction-criterion.md`

---

## 0 结论先行

```
verdict: pass（推荐方案 C 具备可辩护性）
blocking: 无
advisory:
  - 原验收线把「过切」与「残留」混进同一个 |cur − goal|，属指标设计缺陷，不是刀路不达标
  - 修正性质是「按物理不对称性分离」，不是「放宽到能过」
evidence: [Q1 分解表, Q2 边界带 100%, Q3 分辨率敏感性, Q4 细刀梯度, 三路判别力表]
```

**推荐方案 C：把一条验收线拆成两条各司其职的指标。**

| | 指标 | 阈值 | B2 实测 | A / B1 对照 |
|---|---|---|---|---|
| **硬约束（安全）** | 最大**过切深度** `max(goal − cur)` | ≤ 0.5 mm | **0.000 mm，10/10 通过** | A 2/10、B1 1/10（最坏 18 mm） |
| **精度（复现）** | 平均绝对偏差 `mean\|cur − goal\|` + 超 0.5mm 柱占比 | ≤ 0.5 mm 且 ≤ 5% | **0.206 mm / 2.84%，10/10 通过** | A 12.225 mm / 95.63%、B1 12.118 mm / 95.06% |
| **诊断量（如实报，不设阈值）** | 最大残留高度 / P99 / 残留体积 / `max_deviation` | — | 最坏 10.0 mm、残留 4605.6 mm³ | 同表列出 |

**上次我建议的「改用平均偏差」是次优解**。数据跑出来后应该修正推荐：
单独改平均虽然能过，但留给审稿人一句「你是因为 max 过不了才换指标」。
方案 C 把 max 保留下来、只是判它该判的东西（过切），并给出硬约束，
逻辑上更严、不是更松——**新指标还把 A 路和 B1 判成不合格**。

---

## 1 先搞清楚：那 10mm 到底是什么

`m3_criterion_analysis.py` 把 `cur` 与 `goal` 的差按符号拆开，10 件全量：

| 件 | A 最大过切 / 最大残留 | B1 最大过切 / 最大残留 | **B2 最大过切 / 最大残留** |
|---|---|---|---|
| 1 | 0 / 7 | 6 / 0 | **0 / 6** |
| 2 | 0 / 4 | 0 / 0 | **0 / 4** |
| 3 | 4 / 5 | 4 / 0 | **0 / 7** |
| 4 | 4 / 5 | 4 / 0 | **0 / 7** |
| 5 | 4 / 5 | 4 / 0 | **0 / 6** |
| 6 | 4 / 5 | 6 / 2 | **0 / 6** |
| 7 | 6 / 10 | 10 / 0 | **0 / 10** |
| 8 | 16 / 17 | 8 / 8 | **0 / 8** |
| 9 | 8 / 8 | 8 / 8 | **0 / 8** |
| 10 | **18** / 19 | **18** / 0 | **0 / 10** |

**B2 在 10 件上的最大过切深度全部为 0；`max_deviation = 10mm` 100% 来自残留。**

制造上这两类误差物理不对称：

- **过切**（`cur < goal`）：材料被切掉，**不可逆，零件报废**。
- **残留**（`cur > goal`）：材料还在，**补一刀即可修复**。

原来的 `|cur − goal| ≤ 0.5mm` 把「报废」和「待精加工」一视同仁地取绝对值，
于是「一根柱还留着 10mm 待补刀」和「一根柱被切穿 10mm」被判成同样严重的错误。
这不是刀路质量问题，是**指标定义问题**。CAM 领域的验收本来就该以过切为硬约束。

---

## 2 超差柱在哪：不是散布误差，是刀具够不到的边界环带

| 件 | 超 0.5mm 柱 | 占比 | 落在台阶边界带内 | 最大连通簇占比 | 其中过切柱 |
|---|---|---|---|---|---|
| 1 | 160 | 0.62% | **100%** | 100.0% | **0** |
| 2 | 64 | 0.25% | **100%** | 9.4% | **0** |
| 3 | 202 | 0.79% | **100%** | 68.3% | **0** |
| 4 | 172 | 0.67% | **100%** | 9.3% | **0** |
| 5 | 140 | 0.55% | **100%** | 11.4% | **0** |
| 6 | 286 | 1.12% | **100%** | 48.2% | **0** |
| 7 | 259 | 1.01% | **100%** | 51.0% | **0** |
| 8 | 562 | 2.19% | **100%** | 55.5% | **0** |
| 9 | 336 | 1.31% | **100%** | 5.4% | **0** |
| 10 | 728 | 2.84% | **100%** | 42.9% | **0** |

**10/10 件的超差柱 100% 落在「目标高度跳变处 ±1 格」的边界带内，过切柱为 0。**
这就是刀具半径偏置（Minkowski 腐蚀）留下的台阶根部环带，
是 2.5D 粗加工的典型、可预期、可补刀的结果，不是随机噪声或算法错误。

---

## 3 `max` 是离散化伪影，不能做跨配置验收线

同一个 B2 生成器，只把栅格分辨率从 0.5mm 改成 1.0mm：

| 件 | res 0.5：mean / max / 超差 | res 1.0：mean / max / 超差 |
|---|---|---|
| 1 | 0.037 / 6.0 / 0.62% | **0.000 / 0.0 / 0.00%** |
| 8 | 0.138 / 8.0 / 2.19% | **0.000 / 0.0 / 0.00%** |
| 10 | 0.206 / 10.0 / 2.84% | **0.000 / 0.0 / 0.00%** |

**10mm 的 max 在换一档栅格后直接消失。**

需要如实说明：本实现里 `res` 同时影响目标高度图栅格化与刀路生成的腐蚀半径，
两者是耦合的，所以这不能简单归因成「测量误差」。但结论不变——
**一个随离散化参数翻倍就归零的单点极值，不具备跨配置可比性，不适合当验收线**。
`mean` 与超差柱占比在同一变化下是连续、单调的（0.206→0），稳定得多。

---

## 4 兜底：审稿人若坚持 `max`，我们能过，只是代价不划算

件 10 的细刀梯度实验（0.5mm 栅格）：

| 刀具组合 | mean mm | max mm | 残留 mm³ | G-code 行数 |
|---|---|---|---|---|
| Ø6 / Ø1.5（现行） | 0.206 | 10.0 | 1317.7 | 10439 |
| Ø6 / Ø1.5 / Ø1.0 | 0.100 | 10.0 | 642.0 | 27524 |
| **Ø6 / Ø1.5 / Ø0.75** | **0.000** | **0.0** | **0** | **28245** |
| Ø6 / Ø1.5 / Ø0.5 | 0.000 | 0.0 | 0 | 28245 |

阈值很清楚：当刀具半径 `r < res/2 = 0.25mm` 的量级关系使腐蚀半径降为 0 格时，
刀中心可以放到每一个目标柱上，残留归零。Ø1.0 不够（仍 10mm），Ø0.75 够。

**所以「max 不过」不是方法天花板，是刀具选择。** 但 Ø0.75 刀逐柱走刀、
G-code 膨胀到 2.8 万行（2.7 倍），加工时间、刀具刚度、让刀都不现实。
这个实验的定位是**分辨率上界**，不是可交付刀路；论文里放讨论节，
用来回应「你这个 max 是不是方法缺陷」的质疑。

---

## 5 三个方案对比

| 维度 | A 改用平均偏差 | B 坚持 `max` 如实写未达标 | **C 分离（推荐）** |
|---|---|---|---|
| B2 结论 | 通过 | **未达标** | 通过 |
| 是否改动验收定义 | 是 | 否 | 是 |
| 改动性质 | 换聚合方式 | — | **纠错：把不对称的两类误差拆开** |
| 物理正确性 | 中 | 低（残留被当成报废） | **高** |
| 是否保留 max | 降级 | 主指标 | **保留，但判过切；残留单独如实报** |
| 对 A/B1 的判别力 | 有 | 有 | **有（A 2/10、B1 1/10）** |
| 审稿人最可能挑的点 | 「挑好看的指标」 | 「结论自己削弱」 | 「阈值 5% 是不是凑的」 |
| 论文可写性 | 一般 | 差 | **好（可写成一个方法学观察）** |

**C 唯一的可疑点**是「超差柱占比 ≤5%」这个阈值确实比 2.84% 宽。
两种处理：① 写死 5% 并给出理由（0.5mm 栅格下边界环带的理论上界）；
② 更稳的做法是**不设第二个阈值**，只把超差柱占比作为报告量，
硬约束与精度各一条即可。我倾向 ②——少一个可调参数就少一个被质疑的口子。

> 修正后的最终推荐定义（去掉 5% 阈值）：
> - **硬约束**：`max_gouge_depth_mm ≤ 0.5`
> - **精度**：`mean_abs_deviation_mm ≤ 0.5`
> - **报告量（不设阈值）**：`max_deviation_mm`、`max_residual_height_mm`、
>   `p99_mm`、`columns_over_0_5mm_pct`、`residual_volume_mm3`

---

## 6 可直接写进论文的措辞

> We evaluate toolpath fidelity with two asymmetric criteria, since over-cut
> (irreversible scrap) and residual material (removable by a finishing pass) are not
> equivalent in manufacturing. The hard safety constraint is the maximum over-cut
> depth ≤ 0.5 mm; reproduction accuracy is measured by the mean absolute deviation
> over all material columns, which equals the volumetric error divided by the
> projected area. We additionally report the maximum deviation, P99, and the fraction
> of columns exceeding 0.5 mm without attaching pass/fail thresholds, because these
> extrema are dominated by a one-cell boundary band that the cutter radius cannot
> reach and are sensitive to the dexel resolution.
>
> On the 10-part anchor set, our height-map-driven toolpath achieves zero over-cut
> on all parts (max over-cut 0.000 mm) and a worst-case mean absolute deviation of
> 0.206 mm. The FreeCAD CAM baseline and the bounding-box template reach the safety
> constraint on only 2/10 and 1/10 parts respectively (worst over-cut 18 mm),
> confirming that the criterion discriminates between methods rather than being
> tailored to one of them. The worst-case maximum deviation of 10 mm is entirely
> residual material located within one grid cell of a height step; it vanishes when
> the dexel resolution is coarsened from 0.5 to 1.0 mm, and can be eliminated with
> an additional Ø0.75 mm finishing pass at the cost of a 2.7× increase in G-code
> length, which we do not consider a practical toolpath.

中文摘要版：**验收按「最大过切 ≤0.5mm + 平均绝对偏差 ≤0.5mm」双线；
max 偏差、P99、超差柱占比只报不判。B2 零过切、最坏平均偏差 0.206mm；
A 路与 B1 在硬约束上仅 2/10 与 1/10 通过，说明该口径有判别力。**

---

## 7 复现

```bash
PYTHONPATH=src python scripts/m3_criterion_analysis.py
# 输出 reports/m3_criterion_analysis.json（Q1 分解 / Q2 边界带 / Q3 分辨率 / Q4 细刀）
```

脚本只读 `reports/annotation/geom/`、`labels/`、`m3_gcode_ref/`、`m3_gcode/`，
不写任何正式产物；细刀梯度只做敏感性实验，不改变 `m3_gcode_hm/` 的交付刀路。

---

## 8 已确认并落地（2026-11-05）

用户回复「按 C 改」，已执行：

| 位置 | 变更 |
|---|---|
| `DEVELOPMENT-PLAN.md` §6 | M3 验收线改为双线：最大过切 ≤0.5mm + 平均绝对偏差 ≤0.5mm |
| `scripts/m3_cam_eval.py` | `acceptance_v2{safety_max_gouge_le_0_5mm, accuracy_mean_le_0_5mm, pass}`；旧 `max` 判定保留为 `legacy_max_pass_count` 供对照 |
| `scripts/m3_heightmap.py` | 同上，`accept_dual_criterion` |
| `reports/m3_cam_eval.json` | A 路双线 **2/10**、legacy max 0/10 |
| `reports/m3_heightmap_eval.json` | B2 双线 **10/10**、legacy max 0/10 |
| `ADR-001` / `OPEN-DECISIONS` / `DECISIONS O6` | Status → Accepted / RESOLVED / 已关闭 |

**论文中必须同时写出**：原 `max` 口径下 B2 为 0/10（最坏 10mm，全部为残留），
口径变更的理由与本节 §1–§4 的证据，以及 A/B1 在新口径下仍不合格（2/10、1/10）。
不得只报通过、不报原口径结论。
