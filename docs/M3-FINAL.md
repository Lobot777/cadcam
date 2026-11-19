# M3 收尾报告：制造模态 + 验证器

> 日期：2026-11-05（22:20 结案）　｜　状态：**M3 完成。口径按 ADR-001 双线落地；人机一致性已结案并降级为 sanity check（平凡基线 1.0000，不作构念效度证据）**
> 依据：`DEVELOPMENT-PLAN.md` §4.2 / §4.3 / §6（M3 行）
> 全栈 FreeCAD / OCC 开源。跨内核论点已由用户决定移除，不在本里程碑范围内。
> 分项文档：`M3-PLAN.md`（M3.1 规格）、`M3-CAM-REFERENCE.md`（A 路技术细节）

---

## 0 一句话

M3 建成了「工序 → 受限 G-code → 解析 → 材料去除仿真 → 结构化错误 → 定点修复信息」的完整闭环，
并把它从"能跑"推进到"**零过切**"：区域掩码把 10 件锚点集的总过切从
**196727 mm³ 压到 0**，最坏平均复现偏差从 **12.12 mm 降到 0.206 mm**。
口径已于 21:20 由用户确认「按 C 改」并落地（双线：B2 10/10、A 2/10）。
人工标注收回 3 份，v1/v2 两轮评分都是 1.000 —— 但打乱题序后不变，且等于「只数红色像素」
的平凡基线 **1.0000**，根因是 colorbar 图例把答案写出来了。**该条已降级为 sanity check，
不作构念效度证据**，见 `M3-HUMAN-QA.md`。

---

## 1 三路刀路对比（10 件锚点集，0.5mm Z-dexel）

| | **A 路** FreeCAD CAM | **B1** M3.1 矩形模板 | **B2** M3.2 高度图区域铣 |
|---|---|---|---|
| 生成依据 | 每朝上水平面一道 MillFace（Face Region）+ 自动 Drilling | 特征实例 bbox 模板 | STEP 目标高度图 + 刀具半径 Minkowski 偏置 |
| 白名单合规 | 10/10 | 10/10 | 10/10 |
| 总过切 mm³ | 215127 | 196727 | **0** |
| 最坏平均偏差 mm | 12.225 | 12.118 | **0.206** |
| 最坏 max 偏差 mm | 19.0 | 18.0 | 10.0 |
| 最坏超 0.5mm 柱占比 | 95.63% | 95.06% | **2.84%** |
| 总残留 mm³ | — | — | 4605.6 |
| 工序语义 | CAM 原生 | 特征族 DAG | **按高度层，无语义** |

逐件数据：`reports/m3_heightmap_eval.json`、`reports/m3_cam_eval.json`。

**B2 的零过切不是调指标调出来的。`heightmap.feasible_centers()` 对每个目标高度层 L
计算刀中心可行域（把 `goal > L` 的柱按刀具半径膨胀后取补），
保证圆盘覆盖的所有柱都有 `goal <= L`，因此在几何上就不可能产生 `cur < goal`。**
这是标准 constant-Z 粗加工 + 偏置，输入是 CAD 几何本身，不是仿真结果——不构成循环论证。

---

## 2 M3.2 六项任务的状态

| # | 任务 | 状态 | 硬证据 |
|---|---|---|---|
| 1 | FreeCAD CAM A 路 | ✅ 接通，**验收未过** | headless 10/10 生成；补 Drilling 自动找孔；`BoundaryShape=Face Region` 修好小肩面；但 ≤0.5mm **0/10**，见 §3 |
| 2 | 工序模板加区域掩码 | ✅ 完成 | `cam/heightmap.py`；10/10 零过切；件 10 过切 77552→0 |
| 3 | `E_GOUGE` 带 `op_id`/`face_id` | ✅ 完成 | 9/10 件带归因；件 10 精确归到 `STEP3 / OP04 / face 32` |
| 4 | G0 穿料 `E_COLLISION` | ✅ 完成 | 10/10 检出注入缺陷；干净刀路 0 误报 |
| 5 | 人工过切标注 30 份 | ⚠️ **两轮 1.000 = 平凡基线 1.0000；降级为 sanity check，不作构念效度证据** | `docs/M3-HUMAN-QA.md`、`reports/m3_human/{score_result,ceiling_baseline}.json` |
| 6 | CAMotics 交叉验证 | ⬜ 未做（计划中标注为可选） | — |

