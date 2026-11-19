# 开发计划：LLM 驱动的 CAD/CAM 自动推理智能体框架

> 工作标题：Towards Spatial and Manufacturing Modalities: An LLM-Driven Agent Architecture for Automated CAD/CAM Reasoning
> 版本：v0.2｜日期：2026-11-04｜状态：关键决策已确认
> 决策依据：`docs/DECISIONS.md`（环境实测、模型选型、数据来源、会议选择）
> 定位：面向论文（NeurIPS 2027）的可复现系统 + 可开源原型，而非产品交付

---

## 0. 一句话定义

构建一个以 **MCP 网关为中枢**的智能体框架：把 LLM（Qwen 系列）的能力从文本扩展到**空间模态**（B-Rep 几何/拓扑）与**制造模态**（工艺决策/G-code），在 **FreeCAD（OCC）** 上以封闭工具契约完成"需求 → 建模特征理解 → 工艺规划 → G-code → 仿真验证 → 自纠"的闭环。

**相对现有工作的差异点（也是论文的三个贡献点）**

| # | 贡献 | 对比基线 |
|---|------|----------|
| C1 | **能力类型化的 MCP 工具契约**：每个 CAD/CAM 动作带 JSON Schema + 几何前置/后置条件 + 可回滚语义，使计划可被静态校验，而非让模型自由生成脚本 | CAD-Assistant（ICCV'25）让 VLLM 生成 FreeCAD Python 代码；错误只能在执行后暴露 |
| C2 | **渐进式几何披露（Progressive Geometric Disclosure）**：分层 B-Rep 摘要（零件→特征→面表→单面细节），智能体按需"下钻"取几何，把上下文从 O(N_face) 降到 O(N_feature) | 直接序列化 STEP/点云，或把整棵特征树塞进 prompt |
| C3 | **验证接地（Verification-Grounded）的制造闭环**：G-code 作为可执行产物，经解析→材料去除仿真→偏差/过切/干涉量化→结构化错误码反哺修复，形成有界自纠 | GLLM（BTW'25）以语法检查+Hausdorff 为主，缺少工艺级干涉与过切结构化反馈 |

**契约的可验证性**是 C1 的实证手段：M1 已用 13/13 事务回放与 7 类错误拦截证明「执行前可静态校验、失败零副作用」，M3 将进一步用 G-code 可执行率与过切检测验证制造侧闭环。

---

## 1. 研究问题与假设

| 编号 | 研究问题 | 可检验假设 |
|------|----------|-----------|
| RQ1 | LLM 能否在不直接读几何数值的前提下，通过结构化工具接口完成空间推理？ | H1：工具契约 + 分层摘要，可使特征级问答准确率显著高于纯文本/纯图像基线 |
| RQ2 | 制造决策能否被"验证器"约束到可执行且安全的空间内？ | H2：引入仿真验证闭环后，G-code 可执行率与无过切率显著上升，修复轮次收敛 ≤3 |
| RQ3 | 多智能体分工是否优于单智能体长上下文？ | H3：同等 token 预算下，角色分工 + 黑板状态的成功率更高、步数更少 |

---

## 2. 系统架构

### 2.1 五层结构

```
┌──────────────────────────────────────────────────────────────┐
│ L5 交互层   CLI / Web 面板（任务提交、特征树可视化、仿真回放）    │
├──────────────────────────────────────────────────────────────┤
│ L4 编排层   Planner ─┬─ CAD Modeler ── DFM Critic              │
│                     ├─ Process Planner ─ Toolpath Strategist   │
│                     └─ G-code Synthesizer ─ Verifier ─ Repair   │
│            黑板状态 + 有界修复循环 + 情节记忆                     │
├──────────────────────────────────────────────────────────────┤
│ L3 模态层   空间模态：B-Rep 摘要器 / 特征识别 / 渲染观测          │
│            制造模态：工艺知识库 / 切削参数包络 / 后处理器         │
├──────────────────────────────────────────────────────────────┤
│ L2 网关层   MCP Gateway：工具注册、Schema 校验、契约检查、        │
│            事务与快照回滚、调用日志、限流、审计                   │
├──────────────────────────────────────────────────────────────┤
│ L1 适配层   FreeCAD Adapter（freecadcmd + RPC）
│            （预留）第二 Adapter 接口：验证契约与实现解耦                 │
│            （可选）Mock Adapter：无授权时的几何仿真替身            │
└──────────────────────────────────────────────────────────────┘
```

