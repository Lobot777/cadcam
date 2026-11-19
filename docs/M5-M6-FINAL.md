# M5 + M6 收尾报告：基准冻结、论文与开源

> 日期：2026-11-05　｜　状态：**完成**
> 验收：M5「所有指标可一键重跑」→ `make bench`；M6「第三人按 README 2h 跑通」→ `make demo` + `docs/REPRODUCE.md`

---

## M5 基准与实验

### 1 数据集冻结

`make freeze` → `data/DATASET-MANIFEST.json`：**10 件 / 60 个文件 / 7.7 MB**，逐文件 sha256。

| 组 | 文件数 | 内容 |
|---|---|---|
| `zenodo_rptu` | 30 | STEP + 参考 .ngc + 渲染图（CC-BY 4.0） |
| `face_labels` | 20 | 面级标注（最终 + 规则草稿） |
| `geom_cache` | 10 | 三角网格缓存（仿真与渲染共用） |

外部训练集 MFCAD++ 不入库（体量大、按上游许可单独分发），清单里登记为 `external`，
附复现命令与产物路径。

### 2 一键复现台账

`make bench`（约 2 分钟）→ `reports/bench.json` + `reports/bench.md`。

| 步骤 | 状态 | 耗时 |
|---|---|---|
| `m3_cam` A 路复算 | ok | 6.3 s |
| `m3_heightmap` B1/B2 复算 | ok | 10.8 s |
| `m4` 三组对照（20 任务） | ok | 46.9 s |
| 测试 | 114 passed | 9.4 s |

台账刻意把**重算**与**存档**分开：M2 的训练要 GPU 小时，不能假装在 bench 里跑过；
它作为 `archived` 列出，附复现命令。`--reuse` 可在不重算的情况下只汇总。

**确定性**：全流程无随机采样；M4 黑板指纹同输入逐位一致（`test_run_is_deterministic`）。
连续两次 `make bench` 的关键数字完全相同（§4 表可对照）。

### 3 主图（5 张，`make figures`）

| 图 | 内容 | 数据来源 |
|---|---|---|
| `fig1_architecture` | 框架总览：四段流水线 + 编排层 | 手绘（matplotlib patches） |
| `fig2_recognition` | 面级识别整体指标 + 前 12 类逐类 IoU | `mfr_test_v2.json` |
| `fig3_three_routes` | 三路刀路的过切与平均偏差 | `m3_cam_eval.json` / `m3_heightmap_eval.json` |
| `fig4_criterion` | 栅格敏感性 + B2 超差柱分布 | `m3_criterion_analysis.json` |
| `fig5_orchestration` | 三组通过率 + 分族 + 修复轮次 | `m4_run.json` |

每张都同时出 PNG(200dpi) 与 PDF（投稿用矢量）。图内数字由脚本现算，不手抄。

### 4 关键数字（与论文 §5 逐行对应）

| 指标 | 值 |
|---|---|
| M2 GNN 准确率 / mIoU | 0.9660 / 0.9042 |
| M2 决策树基线 | 0.9075 / 0.7198 |
| B1 总过切 | 196 726.7 mm³ |
| B2 总过切 | **0.0 mm³** |
| B2 最坏平均偏差 | 0.2059 mm |
| B2 双线通过（旧 max 口径） | 10/10（0/10） |
| A 路双线通过 | 2/10 |
| M4 通过率（不修复 → 修复） | 0.100 → 1.000 |
| M4 平均修复轮次（冷 → 热） | 0.90 → 0.15 |

---

### 补记：LLM 本地接入（2026-11-06）

M5 之后追加了 LLM 接入，见 `docs/M5-LLM.md`：Qwen3-8B（Q4_K_M，4090 单卡）接入规划与修复两个决策点，
六组对照 20 任务。核心结论：

- 一句成本提示让 20/20 决策整体翻转 → 模型在"安全 vs 工时"上没有权衡，只有跟随最显著指令；
- 激进组 18/20 首轮过切，全靠有界修复救回 → **正确性来自验证器，不来自模型**；
- 结构化错误 / 散文 / 不给数值三组行为逐项相同 → "结构化让 LLM 修得更准"在本任务上**不成立**；
- C2 得到支持：实例级摘要 2.2× token 达到全量面表 95% 的准确率（10.4× token）。

## M6 论文与开源

| 交付 | 路径 | 说明 |
|---|---|---|
| 论文初稿 | `docs/PAPER-DRAFT.md` | 中文工作稿，含摘要/引言/相关工作/方法/实验/讨论/限制/结论 + 9 项投稿前 TODO |
| 主图 | `reports/figures/fig1–fig5.{png,pdf}` | 已嵌入初稿 |
| 数据卡 | `docs/DATA-CARD.md` | 构成、用途边界、偏差、伦理、维护 |
| 复现指南 | `docs/REPRODUCE.md` | 三条命令先跑通 + 分阶段表 + 逐表对照 + 常见失败 |
| 端到端 demo | `scripts/demo.py`（`make demo`） | 单件 20 秒，**不需要 FreeCAD** |
| README | `README.md` | 状态表（M0–M6）、快速开始、目录、文档地图 |

### demo 输出（10 号件，10 个高度层）

```
① 几何摘要     面 27 ｜ 边 51 ｜ 包围盒 80×80×52 mm
③ 工艺计划     7 道工序（面铣/型腔/轮廓，Ø6/Ø10）
④ 白名单       325 行 ｜ 277 段 ｜ 通过
⑤ 仿真         过切 77552.1 mm³ ｜ 平均偏差 12.117 mm  → E_GOUGE
⑥ 编排         INIT→…→REPAIR→…→ACCEPT ｜ 修复 1 次 → 0.000 mm / 0.206 mm → 通过
```

这一段就是论文的微缩版：包围盒计划切坏了，结构化错误指出切坏了，编排层换规划器修好了。

### 论文初稿的诚实边界

初稿里明确写了三处「不主张」/「必须说清」：

1. **20/20 是构造结果**：第 2 档修法几何上不可能过切，信息量在 A 组 0.100 与轮次 −83%。
2. **人工一致性降级**：1.000 等于「只数红色像素」的平凡基线 1.0000，只作 sanity check。
3. **规模**：10 件，M4 的 20 是 10×2 变体；A 路失败是算子语义不匹配，不是「FreeCAD 不行」。

---

## 复现

```bash
make reproduce PY=<装了 pytest/numpy/matplotlib 的解释器>
# = freeze + bench + figures + test
```

产物：`reports/bench.{json,md}`、`reports/figures/*`、`data/DATASET-MANIFEST.json`、
`reports/demo.json`。
