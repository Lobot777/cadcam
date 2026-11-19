# ADR-001: M3 复现误差验收按过切/残留分离为双线指标

## Status

**Accepted (2026-11-05)** — 用户回复「按 C 改」后升格；规格契约与全部报告已同步。
完整数据与论证：`docs/M3-ACCEPTANCE-CRITERION.md`。
落地证据：`reports/m3_cam_eval.json` → `summary.criterion = "ADR-001 dual: ..."`、
`reports/m3_heightmap_eval.json` → `summary.accept_dual_criterion = true`。

## Background

`DEVELOPMENT-PLAN.md` §6 给 M3 定的验收线是「参考刀路复现误差 ≤ 0.5mm」，
未指明聚合口径。实测出现口径歧义：

- 高度图区域铣（B2）零过切，最坏**平均**偏差 0.206mm，但 `max` 偏差 10mm；
- 按平均判 10/10 通过，按 `max` / P99 判 0/10 通过。

若不澄清就选一个，属于事后挑好看的定义，审稿可质疑。

## Decision

建议把单条验收线替换为两条**物理不对称**的独立指标：

| 类别 | 指标 | 阈值 | 理由 |
|---|---|---|---|
| 硬约束（安全） | `max(goal − cur)` 最大过切深度 | ≤ 0.5 mm | 过切不可逆、零件报废，是 CAM 的真正底线 |
| 精度（复现） | `mean\|cur − goal\|` 平均绝对偏差 | ≤ 0.5 mm | 等于体积误差 / 投影面积，有明确物理量纲、对分辨率稳定 |
| 报告量（不设阈值） | `max` / P99 / 超 0.5mm 柱占比 / 残留体积 | — | 由刀具半径够不到的边界环带主导，随栅格分辨率变化，不做跨配置判定 |

**变更性质是纠错，不是放宽**：原式 `|cur − goal|` 把「切穿 10mm（报废）」
与「还留 10mm 待补刀（可修复）」判成同等严重，是指标设计缺陷。

## Consequences

**正面**

- 数字不藏：10mm 的 max 残留、P99、超差柱占比全部照报，写进 limitation。
- 新口径有判别力，不是为 B2 定制：A 路 2/10、B1 1/10 通过硬约束（最坏过切 18mm），
  B2 10/10（0.000mm）。
- 有一个可直接写进论文的方法学观察：验证指标应区分可逆与不可逆误差。
- 兜底实验齐备：补一把 Ø0.75 精刀可让 `max` 也归零（代价 G-code 2.7 倍），
  可回应「max 是不是方法天花板」的质疑。

**负面 / 代价**

- 改动了 `DEVELOPMENT-PLAN.md` 的验收定义，必须在论文中显式声明并给出理由，
  且同时报原 `max` 口径下的结论（0/10 通过）。
- 需要同步 `M3-FINAL.md` §4.1、各 `accept_*` 字段、`NEXT-ACTIONS.md` 第 1 项。
- 「平均绝对偏差 ≤0.5mm」的阈值沿用原线，未重新论证其绝对值是否合适；
  若审稿人追问，需补一条与加工余量/公差的对应关系。

## Alternatives Considered

| 方案 | 结论 |
|---|---|
| A 仅改用平均偏差 | 能通过，但留下「因 max 过不了才换指标」的质疑；次优 |
| B 坚持 max、如实写未达标 | 学术最保守，但把可修复残留当成报废，物理上不正确，且自我削弱结论 |
| C 分离双线（本 ADR） | 推荐 |

## Related

- `docs/M3-FINAL.md` §4.1（两条验收的状态）
- `docs/NEXT-ACTIONS.md` 第 1 项（已关闭 2026-11-05 21:20）
- `reports/m3_criterion_analysis.json`（四组实证）
- `scripts/m3_criterion_analysis.py`（复现）