### 2.2 关键设计决策

**D1｜不让模型写脚本，让模型调工具。**
FreeCAD 侧存在 `execute_code` 类"万能工具"，看似方便，实则破坏可验证性与可复现性（任意代码 = 不可枚举的动作空间）。本项目坚持**封闭动作空间**：建模只暴露 sketch/extrude/revolve/pocket/hole/fillet/chamfer/pattern/boolean 等有限算子；`execute_code` 仅作为调试旁路，不进论文实验路径。

**D2｜契约先行，适配层可替换。**
上层只依赖语义契约（参数名、单位、坐标系、返回值），`cad.model.pocket` 由 FreeCAD 适配层落到 `PartDesign::Pocket`。接口与实现解耦，但不把「换一个内核跑一遍」作为研究问题或验收指标。

**D3｜快照式事务。**
每个"可提交步骤"前存快照（复制 `.FCStd`），失败即回滚。相比逐特征撤销，快照更可靠且便于复现实验。

**D4｜几何 ID 稳定化。**
FreeCAD 的 `SubElementName` 在拓扑变化后漂移（经典 topological naming 问题）。统一采用**几何指纹 ID**：`hash(surface_type + canonical_params + 邻接凸凹签名 + 面积分位)`，会话内维护 `fingerprint ↔ kernel_id` 双向映射表；指纹冲突时加邻域二级哈希。

---

## 3. 接口契约（MCP 工具面）

### 3.1 命名空间

| 命名空间 | 代表工具 | 说明 |
|----------|----------|------|
| `cad.session.*` | `open_part` `snapshot` `rollback` `state_hash` `set_units` | 会话与事务 |
| `cad.query.*` | `part_summary` `feature_list` `face_page` `face_detail` `measure` `section` `render_view` `mass_props` | 只读观测（C2 的落地） |
| `cad.model.*` | `sketch_create` `constrain` `extrude` `revolve` `pocket` `hole` `fillet` `chamfer` `pattern` `boolean` | 建模算子，带前置/后置条件 |
| `cam.setup.*` | `define_stock` `define_wcs` `load_tool_lib` `select_machine` | 工艺准备 |
| `cam.op.*` | `face_mill` `pocket_mill` `contour` `drill` `params_set` | 工序生成（调用内核 CAM 或参数化模板） |
| `cam.verify.*` | `simulate` `compare` `dfm_check` `collision_check` | 验证器 |
| `cam.gcode.*` | `validate` `parse_toolpath` `post_process` `dry_run` | 代码层 |

### 3.2 契约结构（每个工具必备）

```json
{
  "name": "cad.model.pocket",
  "preconditions": ["target_face.is_planar", "target_face.normal ≈ tool_axis", "depth < body.thickness - 0.5mm"],
  "input_schema": {"face_id": "fp_id", "depth_mm": "number[0.1, 200]", "corner_radius_mm": "number>=0"},
  "postconditions": ["new_feature.kind == 'pocket'", "volume_delta ≈ expected ± 2%"],
  "effects": {"mutates": ["feature_tree"], "returns": ["feature_id", "face_delta"]},
  "rollback": "snapshot",
  "cost": {"time_s": 0.8, "tokens_hint": "low"}
}
```

**价值**：Planner 在调用前即可做可行性检查（如"该面不是平面"→ 不发起调用），把"执行后报错"变成"计划期剪枝"。这是 C1 的核心，也是与自由代码生成路线的关键对照实验。

### 3.3 观测接口的分层（C2 落地）

| 层级 | 内容 | 典型 token |
|------|------|-----------|
| L0 | 包围盒、体积、面/边计数、特征直方图、对称性 | ~120 |
| L1 | 特征列表（类型、尺寸、加工方向、开闭） | ~400 |
| L2 | 面表分页（每页 20 面：类型/面积/法向/邻接） | ~600/页 |
| L3 | 单面细节（曲面参数、环、曲率、边序列） | ~300/面 |
| L4 | 渲染视图（等轴测/剖视/局部放大） | 图像 |

智能体默认只见 L0+L1，需要时显式调用下钻工具——把上下文成本从"零件复杂度"解耦出来。

---

## 4. 关键技术路线

### 4.1 空间模态：B-Rep → 可用表示

**管道**：`内核 API 导出` → `规范化` → `特征识别` → `摘要生成` → `按需下钻`

