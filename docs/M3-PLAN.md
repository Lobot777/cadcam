# M3 制造模态 + 验证器

> 状态：**M3 代码侧全部完成**（2026-11-05）｜口径已按 ADR-001 双线确认并落地；人工 v1 形式通过但需复核（v2 待重做）
> **收尾报告 `docs/M3-FINAL.md`｜待办交接 `docs/NEXT-ACTIONS.md`**
> A 路技术细节 `docs/M3-CAM-REFERENCE.md`
> 依据：`DEVELOPMENT-PLAN.md` §4.2 / §4.3 / §6（M3 行）
> 全栈 FreeCAD / OCC 开源；跨内核论点已由用户决定移除，不在本里程碑范围内。

---

## 1 M3.1 做了什么

一条**端到端纵切**，把 M2 的特征实例接到可执行的 G-code 与材料去除仿真：

```
geom/<n>.json + labels/<n>.json
      ↓ mfr.instances.group  (M2 已有)
   特征实例
      ↓ cam.process.plan
   工序 DAG（含刀具/参数/依赖）
      ↓ cam.gcode_gen.generate
   受限 G-code
      ↓ cam.gcode_gen.validate（白名单 + 模态）+ ngc.parse
   刀路 IR
      ↓ verify.dexel.simulate（Z-dexel 材料去除）
   gouge / residual / max_deviation
      ↓ verify.dexel.errors + verify.dfm.check
   结构化错误对象  →  M4 的 Repair Agent 输入
```

| 模块 | 职责 |
|---|---|
| `cam/knowledge.py` | 材料 × 刀具 × 参数包络；越界硬钳制并产出 `E_PARAM_*` 违规 |
| `cam/process.py` | 特征族 → 工序模板；毛坯留 1mm 顶面余量；线性 DAG |
| `cam/gcode_gen.py` | 模板刀路 → 受限 G-code；白名单/模态校验；验收用缺陷注入 |
| `verify/dexel.py` | 三角网格栅格化成高度图；沿 IR 扫掠刀具；过切/残留体积 |
| `verify/dfm.py` | 内圆角 vs 刀具半径、深径比、出界、穿底、参数违规 |
| `scripts/m3_demo.py` | 10 件锚点集一键跑 → `reports/m3_acceptance.json` |

复用 `cam/ngc.py` 的白名单与解析器，**生成器与解析器共用同一套白名单**，
不会出现「自己生成、自己解析不了」。

### 关键口径

- **材料**：Al6061-T6 / 45 钢 / Ti6Al4V，各自 `vc / fz / ap / ae` 上下界；
  钻孔没有 `ae`，标注为「不适用」（`None`）而不是伪造成越界。
- **工序映射**：`stock→面铣`、`pocket/slot→型腔环切`、`step→肩面铣`、
  `hole→G83 啄钻`、`boss→轮廓`、`fillet/chamfer→本版跳过并记录原因`。
- **G-code 白名单**：`G0/G1/G2/G3/G81/G82/G83/G17/G21/G54/G90/G94` +
  `M3/M5/M6/M8/M9/M30`（沿用 `ngc.WHITELIST_G/M`）。
- **仿真**：0.5mm Z-dexel，三角网格栅格化目标高度图，平底刀圆盘扫掠压低当前顶面；
  `cur < target_top` 记过切，`cur > target_top` 记残留，按柱面积换算体积。
  每道工序用**自己的**刀具半径（首版统一用 Ø6 曾虚报 816mm³ 残留，已修）。

---

## 2 验收结果（10 件锚点集）

```bash
PYTHONPATH=src python scripts/m3_demo.py
```

| 件 | 实例 | 工序 | G-code 行 | 段 | 过切 mm³ | 残留 mm³ | DFM 错误 |
|---|---|---|---|---|---|---|---|
| 1 | 2 | 2 | 96 | 78 | 2880.0 | 0 | 0 |
| 2 | 5 | 5 | 101 | 61 | **0** | **0** | 0 |
| 3 | 7 | 7 | 169 | 117 | 18336.0 | 0 | 0 |
| 4 | 7 | 7 | 169 | 117 | 14108.0 | 0 | 0 |
| 5 | 7 | 7 | 169 | 117 | 16222.0 | 0 | 0 |
| 6 | 8 | 8 | 284 | 226 | 10506.0 | 8.0 | 0 |
| 7 | 7 | 3 | 393 | 369 | 17514.1 | 0 | 0 |
| 8 | 5 | 5 | 193 | 157 | 20840.0 | 3736.0 | 2 |
| 9 | 14 | 14 | 372 | 278 | 18768.6 | 194.0 | 4 |
| 10 | 11 | 7 | 325 | 277 | **77552.1** | 0 | 0 |

**可机械检查的硬指标（全部通过）**

| 指标 | 结果 |
|---|---|
| 白名单合规 | **10/10**，`unknown_codes = []` |
| 生成→解析 round-trip | 1797 段全部可解析，65 道工序、2271 行 |
| 目标高度图覆盖率 | 10/10 件 = **1.000** |
| 注入 1mm 过切缺陷被检出 | **10/10** |
| 注入 1mm 少切缺陷被检出 | **10/10** |
| DFM 结构化错误 | 6 条（件 8 两条、件 9 四条，均为 R3 圆角 < 刀具半径），字段齐全 |
| 全量测试 | **93 passed**（原 85 + M3 新增 8） |