`m3_demo.py` 全量回归：65 工序 / 2271 行 / 1797 段，白名单 10/10，
三类注入缺陷（过切 / 少切 / 碰撞）**10/10 检出**，DFM 6 条，目标高度图覆盖率 1.000。
全量测试 **100 passed**（M3.1 的 85 → M3.2 前的 95 → 本轮 +4）。

---

## 3 A 路为什么仍然 0/10：一个明确的 negative result

接入顺序与三个 API 坑见 `M3-CAM-REFERENCE.md`。本轮又做了两处改进：

- **Drilling**：`Create` 后必须先 `job.Operations.addObject(op)` 再 `recompute()`，
  Proxy 才会从 model 自动找可钻圆柱面（件 2 找到 4 个孔，生成 4 个 G81）。
  件 9 过切 18769→4819、件 7 17514→8754。
- **`BoundaryShape`**：默认 `Boundbox` 会把小面扩成整个零件包围盒，
  改成 `Face Region` 后件 10 的 z=48 四角肩面走刀区从 `[-24.7,-3.5]` 收窄到 `[-24.7,-17.2]`，
  件 2 过切 200→0。

**但件 8 / 10 完全没改善**。根因用 `scripts/cam_probe6.py` 定位：

| BoundaryShape | ExcludeRaisedAreas | 件 10 F32（z=34，面积 316mm²）走刀范围 |
|---|---|---|
| Boundbox | False / True | X=[-37.5, 37.5] / 无刀路 |
| Face Region | False / True | X=[-37.5, 37.5] / 无刀路 |
| Perimeter | False / True | X=[-37.5, 37.5] / 无刀路 |
| Stock | False / True | X=[-38.5, 38.5] / 无刀路 |

**四种模式都约束不住**。原因不是配置错误，而是 MillFace 的语义就是
「从毛坯顶面**铣平**到 z」：对最深的 z=34 层，它必然要清掉上方 19mm 的所有材料，
而目标几何里只有 1264 柱（约 5%）真的是 z=34，其余是 44/48/52。
要正确加工多级腔，必须用 `Pocket` / `Profile` 按封闭轮廓 + 显式 ToolController +
深度链做完整 CAM 编程——这是独立的一块工程，不是"接通 FreeCAD CAM"能顺带完成的。

**这个 negative result 对论文反而是有用的**：通用工业面铣在这批多级腔零件上会产生
大面积过切，而带区域掩码的高度图方法几何上保证零过切。这正是 C3「验证接地」
要论证的东西，比"我们调通了 FreeCAD CAM"更有价值。

---

## 4 两条 M3 验收：逐条说清

`DEVELOPMENT-PLAN.md` §6 给 M3 定了两条验收线。

### 4.1 参考刀路复现误差 ≤ 0.5mm

口径问题必须先摊开，不能事后挑好看的定义：

| 口径 | A 路 | B1 | B2 | 谁通过 |
|---|---|---|---|---|
| `max_deviation_mm` | 19.0 | 18.0 | 10.0 | **都不过** |
| `p99_deviation_mm` 最坏 | 18.0 | — | 10.0 | **都不过** |
| `mean_deviation_mm` 最坏 | 12.225 | 12.118 | **0.206** | **B2 10/10 通过** |
| 过切体积 | 215127 | 196727 | **0** | **B2 通过** |

`max` / `P99` 对**少数够不到的窄缝柱**极敏感：0.5mm 栅格下，刀具半径无法进入的
内圆角与台阶根部会整柱残留到台阶高度差（4–10mm），把分位数拉爆。
超 0.5mm 柱占比 B2 最坏只有 2.84%，把这些柱从"平均"里剔除后指标是 0.206mm。

**口径已确认并执行（用户 2026-11-05「按 C 改」，ADR-001 Accepted）**：见 `docs/M3-ACCEPTANCE-CRITERION.md`
与 `docs/decisions/ADR-001-reproduction-criterion.md`。实证（`scripts/m3_criterion_analysis.py`）：

- B2 的 10mm `max` **100% 是残留**（`max_gouge_depth = 0.000mm`，10/10 件），
  不是过切。过切不可逆（报废）、残留可补刀，原 `|cur−goal|` 把两者混为一谈是指标设计缺陷。
- 超差柱 **10/10 件 100% 落在台阶边界带内**，是刀具半径够不到的环带，不是散布误差。
- `max` 是离散化伪影：栅格 0.5→1.0mm 时 max 10.0→0.0、mean 0.206→0.000。
- 新口径**有判别力**，不是为 B2 定制：硬约束「最大过切 ≤0.5mm」下
  A 2/10、B1 1/10（最坏 18mm）、B2 10/10。