1. **几何导出**：FreeCAD 用 `Part.Shape` + `pythonocc`（面 `Geom_Surface`、边 `Geom_Curve`、`TopExp_Explorer` 遍历）；边凸凹用邻接法向点积判定。
2. **规则层特征识别**：凹边环检测（二面角 + 法向符号）→ 面邻接子图匹配 → 加工特征（型腔 / 槽 / 台阶 / 通孔 / 盲孔 / 沉头 / 圆角 / 倒角）。规则层负责"可解释、零训练、口径稳定"。
3. **学习层增强**：面邻接图 → GNN（GAT/BRepFormer 类）多分类，在 MFCAD++（15 类加工特征）上预训练，再用自建数据微调；目标是**规则+学习融合**：规则给候选与置信度，模型裁决歧义（槽 vs 型腔、台阶 vs 开放型腔）。
4. **LLM 语义标注**：把 L1 摘要 + 规则证据交给 LLM 生成"工程师视角"的特征描述（用途、可能的加工方式、风险点），作为后续工艺规划的语义上下文。

> 论文卖点不在"又一个特征识别模型"，而在**识别结果如何被压缩成 LLM 可消费的表示**。因此特征识别只需达到"够用"（MFCAD++ macro-F1 ≥ 0.88），重点在摘要的信息保真度实验（消融：L1 摘要 vs 原始 STEP 文本 vs 点云）。

### 4.2 制造模态：工艺 → G-code

1. **工艺知识层**：材料库（Al6061 / 45 钢 / Ti6Al4V）× 刀具库（平底/圆鼻/球头，直径 3–20mm）× 参数包络（`vc, fz, ap, ae` 的**上下界**）。LLM 只能在包络内选择并给出理由，**数值越界由网关钳制并记录违规**——安全关键系统的必要设计。
2. **工序映射**：特征 → 工序（型腔→螺旋下刀+摆线清角；孔→中心钻+啄钻；轮廓→顺铣），生成工序 DAG（含装夹/WCS 切换）。
3. **两条 G-code 路径**（对比实验素材）：
   - **A 路（内核 CAM）**：调 **FreeCAD CAM 工作台**生成刀路 → 高保真、可作参考真值（reference toolpath）。
   - **B 路（智能体合成）**：由 LLM 依据工序 DAG 与几何，直接生成受限 G-code 子集（G0/G1/G2/G3/G81/G83/M03/M05/M06/M08/M09/G54/G90/G21），经后处理器规范化。
   - 论文主张：B 路 + 验证闭环可逼近 A 路的可加工性，而成本与可解释性更优。
4. **后处理器**：FANUC 风格子集，固定头部/尾部模板，禁止未登记 G 代码（白名单校验）。

### 4.3 验证器（研究闭环的核心）

| 检查 | 方法 | 输出 |
|------|------|------|
| 语法/语义 | 解析为刀路 IR；模态检查（主轴未开即切削、G0 穿越工件、程序结束后仍有指令） | `E_SYNTAX_*` |
| 过切/残留 | 离散料坯（dexel 或体素，0.5mm 分辨率）沿刀路扫掠减去，与目标网格做内外判定 | `gouge_volume` `residual_volume` `max_deviation` |
| 干涉 | 刀柄包络 vs 工件/夹具包围体；快速移动穿料检查 | `E_COLLISION` |
| DFM | 内圆角半径 vs 刀具半径、深径比、薄壁、可达性（沿 −Z 射线） | `E_DFM_*` |
| 效率 | 切削长度 / 空行程比 / 估算工时 vs 参考 | `air_cut_ratio` |

**结构化错误对象**是自纠能work的前提：

```json
{"code": "E_GOUGE_INTERNAL_CORNER", "face_id": "fp_3a91", "magnitude_mm": 0.8,
 "cause": "tool_radius(4) > corner_radius(3.0)", "suggest": {"tool_radius_max": 2.5}}
```

Repair Agent 只消费这个结构 + 相关面细节，不做全量重规划。

### 4.4 多智能体编排

- **状态机**：`INTENT → SPEC → MODEL → DFM_REVIEW → PROCESS_PLAN → TOOLPATH → GCODE → VERIFY → (REPAIR ≤3) → DONE`
- **黑板**：`spec` / `design_state` / `feature_graph` / `process_plan` / `gcode_state` / `verify_report`，全部可序列化，用于实验复现与论文附录
- **交接协议**：每个角色回传 `{verdict: pass|fail, blocking[], evidence[], artifacts[]}`，不允许自由文本交接
- **记忆**：情节记忆（相似零件 + 曾修复的坑，按技术栈/特征类型召回）+ 失败模式库（`pitfalls.jsonl`，签名去重）