> ⚠️ **不要把这个表读成「刀路可用」**。M3.1 的验收对象是**闭环与验证器**，
> 不是刀路质量。模板刀路在复杂件上过切严重，见下节。

---

## 3 已知问题：模板刀路的结构性过切（M3.2 的基线）

最坏是件 10，过切 77552 mm³。诊断（真实数据，非猜测）：

- 目标高度图的 z 分布：`34` 只有 1264 柱（约 5%），`40` 915 柱，
  `44` 12183 柱，`48` 4834 柱，`52` 6396 柱。
- 但 `STEP3` 工序按整个 80×80 实例 bbox 一刀削到 **z=34**，
  等于把本来该留在 44/48/52 的材料也削掉了。

根因不是仿真错误，而是**工序模板只有矩形 bbox、没有区域掩码**：
面铣/型腔/肩面都按矩形范围走刀，圆形腔、星形岛、同高不同义的肩面都会被误切。
首版已修掉两个明显的深度错误（凸台按 bbox 底面下刀、台阶按 bbox 最低点下刀），
件 10 过切从 102627 降到 77552、件 7 从 68408 降到 17514，但结构性缺陷仍在。

这正是 C3「验证接地的制造闭环」要的东西：**验证器先能可靠地检出错误，
M3.2 / M4 才有可优化的目标**。当前 `E_GOUGE` 只带体积与柱数，
M3.2 会把 `op_id` / `face_id` 也带上，让 Repair Agent 能定位到具体工序。

其它明确限制：

- Z-dexel 是 2.5D 假设（每柱单值顶面），**不支持倒扣 / 侧凹 / 五轴**；
  与项目 §9「不做五轴」一致，但论文 limitation 要写。
- 刀路是模板化蛇形/环切/啄钻，不是 FreeCAD CAM 的参考刀路（A 路尚未接入）。
- DFM 未覆盖「刀柄包络 vs 夹具」（需要装夹模型）。
- 计划里 M3 的最终验收「过切检测与人工标注一致率 ≥90%」**尚未做**：
  需要人工标注的过切真值；本版用「注入已知缺陷」做代理自检，不能顶替。

---

## 4 M3.2 任务（按优先级）

1. ~~接入 FreeCAD CAM 生成 A 路参考刀路~~ **已完成 2026-11-05**（`M3-CAM-REFERENCE.md`）：
   headless 10/10；本轮又补 **Drilling 自动找孔** 与 `BoundaryShape=Face Region`。
   但「≤0.5mm」仍 **0/10 通过**；根因已定位为 MillFace 的「铣平到 z」语义，
   四种 BoundaryShape 均无法约束（`cam_probe6.py`），属完整 CAM 编程范畴，**本轮不做**。
2. ~~给工序模板加区域掩码~~ **已完成**：`cam/heightmap.py`，10/10 **零过切**
   （总过切 196727→0 mm³），最坏平均偏差 **12.118→0.206 mm**。见 `M3-FINAL.md` §1。
3. ~~`op_id` / `face_id`~~ **已完成**：`dexel.simulate(op_faces=...)` 按工序归因，
   件 10 精确归到 `STEP3 / OP04 / face 32`；9/10 件带归因字段。
4. ~~G0 穿料 `E_COLLISION`~~ **已完成**：`gcode_gen.validate(text, stock=...)`，
   10/10 检出注入缺陷、干净刀路 0 误报。
5. **人工过切标注**：v1 收回 3 份、评分 1.000 形式通过，但检出**顺序泄漏**
   （H01–H15 全 no_gouge、H16–H30 全 gouge，最长连续同真值 15）与三人逐题 100% 一致，
   门禁 fail、**暂不能作为独立盲标证据**。v1 已归档 `reports/m3_human_v1/`；
   v2 打乱题序（最长 4）、加版本隔离与回归断言，待 3 人重做或降级披露。
   详见 `docs/M3-HUMAN-QA.md`、`NEXT-ACTIONS.md` 第 2 项。
6. CAMotics 交叉验证：**未做**（可选项，写进论文 limitation）。

---

## 5 复现

```bash
PYTHONPATH=src python scripts/m3_demo.py                 # B1 模板 + 验证器全量回归
PYTHONPATH=src python scripts/m3_heightmap.py            # B2 高度图刀路 + 三路评估
PYTHONPATH=src python scripts/m3_cam_eval.py             # A 路复现误差（需先跑 cam_reference.py）
PYTHONPATH=src python -m pytest tests -q                 # 100 passed
```

产物：`reports/m3_acceptance.json`、`reports/m3_gcode/<n>.nc`。
固定口径：dexel 分辨率 0.5mm，毛坯顶面余量 1.0mm，材料 Al6061-T6。

---

## 6 变更记录

| 日期 | 变更 |
|---|---|
| 2026-11-05 | 建立 M3.1 纵切；首版统一刀具半径虚报残留、凸台/台阶下刀过深两个 bug 已修并回归 |
| 2026-11-05 | M3.2-1：A 路接通（headless 10/10），≤0.5mm 验收 0/10 |
| 2026-11-05 | M3.2-2：A 路补 Drilling + Face Region 并定位根因；B2 零过切；错误归因 + E_COLLISION；30 题人工包；**100 passed**。详见 `M3-FINAL.md` |