- 兜底：补 Ø0.75 精刀可让 `max` 也归零，代价 G-code 2.7 倍，工艺不现实（讨论节用）。

**已落地的定义**：硬约束 `max_gouge_depth ≤0.5mm` + 精度 `mean_abs_deviation ≤0.5mm`；
`max` / P99 / 超差柱占比 / 残留体积只报不判。

| 报告 | 新口径结果 | 旧 max 口径（保留作对照） |
|---|---|---|
| `reports/m3_heightmap_eval.json` | **B2 10/10 通过**（`accept_dual_criterion = true`） | 0/10 |
| `reports/m3_cam_eval.json` | **A 路 2/10 通过**（`pass_count = 2`） | 0/10 |

`DEVELOPMENT-PLAN.md` §6 规格契约、两个评估脚本的 `acceptance_v2`、ADR-001、OPEN-DECISIONS、
DECISIONS O6 均已同步。**论文须同时写出原 max 口径下 B2 为 0/10（最坏 10mm 全为残留）**，
不得只报通过。

### 4.2 人机一致性 sanity check ≥ 90%（原「人工标注一致率」口径已降级）

**两轮都是 1.000，且恰好等于只数红色像素的平凡基线 1.0000 —— 因此不作为构念效度证据。**

`CESHI/` 三份作答（E1–E3）在 v1、v2 两个题目包上评分均为：逐人一致率 / κ = 1.000，
多数投票 1.000，`accept_ge_90pct = true`，分难度三档均 1.000。但：

| 轮次 | 归因 | 证据 |
|---|---|---|
| v1 | 顺序泄漏（次要） | H01–H15 全 `no_gouge`、H16–H30 全 `gouge`，最长连续 15 |
| **v2** | **图例泄漏（主因）** | 题序已打乱（最长连续 **4**），一致率**不变**；colorbar 直接标注「准确/残留/过切」，与二分类问题同构 |

量化（`scripts/m3_human_baseline.py`，`reports/m3_human/ceiling_baseline.json`）：
无过切样本红色像素最大值 **0**，过切样本最小值 **40 986**（≈ 零件足迹的 25%），
阈值取 1 即可把 30 题全部判对 → **平凡像素分类器准确率 1.0000**。
名义「hard」的局部过切，红斑仍占零件面积 25%–56%，是一眼可见的色块。

**结论**：该实验只能说明「验证器的报警与可视化一致，未出现报过切而图上无红」，
不能说明标注者具备独立判读能力。写作口径见 `M3-HUMAN-QA.md` §4.2；
构念效度需与仿真输出解耦的通道（刀路 + 零件图），列为后续工作。
`DEVELOPMENT-PLAN.md` §6 已按此降级。

---

## 5 人工过切标注包（v2；已结案，含平凡基线）

`reports/m3_human/`（v2；v1 归档在 `reports/m3_human_v1/`）：

| 文件 | 内容 | 能否发给评估者 |
|---|---|---|
| `annotate.html` | 30 张偏差热图、三选一、把握度、备注、进度自动保存、导出 JSON（单文件离线 727KB） | ✅ 仅作预实验 |
| `img/H01–H30.png` | 30 张热图（红=过切 / 蓝=残留 / 灰=准确）——**图例即答案，这是天花板根因** | 会泄漏 |
| `key.json` | 真值 + 难度档 | ❌ 主试留存 |
| `validator.json` | 验证器独立预测 | ❌ 主试留存 |
| `samples.json` | 题目元数据（含 base64） | ❌ 不必发 |
| `key.json` 的 `pack_version` / `order_seed` | v2 版本标识，导出 JSON 与 key 不符会被评分脚本拒绝 | ❌ 主试留存 |
| `responses/` | v2 回收的 3 份真人 JSON（`pack_version = v2`） | — |
| `ceiling_baseline.json` | 平凡像素基线 1.0000（红线） | 审计用 |
| `reports/m3_human_v1/` | v1 题目包 + 3 份作答 + 两份 json | ❌ 只作审计 |

**方法学关键：真值来自已知注入，不是 dexel 自己。**
否则拿验证器结果当答案键再报"人与验证器一致率"就是同义反复。

| 档 | 构造 | 题数 | 真值 |
|---|---|---|---|
| easy | B2 干净 / 注入 1.0mm 过切 | 8 / 7 | 8 无 + 7 有 |
| medium | 0.5mm 抬刀（**只有残留，蓝色干扰项**）/ 注入 0.3mm 过切 | 7 / 4 | 7 无 + 4 有 |
| hard | 只在一道高度层注入 0.8mm（**局部**过切） | 4 | 4 有 |