---

## 5. 基准与评测

### 5.1 数据集（v0.2 已锁定来源，详见 `DECISIONS.md` §4）

| 轨道 | 数据来源 | 规模（目标） | 说明 |
|------|----------|--------------|------|
| 锚点集 | RPTU Zenodo 17035762（STEP + .ngc + 实拍图，CC-BY 4.0） | 10 件 | 唯一"真几何+真 G 代码+真机加工"公开集，作外部效度 |
| T1 几何问答 | ABC / DeepCAD 采样 + 自生成 | 300 件 × 5 问 | 特征计数、尺寸、可达性 |
| T2 建模复现 | Fusion360 Gallery + 自建参数化族 | 120 件 | 目标 B-Rep + 特征树 |
| T3 工艺/G-code | **自生成**（FreeCAD Path 真值）+ Mendeley `gtvvwmz7r7` 交叉验证 | 300 件 | 参考刀路、工艺卡；锚点 10 件由专家复核 |

自建数据用**参数化族生成器**（族模板 + 尺寸采样 + 自动标注），保证可扩展与可复现；所有 `(part, prompt, gcode, verify_report)` 四元组入库。

### 5.2 指标

| 维度 | 指标 | 目标（demo 规模） |
|------|------|------------------|
| 理解 | 特征问答准确率 / 特征 macro-F1 | ≥ 0.80 / ≥ 0.88 |
| 建模 | 端到端成功率、Chamfer 距离、体积 IoU | ≥ 75%、≤ 1.5mm、≥ 0.92 |
| 制造 | G-code 可执行率、无过切率、DFM 满足率 | ≥ 95%、≥ 90%、≥ 85% |
| 智能体 | 工具调用合法率、平均步数、修复轮次 | ≥ 98%、≤ 12、≤ 2.1 |
| 成本 | 单件 token / 费用 / 时延 | ≤ 40k tokens、≤ ¥0.5、≤ 3min |

### 5.3 基线与消融

- **基线**：① 直接提示 LLM 生成 FreeCAD 脚本（CAD-Assistant 风格）；② 纯文本描述零件 + LLM 规划（无工具）；③ NumCraft 式多智能体 G-code（无几何验证）；④ GLLM 式参数抽取 + 结构化提示。
- **消融**：−工具契约（改自由脚本）/ −分层摘要（全量 dump）/ −验证闭环 / −DFM Critic / −记忆 / 单智能体 vs 多智能体 / Qwen 不同规模（7B / 32B / Max）。
- **人类研究**：3 名 CNC 工程师对 30 份输出的"可加工性 / 安全性 / 合理性"5 分制盲评，报告 Cohen's κ。

---

## 6. 里程碑与排期（24 周，单人 + AI 协作）

