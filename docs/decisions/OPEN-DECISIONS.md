# 悬而未决登记册（OPEN-DECISIONS）

> 规范：只追加 + 就地关闭（OPEN → RESOLVED 并补 Resolution），禁止整篇重写。
> 每个 Phase / 里程碑开始时把未决项复现到工作上下文，逐条判断能否关闭。
> 当前：**1 未决 + 2 已决（历史）**。

| Date | Source | Open Item | Related Constraints | Current Leaning | Blocked By | Resolves When | Status |
|------|--------|-----------|---------------------|-----------------|------------|---------------|--------|
| 2026-11-05 | M3 收尾 / 用户追问「定口径」 | 是否按 ADR-001 把 M3 复现误差验收线改为「最大过切 ≤0.5mm + 平均绝对偏差 ≤0.5mm」双线 | `DEVELOPMENT-PLAN.md` §6 是规格契约，改动须用户点头；`M3-FINAL.md` §4.1 与所有 `accept_*` 字段依赖此决定 | 已采纳：B2 10/10、A 2/10；规格契约与两个评估脚本已改双线 | `waiting-on-external-condition`：等用户一句确认 | 用户 21:20 回复「按 C 改」 | **RESOLVED：按 ADR-001 方案 C 落地** |
| 2026-11-05 | M3 收尾 | 是否继续投入 A 路完整 CAM 编程（Pocket/Profile/ToolController/深度链） | MillFace 语义与多级腔不匹配，四种 BoundaryShape 均无效；属独立工程 | 不投，作为 negative result 写进 limitation | 无（可自行决定） | 已按「不投」执行，见 `M3-FINAL.md` §3 | RESOLVED：不投 |

## 关闭记录

| Date | Item | Resolution |
|------|------|------------|
| 2026-11-05 | A 路是否继续投入 | 不投。`cam_probe6.py` 四组合实验证明根因是 MillFace 的「铣平到 z」语义；作为 negative result 反证区域掩码方法的价值。 |
| 2026-11-05 21:20 | 复现误差验收口径 | 用户确认「按 C 改」。`DEVELOPMENT-PLAN.md` §6、`m3_cam_eval.py`、`m3_heightmap.py` 全部改为 `acceptance_v2` 双线；ADR-001 → Accepted。 |

## 新增未决

| Date | Source | Open Item | Related Constraints | Current Leaning | Blocked By | Resolves When | Status |
|------|--------|-----------|---------------------|-----------------|------------|---------------|--------|
| 2026-11-05 21:20 | `M3-HUMAN-QA.md` 门禁 fail | v1 人工标注（1.000）因顺序泄漏不能当独立盲标证据：重做 v2 还是降级披露 | 3 名评估者时间；v1 已归档 `reports/m3_human_v1/`，v2 页面已生成 | **倾向重做 v2**（3×20 分钟），换人或间隔 ≥3 天 | `design-decision-to-evaluate`：等用户决定 | 用户回「重做」收齐 3 份 v2 JSON，或回「降级」按预实验披露 | OPEN |