合计 30 题，真值 15 / 15 平衡；验证器对真值 **30/30**。
评分脚本报：每人准确率 / 判不了率 / 与验证器一致率 / Cohen κ，
以及**多人多数投票**对验证器的一致率（保守口径，`accept_ge_90pct` 用这个），
并按难度分层。已用 3 份模拟数据端到端跑通（演练结果 0.833，未过——说明脚本能区分，不是恒真）。

---

## 6 已知限制（写进论文）

1. **Z-dexel 是 2.5D**：每柱单值顶面，不支持倒扣 / 侧凹 / 五轴；与项目 §9 一致。
2. **B2 按高度层而非特征实例驱动**，丢失工序、刀具、装夹语义。
   它是**几何参照刀路**，用于验证器与复现误差；M4 的智能体仍需从 `process.plan` 的
   特征 DAG 出发生成刀路，不能直接拿 B2 当智能体输出。
3. **B2 残留 4605.6 mm³**，集中在内圆角与窄缝，是刀具半径的物理限制
   （Ø6 粗 + Ø1.5 精两遍）。M4 的 Repair Agent 应据此选更小的刀或补精加工工序。
4. **A 路未做完整 CAM 编程**：缺 Pocket / Profile / 显式 ToolController / 深度链，
   详见 §3。本轮不做，也不宣称 A 路达标。
5. **真实 `.ngc` 机床坐标系与 STEP 不匹配**（件 1：ngc X[-3,84] Z[-7,15] vs
   STEP XY[-40,40] Z[2,52]），未做几何配准，只适合工序/刀具统计对照。
6. **CAMotics 交叉验证未做**；若审稿人要求第三方仿真佐证，需补。
7. **DFM 未覆盖刀柄包络 vs 夹具**（需要装夹模型）。
8. 本设计**无法分离单个字段（极性 / 邻面 z / 邻面法向 / 培训）对指标的贡献**，
   若被追问需另做消融实验。

---

## 7 一键复现

```bash
# B1（M3.1 模板）+ 验证器回归
PYTHONPATH=src python scripts/m3_demo.py

# A 路（在 FreeCAD 1.1.4 自带解释器里跑）
CADCAM_DATA=data/zenodo_rptu CADCAM_OUTDIR=reports/m3_gcode_ref \
  CADCAM_META=reports/cam_reference_meta.json \
  "E:/FreeCAD_1.1.4-Windows-x86_64-py311/bin/freecadcmd.exe" scripts/cam_reference.py
PYTHONPATH=src python scripts/m3_cam_eval.py

# B2（高度图区域铣）+ 三路评估
PYTHONPATH=src python scripts/m3_heightmap.py

# 人工标注包（需 matplotlib，用系统 python）
PYTHONPATH=src python scripts/m3_human_pack.py
PYTHONPATH=src python scripts/m3_human_pack.py --score \
  reports/m3_human/responses/E1_M3_HUMAN.json \
  reports/m3_human/responses/E2_M3_HUMAN.json \
  reports/m3_human/responses/E3_M3_HUMAN.json

# 全量回归
PYTHONPATH=src python -m pytest tests -q
```

产物：`reports/m3_acceptance.json`、`m3_cam_eval.json`、`m3_heightmap_eval.json`、
`m3_gcode/`、`m3_gcode_ref/`、`m3_gcode_hm/`、`m3_human/`。
固定口径：dexel 0.5mm、毛坯顶面余量 1.0mm、材料 Al6061-T6、ap=4mm、
B2 两遍刀 Ø6 / Ø1.5。

---

## 8 变更记录

| 日期 | 变更 |
|---|---|
| 2026-11-05 | M3.1 纵切：工艺库 / 工序 DAG / 受限 G-code / Z-dexel / DFM，93 passed |
| 2026-11-05 | M3.2-1：FreeCAD CAM A 路接通，headless 10/10；≤0.5mm 验收 0/10，95 passed |
| 2026-11-05 | M3.2-2：A 路补 Drilling + Face Region 并定位 BoundaryShape 根因；B2 区域掩码零过切；错误归因 + E_COLLISION；30 题人工包；**100 passed** |
| 2026-11-05 21:20 | 用户确认「按 C 改」：ADR-001 双线落地到规格契约与两个评估脚本，B2 10/10、A 2/10；3 份真人作答收回，评分 1.000 但检出顺序泄漏，v2 修包并归档 v1（`M3-HUMAN-QA.md`） |