| 里程碑 | 周次 | 交付物 | 验收标准（可机械检查） |
|--------|------|--------|------------------------|
| **M0 环境与基线** ✅ | W1–W2 | 仓库骨架、CI、FreeCAD headless 通路、基线脚本 | `freecadcmd` 可无头建模并导出 STEP；基线能跑通 10 件样例（**已完成，见 `docs/M0-REPORT.md`**） |
| **M1 MCP 网关 + 适配层** | W3–W6 | ✅ **已完成**：`gateway/`（契约/校验/事务/快照）、`adapters/model` + `freecad_model_worker`、9 个工具 | ✅ 实测：建块→挖腔→快照→钻孔→回滚 13/13 通过；7 类错误拦截率 100%，失败调用零副作用（`reports/m1_acceptance.json`） |
| **M2 空间模态** | W7–W10 | B-Rep 摘要器、规则特征识别、GNN 特征分类、MFCAD++ 评测脚本 | 摘要器在 100 件上稳定输出 L0–L2；规则 F1 ≥ 0.75，融合后 ≥ 0.88 |
| **M3 制造模态 + 验证器** | W11–W14 | ✅ **完成** 2026-11-05：见 `M3-FINAL.md` | ① 复现误差按 **ADR-001 双线**（最大过切 ≤0.5mm + 平均绝对偏差 ≤0.5mm）：**B2 10/10 通过**，A 路 2/10；`max`/P99 只报不判；② 人机一致性 **sanity check ≥90%**（原「人工标注一致率」口径已降级）：v1/v2 两轮均 1.000，**等于只数红色像素的平凡基线（1.0000）**，故不作为构念效度证据；构念效度验证需与仿真输出解耦的通道，留作后续工作，见 `M3-HUMAN-QA.md` |
| **M4 多智能体编排** | W15–W17 | ✅ **完成** 2026-11-05：见 `M4-FINAL.md`（状态机 / 黑板 / 6 角色 / 有界修复 / 情节记忆） | T3 子集端到端成功率 ≥ 60%：**实测 20/20 = 1.000**（10 件 × 2 种毛坯余量 = 20 任务，数据集只有 10 件几何）。消融：不修复 2/20 = 0.100；修复+冷记忆 20/20、均轮次 0.90；修复+热记忆 20/20、均轮次 **0.15**；有界性用故障注入验证（`rounds≤3` 必停手） |
| **M5 基准与实验** | W18–W21 | ✅ **完成** 2026-11-05：见 `M5-M6-FINAL.md`（数据集 sha256 冻结 + `make bench` 台账 + 5 张主图） | 所有指标可一键重跑：`make bench` 约 2 分钟重算 M3/M4 并汇总，重算/存档分开报；无随机采样，同一输入黑板指纹逐位一致 |
| **M6 论文与开源** | W22–W24 | ✅ **完成** 2026-11-05：论文初稿 `PAPER-DRAFT.md`、5 张主图 `reports/figures/`、数据卡 `DATA-CARD.md`、复现 `REPRODUCE.md`、`make demo` | 第三人按 README 跑通 demo：`bash scripts/reproduce.sh` 全链路通过（冻结→复算→出图→114 passed），demo 单件 20 秒且不需 FreeCAD |

**并行建议**：M2 的 GNN 训练与 M3 的仿真器可并行（不同人）；M5 前必须冻结接口，禁止再改 Schema。

### 关键依赖与前置条件

| 事项 | 状态 | 影响 | 应对 |
|------|------|------|------|
| FreeCAD 1.0+ | 待安装 | 阻断 M0 | 优先安装（开源、无授权成本） |
| GPU（本地 Qwen 推理） | 待确认 | 影响成本与迭代速度 | 无 GPU 则先用 API，规模对比实验后补 |
| 仿真/切削真机 | 无 | 无法做物理验证 | 明确声明"仅仿真验证"，论文 limitation 中说明 |

---

## 7. 仓库结构

```
CAD2CAM/
├─ docs/                     # 计划、架构、ADR、实验记录
│  ├─ DEVELOPMENT-PLAN.md
│  ├─ decisions/             # ADR-001..N + OPEN-DECISIONS.md
│  └─ paper/                 # 图源（svg/tikz）、表格生成脚本
├─ gateway/                  # MCP 网关：注册、schema、契约、事务、审计
│  ├─ registry/
│  ├─ contracts/             # 每个工具的 pre/post 条件（YAML）
│  └─ server.py
├─ adapters/
│  ├─ freecad/               # headless RPC + pythonocc 几何导出
│  └─ mock/                  # 无授权时的替身（基于 OCC）
├─ modalities/
│  ├─ brep/                  # 摘要器、指纹、图构建
│  ├─ features/              # 规则识别 + GNN 训练/推理
│  └─ cam/                   # 工艺库、工序映射、后处理
├─ agents/                   # 角色、状态机、黑板、记忆
├─ verify/                   # gcode 解析、dexel 仿真、DFM
├─ bench/                    # 数据集构建、评测、消融
├─ tests/                    # 单元 + 集成（含双内核一致性测试）
└─ Makefile                  # setup / dev / bench / paper
```

**技术栈（版本在 M0 冻结）**：Python 3.11（内核侧，跟随 FreeCAD 自带解释器）+ Python 3.12（宿主侧）；MCP Python SDK；Qwen3（API 优先，本地 vLLM 备选）；FreeCAD 1.0 + pythonocc-core；`trimesh` / `numpy` / `scipy` 做几何与仿真；`pytest` + `ruff` + `mypy`；实验记录用 JSONL + 固定 seed。

---

## 8. 风险登记册

| # | 风险 | 概率/影响 | 缓解 |
|---|------|-----------|------|
| R2 | 拓扑命名漂移导致面 ID 失效，工具调用错面 | 高/高 | 几何指纹 + 邻域哈希 + 调用前 `state_hash` 校验；失效即降级为"面表重查"而非盲调 |
| R3 | 大零件 B-Rep 上下文爆炸 | 中/高 | C2 分层披露 + 分页 + 硬预算（超限强制下钻）；在 300 面零件上做压力测试 |
| R4 | dexel 仿真精度不足，过切漏检 | 中/中 | 0.25mm 分辨率 + 关键区域自适应细分；与 FreeCAD Path 的碰撞检查交叉验证 |
| R5 | LLM 生成的工艺参数越界/危险 | 中/高 | 参数包络硬钳制 + 白名单 G 代码 + 永不下发真机 + 人审签字环节 |
| R6 | 评测主观、审稿人质疑"自建数据自评" | 高/中 | 尽量复用公开基准（MFCAD++ / SGP-Bench / ABC）；自建部分公开数据卡与标注协议；加入第三方专家盲评 |
| R7 | 24 周内范围蔓延（装配、五轴、真实后处理） | 高/中 | 第 3 节明确 out-of-scope，变更走 OPEN-DECISIONS 登记 |

---

## 9. 明确不做（MVP 边界）

| 不做 | 原因 | 何时考虑 |
|------|------|----------|
| 装配体级工艺规划 | 组合爆炸，MVP 无收益 | v2 |
| 五轴 / 车铣复合 | 运动学与干涉复杂度陡增 | v2 |
| 真实机床后处理与 DNC 下发 | 安全责任 + 厂商协议 | 永不（论文范围内） |
| 自由曲面高精加工策略优化 | 与核心论点无关 | v2 |
| PMI/MBD 标注解析（AP242） | 依赖商业工具链 | 视审稿意见 |
| 自研特征识别大模型 | 非贡献点，成本高 | 不追 |

---

## 10. 近期两周的具体动作（可直接开工）

1. **D1**：安装 FreeCAD 1.0，验证 `freecadcmd` 无头建模 → 导出 STEP → 读回面/边。
2. **D2**：跑通最小 MCP server（stdio），暴露 `part_summary`、`create_box`、`pocket` 三个工具，用 Qwen 完成一次"建块挖腔"闭环。
3. **D3**：写 `snapshot/rollback` 与 `state_hash`，验证失败回滚。
4. **D4**：把 FreeCAD 的 `execute_code` 关闭，只留封闭算子，测一次"模型想写脚本但被拒"的行为，记录降级路径。
5. **D5–D6**：几何指纹算法原型 + 在 20 个零件上测 ID 稳定性（改尺寸后 ID 是否保持）。
6. **D7**：写 Mock 适配验证「契约与实现解耦」，并记录 ADR；不探测商业内核。
7. **D8–D10**：B-Rep 摘要器 L0/L1 版本，人工评估 30 件的摘要是否"够用"。
8. **D11–D14**：搭 bench 骨架（10 件样例 + 评测脚本），产出第一张基线表。

---

## 11. 待确认事项

> v0.2 已关闭 Q1–Q7，详见 `docs/DECISIONS.md` 第 2 节。剩余 5 项（O1–O5）见该文档第 8 节。

| # | 原问题 | 结论 |
|---|--------|------|
| Q1 | 投稿目标 | NeurIPS 2027 主会，备选 ICCV 2027 / RCIM |
| Q2 | NX 授权 | **不纳入研究范围**（2026-11-05 用户决定）；全栈 FreeCAD/OCC 开源 |
| Q3 | GPU | RTX 4090 24GB，Ollama + GGUF |
| Q4 | 人力 | 2 人（用户 + 助手） |
| Q5 | 控制器 | 不指定，通用 G 代码子集 |
| Q6 | 开源 | Apache-2.0 |

---

## 12. 变更记录

| 日期 | 变更 | 原因 | 影响 |
|------|------|------|------|
| 2026-11-04 | 创建 v0.1 | 用户提出标题与摘要，需落地开发计划 | 全文档 |
| 2026-11-05 | **移除 RQ4/H4 与 T4 跨内核轨道** | NX 试用未获批；用户判定该论点不重要，不写进论文 | 删去研究问题、数据轨道、指标、风险、依赖与适配层中的相关条目；§0/§2/§4 统一为单内核。**M0–M6 交付物与验收标准不变** |
