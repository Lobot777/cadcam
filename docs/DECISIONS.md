# 决策记录（v0.2）

> 日期：2026-11-04｜状态：生效
> 上游文档：`docs/DEVELOPMENT-PLAN.md`（v0.1）

---

## 1. 环境实测结果

| 项 | 实测 | 结论 |
|----|------|------|
| FreeCAD | **1.1.4**（2026-09-28）已装于 `E:\FreeCAD_1.1.4-Windows-x86_64-py311`；旧 0.20.1 保留于 `E:\FREECAD` 作 A/B 对照 | ✅ 升级完成，10/10 件拓扑与指标零偏差 |
| 内置 Python | 3.8.10（旧）→ **3.11.14**（1.1.4） | 主工程仍用 conda，内核侧 3.11 已够新 |
| 已装模块 | Part / Import / Path / PathScripts / Mesh / Draft / PartDesign / Path.Main 全部可导入 | CAM 链路可行 |
| GPU | RTX 4090，24564 MiB，驱动 610.88 | 单卡 24GB 是硬预算 |
| E 盘 | 1.9T，可用 1.2T | 模型 + 数据集 + 快照足够 |
| 宿主 Python | 3.13.14；conda 可用；uv 缺失 | 用 conda 建环境 |
| WSL | **WSL 本身可用**（用户手动可跑）；但 `wsl.exe` 在本机程序黑名单中，**代理侧不可调用** | vLLM 由用户在 WSL 内手动拉起，代理侧只走 OpenAI 兼容 HTTP |

> WSL 这条的准确表述是"代理无法直接调用 WSL"，不是"vLLM 路线不可行"：WSL2 支持 localhost 端口转发，vLLM 在 WSL 内监听 8000 后，Windows 侧客户端可直连。因此 D4 改为**双栈**——vLLM（WSL，用户启动）为主、Ollama（Windows 原生）为备，两者都只需暴露 OpenAI 兼容接口，`LLMProvider` 抽象层无需区分。

---

## 2. 决策表

| 编号 | 决策 | 理由 | 影响 |
|------|------|------|------|
| D1 | ~~投稿目标：**NeurIPS 2027 主会**~~ **（历史，2026-11-09 由 D8 取代）** | 本文内核是"LLM 智能体 + 工具契约 + 可执行验证"，属 agent/tool-use 议题；ICCV 需要视觉新颖性，我们的渲染观测是辅助手段 | 已不适用；门禁评审见 `.workbuddy/memory/2026-11-09.md` |
| D2 | ~~无硬 deadline，24 周排期~~ **（历史，由 D8 取代）** | 原为与 NeurIPS 2027 截稿留缓冲 | 改为按特刊 deadline 倒排，deadline 待确认 |
| D3 | **纯 FreeCAD / OCC 单内核**；不设第二内核实验 | 跨内核一致性论点已由用户决定移除 | 全栈开源可复现，M0–M6 交付物不变 |
| D4 | GPU：RTX 4090 24GB；推理双栈——**vLLM（WSL 内，用户启动）为主，Ollama（Windows 原生）为备** | 代理侧无法调用 `wsl.exe`，但可走 localhost HTTP；Ollama 保证无 WSL 时仍能开工 | 上下文与并发受限，反过来验证 C2 分层披露的必要性 |
| D5 | 人力 2 人（你 + 我） | 可并行：一人主系统、一人主数据/实验 | M2 与 M3 可部分并行 |
| D6 | 不指定控制器，输出通用 G 代码子集 | 避免后处理器绑定，提升通用性 | 白名单指令集 + 标准头部/尾部 |
| D7 | 开源（Apache-2.0） | 与 Qwen、FreeCAD 许可兼容；利于复现评分 | 数据集需逐项核对许可证 |
| **D8** | **改投 *Intelligent Computing*（IF 6.4 / JCR Q1）特刊 "Foundation Models Beyond Text: Towards General Multimodal Intelligence"，Research Article** | 客座编辑已认可 Proposal 1 relevance；2026-11-09 门禁评审结论：ML 主会缺新学习方法与 SOTA 基线且撞车 2025–2026 工作，而该特刊主题（空间/几何/制造模态 + agentic 交互）与本文实证吻合；15 000 词 / 10 图表容量使 8 组 LLM 实验全保留 | 论文主线由「可验证性 + 负结果」重构为「基础模型接入可执行物理模态所需的可判定接口 + 动作空间实证」；标题改为 *A Verification-Grounded Agent Architecture*；§0 收敛 NX / high-end manufacturing 两项未落地选项，highly autonomous 改为**自主性上移**（见 D9）。详见 §7 与论文 §0 |
| **D9** | **Siemens NX 不作承诺；自主性口径由「降级为 bounded」改为「relocated / 权限上移」**（2026-11-09，用户裁定） | 用户明确 NX 只是当时的计划候选，不是交付承诺，无需主动致歉；且 `scripts/m5_autonomy_probe.py` 离线证明：把 LLM 原始 G-code 刀心投影到验证器安全高度后双线仍 0/10、残留 25 503→25 623 mm³，失效主因是**覆盖规划缺失**而非下刀深度 → L0 不该靠「重写」救，应换可判定动作空间 | 论文 §0 表两行改写、新增 §0.2 自主性层级 L0–L4、§5.6 新增离线乐观上界；cover letter 删去 NX 致歉句，改写为 *relocated rather than reduced*；不写 unconstrained autonomy |
| W1 | **NX 不纳入研究范围**，见第 6 节 | 移除不重要的论点、缩短范围 | 论文只主张工具契约与验证闭环，不作跨内核宣称 |
| W2 | 工艺知识库由我方构建，形式为**轻量知识图谱**，见第 5 节 | 结构化规则比纯 RAG 更可审计、可门禁 | 需要你补厂内规则与翻车案例 |
| W3 | 评测在 M5 统一做，支持一键重跑 | 论文可复现要求 | 所有指标落 JSONL |
| W4 | 云 API 与全离线**双栈并行** | 敏感件走本地，对照实验走云 | 统一 `LLMProvider` 抽象 |

---

## 3. 模型选型

### 3.1 对 `ccore/Qwen3.5-4B-3D-Coder` 的判断：不建议用于本项目

已核实该模型页面存在（2026-10-29 发布，Apache-2.0，4B）。但其训练目标是**生成 Three.js 交互式 3D 场景代码**，数据集 `ccore/ThreeJS-3D-Scenes` 是网页 3D 场景，不涉及 B-Rep、加工特征或 G 代码。两个硬伤：

1. **领域不匹配**：Three.js 场景描述 ≠ 工程几何。它不认识平面度、型腔深度、刀具可达性。
2. **规模不足**：4B 在多步工具调用 + 严格 JSON Schema 约束下，长链路成功率明显低于 27B 级。

唯一可能的用途：作为"图纸/渲染图 → 前端可视化"的展示组件，与主链路无关。

### 3.2 建议的三档模型（24GB 预算）

| 角色 | 模型 | 量化后显存 | 用途 |
|------|------|-----------|------|
| 主力（规划 + 工具调用） | Qwen3.6-27B / Qwen3.5-27B | 约 17GB | 6 个智能体角色共用，留 6–7GB 给 KV |
| 快迭代（消融、单测） | Qwen3.5-9B | 约 6.6GB | 跑 100 次实验不心疼，做规模消融的低档位 |
| 视觉观测 | Qwen3-VL-8B | 约 9GB | 渲染图判读（与主力互斥加载） |
| 云端对照 | 百炼 qwen3.5-plus / max | — | 上限参照，证明本地部署的性价比 |

**显存账**：24GB − 17GB 权重 = 7GB KV，27B 在 8K 上下文下够用，32K 需 FP8 KV 或降 batch。这直接印证 C2（渐进式披露）不是设计偏好，而是硬件约束下的必然。

**加载策略**：主力与视觉模型互斥，用 `ollama` 的按需加载 + 端口切换；评测脚本里显式记录模型版本与量化方式。

### 3.3 部署方式

- 运行时：Ollama（Windows 原生），OpenAI 兼容端点 `http://localhost:11434/v1`
- 模型目录重定向到 `E:\models\ollama`（避免 C 盘膨胀）
- 关键：Qwen3.5 系列需 Ollama 0.17.4+（Gated DeltaNet 架构），版本不足会加载失败
- 双栈：`LLMProvider` 抽象两个实现 —— `LocalOllama` / `CloudDashScope`，配置切换，代码零改动

---

## 4. 数据来源（已核实）

### 4.1 主基准：RPTU 三轴铣削基准集（强烈推荐）

- 链接：https://zenodo.org/records/17035762
- 内容：**10 个零件**，每个含 `.step` + `.ngc`（真实 CAM 输出 G 代码）+ `.png`（实拍/渲染）
- 规模：总计 5.3MB；STEP 14–146KB；G 代码 6.9KB–1.8MB
- 工艺前提：毛坯 80×80×50mm，**单把 ≤Ø6 立铣刀**，**一次装夹**完成
- 特征覆盖：槽、孔、矩形/圆形内外轮廓、型腔、负自由曲面，复杂度 1→10 递增
- 许可：**CC-BY 4.0**（可商用、可改、可再分发，只需署名）
- 出处：Schmitz, Mertes, Schillinger, Wagner (2025)，基于 ISO 10791-7 的 Mertes 2024 基准件扩展

价值：这是我们能拿到的**唯一同时具备"真几何 + 真 G 代码 + 真机加工记录"且许可干净**的公开数据。缺点也明确——只有 10 件、零件小、特征有限。

### 4.2 补充数据集

| 来源 | 内容 | 许可 | 用途 |
|------|------|------|------|
| Mendeley `gtvvwmz7r7` | 4 个零件，`.stp` + `.mpf` + 刀具清单 + 能耗数据 | CC BY-NC 4.0 | 刀具/参数交叉验证（非商用，论文可用） |
| FabriCAD（CIMTT-Kiel） | 1k–100k 合成件，STEP + 多步工艺计划（铣/钻/磨/焊） | 需申请 | 工艺序列先验，规模大 |
| MFCAD++ | 加工特征标注（15 类） | 学术 | 特征识别训练/评测 |
| ABC / DeepCAD | 大规模 B-Rep/参数序列 | 学术 | 几何理解的预训练与压力测试 |

### 4.3 数据策略：锚点 + 规模 + 标签

```
外部锚点（10 件，真值可信）  →  验证"系统说的是人话吗"
自生成规模（参数化族 × FreeCAD Path，200–500 件）→ 验证统计显著性
特征标签（MFCAD++）→ 验证识别模块本身
```

自生成不是凑数：FreeCAD Path 走的是真实 CAM 引擎，产出的 G 代码与几何严格对应，且**许可完全干净**，可以随论文开源。Zenodo 那 10 件则作为"外部效度"证据——证明我们的自生成集没有系统性偏差。

---

## 5. 工艺知识图谱设计（W2 落地）

### 5.1 本体（8 类节点）

| 节点 | 关键属性 | 示例 |
|------|----------|------|
| `Material` | 牌号、族、硬度、可加工性 | Al6061-T6, HB95 |
| `Tool` | 类型、直径、刃数、涂层、伸出长 | 平底立铣 Ø6 / 3 刃 / 涂层 |
| `Machine` | 轴数、转速范围、控制器 | 3 轴 / 0–12000 rpm |
| `Feature` | 类型、尺寸、公差、可达方向 | 型腔 60×40×12 |
| `Operation` | 类型、策略、余量 | 型腔粗铣（螺旋下刀） |
| `Parameter` | vc / fz / ap / ae / n / F | vc=120 m/min |
| `Rule` | 表达式、严重度、来源 | 内圆角 ≥ 0.5×刀具半径 |
| `Defect` | 症状、根因、对策 | 深腔让刀→分多次降深 |

### 5.2 关系（8 类）

`Feature -[machined_by]-> Operation`、`Operation -[requires]-> Tool`、`Tool -[applicable_on]-> Material`、`Parameter -[bounded_by]-> Rule`、`Rule -[derived_from]-> Defect`、`Operation -[precedes]-> Operation`、`Feature -[constrains]-> Tool`、`Material -[grounded_on]-> Machine`

### 5.3 存储与查询

- **SQLite 属性图**（`nodes` / `edges` 两表 + JSON 属性），不是 Neo4j：零运维、可随论文附带、CI 里能跑
- 查询：递归 CTE 做多跳；导出 JSON-LD / Cypher 供审稿人导入 Neo4j 复核
- 与 LLM 的接口：**不给 LLM 自由 Cypher**，只暴露 3 个只读工具 `kg.lookup(feature, material)`、`kg.params(tool, material, feature)`、`kg.rules(feature, severity)` —— 回到 C1 的契约原则

### 5.4 种子数据来源

1. 公开手册级参数（Sandvik / Kennametal 公开推荐值、ISO 513 刀具分类）——引用而非整表复制
2. FabriCAD 的工艺序列先验（马尔可夫链统计）
3. **你提供的厂内规则与翻车案例**（最有价值，也是审稿人看不到的部分）

### 5.5 验收指标

用 30 条 held-out 工艺问答（"Ø6 三刃铣铝，型腔 12 深，ap 取多少"）测图谱作答覆盖率，目标 ≥ 80%，并记录答错类型。

---

## 6. 第二内核：不纳入研究范围（2026-11-05 定）

NX 学生试用未获批，本机无 NX 主程序（仅 `PLMLicenseServer`）。
**用户决定：不把「跨内核 / OCC vs Parasolid 一致性」作为研究问题、数据轨道、
指标、风险或论文内容，也不留作待办。** 全栈 FreeCAD / OCC，开源可复现。

- `DEVELOPMENT-PLAN.md` 已同步移除 RQ4 / H4、T4 数据轨道、「FreeCAD vs NX 结论一致率」
  指标、风险 R1、NX 依赖行、NX Adapter 与 `adapters/nx/` 目录。
- M0–M6 的交付物与验收标准**不变**：该论点原本就不在关键路径上。
- M3 的「A 路参考刀路」由 **FreeCAD CAM 工作台**（1.1 起 Path 更名，LGPL）承担，
  配 CAMotics 做 G-code 仿真交叉验证；项目明确不做五轴，开源 CAM 的短板落在范围外。
- 历史讨论与备选方案（含免费闭源第二内核的对比）已移除，此处只保留结论供追溯。
- **2026-11-06 补充（第三方 CAM 对照工具）**：原 `M6-EXTERNAL.md` §6 的「Fusion 360
  人工导出 `.nc`」已取消。用户要求找免费、非 GUI、可脚本运行的替代，定为
  **OpenCAMLib 2023.1.11（LGPL-2.1，`pip install opencamlib`）**：
  独立 CAM 内核做目标几何确认、逐刀心点过切判据、自生成刀路，外加分辨率收敛检查。
  这与 §6 的「不做跨内核一致性研究」不冲突——OCL 是**验证工具**，不是第二个 CAD 建模内核，
  几何仍全部来自 FreeCAD/OCC。完整结果见 `docs/M6-OPENCAMLIB.md`，
  论文里写「与两个独立开源内核交叉验证」，**不写与商用 CAM 对比**。
  该交叉验证同时查出离散膨胀在圆弧边界少盖半格的盲点（已量化披露为 limitation，B21）。
- **2026-11-07 补充（三条口径补充）**：
  ① **几何链路**：新增 `STEP → FreeCAD Mesh → 二进制 STL → OCL STLReader`
     （`scripts/m6_step_stl_xcheck.py`），绕开 geom JSON 缓存复核输入网格，10 件 bbox 全一致、
     两条 OCL 曲面逐柱一致 ≥99.984%。**B-rep 内核仍是 OCC，不得称"第二 CAD 内核"**；
     论文措辞固定为「独立几何链路复核了输入网格」。
  ② **统计口径**：所有 `k/n` 通过率一律只写分数、不写百分比，附 Wilson 95% 区间
     （`src/cadcam/verify/stats.py`、`reports/bench.json::wilson_95ci`）；
     10/10 下界 0.7225、130/130 下界 0.9713。理由：Wald 在 p=0/1 处退化。
  ③ **外部集交叉验证**：OCL 第三方判据扩到 130 件 MFCAD 集，发现稀疏大三角/斜边台阶上的
     第二类掩码失效（深过切），**主动披露为 limitation**，不得把 130/130 写成
     「连续几何上零过切」。详见 `docs/M6-OCL-EXTERNAL.md`。

## 7. 投稿目标说明（D8，取代 D1/D2）

**Intelligent Computing 特刊 "Foundation Models Beyond Text: Towards General Multimodal Intelligence"，Research Article。**

期刊核验（2026-11-09，spj.science.org）：之江实验室 + AAAS Science Partner Journal，OA；
2025 JIF **6.4**、JCR Q1（CS:AI / Theory&Methods / Interdisciplinary Applications），
CiteScore 9.0；ESCI / EI / Scopus / DOAJ 收录。Research Article ≤15 000 words、
≤10 图表、≤60 参考文献、Abstract ≤250 words。

**为什么改**：门禁评审显示按现稿投 NeurIPS/ICLR 主会不够——缺新学习范式、缺同期 SOTA
定量基线、且 2025–2026 已有 CAD-Coder (NeurIPS 2025)、Plan then Act / Graph-CAD
(ICLR 2026) 高度撞车。该特刊由客座编辑认可 Proposal 1，主题正是空间/几何/制造模态与
agentic 交互；篇幅上限反而允许保留全部 8 组 LLM 实验与 6 张图。

**必须同步做的事**（否则提案与成稿不符）：
1. 主线重构：不是「基础模型不行，所以用规则系统」，而是「基础模型接入可执行物理模态
   所需的可判定接口 + 动作空间决定可靠性的 15 模型实证」。
2. 逐篇区分撞车文献（论文 §2 已重写，§2.6 列三点区别）。
3. 摘要「零过切」加离散 Z-dexel 口径限定，主动披露 130 件 3.275%（论文 §0.1 / 摘要已做）。
4. 提案中未落地的一项不作承诺：high-end manufacturing（改为 simulation-verified testbed）。
   **NX 直接不选**（用户 2026-11-09 裁定）：投稿材料中不出现、不解释、不致歉；
   **highly autonomous 不降级，改为自主性上移**（D9 / 论文 §0.2）：L1–L4 模型端到端自主，
   仅关闭 L0/L0′ 两个不可判定动作空间，并用离线上界说明为何「反馈 + 重写」不是出路；
   Qwen2.5 升级为 Qwen3 + 10 云端共 15 模型。论文 §0 有逐条对齐表与给编辑的英文说明。

**仍待外部条件**：当前 APC、两个 proposal 的关系（防重复发表）。
**已确认（2026-11-09）**：deadline **2027-06-30**；CFP 唯一明文要求是
「在 cover letter 中声明投给该特刊」（已写入 `latex/cover-letter.tex` 首段），
系统若有 Special Issue 选项一并勾选。"found relevant" 仅为提案相关性认可，≠ 接收。

---

### D10（2026-11-12）SolidCraft 只作文献引用，不进实验链

用户提供 `https://github.com/tangency-labs/solidcraft`，问能否"应用上"、对论文有无帮助。
核查结论：纯 Rust 开源参数化 3D CAD（MIT OR Apache-2.0，v0.40.1，2026-11-12 发布，
约 13.3k stars / 8,409 commits），有 headless CLI、22 个 MCP 工具、STEP/IGES 读写、
STL 导出与 `measure`（体积/面积/质心/包围盒）。

**有帮助的部分（已落地，纯文字、零实验、零数字改动）**：它是 2026 年最新一批
"让 agent 通过 MCP 驱动 CAD 内核"的系统，与本文 `cad.*` 契约同属一类但边界完全不同——
它检查的是几何量，不产刀路、不产 G-code、不做去除体积或过切判据。因此在
`sections/01-introduction.tex` 相关工作段加入一句定位对照，并在
`sections/04-conclusion.tex` limitations 段主动说明"考虑过第二内核但未采用及其三条理由"，
`refs.bib` 增 `SolidCraft2026`（软件引用）。这样审稿人问"为什么不用第二个 CAD 内核"
时稿子里已有答案，不用临时补实验。

**不做的部分（与 §6 / D8 一脉相承）**：① 本机无 `solidcraft-cli`、无 cargo，
Windows 便携包 160 MB 且 MSI 未签名；② 该项目 2026-10-07 才建仓，v0.40.x 每日发版，
"early but usable for simple parts"；README 写参考件 75/76、`docs/oracle.md` 写 90/90，
文档口径仍在漂移；其自身 oracle 90 例中 21 例期望值由 **FreeCAD 1.1.4** 计算，
不具备相对 OCC 的独立性；③ 它没有制造侧验收能力，
接进来最多补一条几何链路，反而会把 §6 已关闭的"跨内核一致性"重新打开；
④ 英文稿 6 图 + 4 表已用满期刊 ≤10 上限，加 experiment 必然挤掉现有图表，
且要重跑 reproduce / figures / DATA-CARD，收益远低于风险。

**处置**：`latex/README.md` 引用数 19 → 20 已同步；`bash build.sh` 重编通过，
21 页、摘要 236 词、6 图 4 表、零 undefined citation、零 LaTeX Warning。
`main.pdf` / `cover-letter.pdf` 为最新。不下载、不安装、不启动任何服务、不调用任何模型。

---

### D11（2026-11-12）审稿弱点清单：全部不补实验

用户提供 8 条弱点清单（无真机 / 只有 10 件 / 130 件偏合成 / 3.275% 过切 /
重复采样 / 20/20 依赖修复 / 工业场景 / 离散零过切），并点名最后一条最值得警惕。

**裁决**：6 条现稿已主动披露、1 条被读反、1 条属已结案范围，**不补任何实验**。
清单按制造类期刊标准评级，目标特刊是基础模型 / trustworthy / evaluation protocols，
整体偏高。逐条证据指针、答辩口径、证据链顺序与触发条件写入
`docs/REVIEW-READINESS.md`（本轮新建）。

两处纠正：
- 「3.275% 刀具穿越」是术语误读。实为第三方 OpenCAMLib 在 9,660,095 个采样刀心点
  打出 316,398 个**过切标记**，是论文自审计的产出，摘要 / 引言 / 结果 / 结论 /
  cover letter 五处主动写明。属于卖点而非缺陷。
- 「离散零过切」全文 6 处限定，且盲区有机理、分辨率收敛 1.332→0.432→0.143%、
  半格余量对照与代价量化（残留 ×4.42、107/130）。真正风险不是"承认了"，
  而是标题 `Verification-Grounded` 与末页 limitation 之间的 **claim 层级错配**。

**唯一稿件改动**（纯文字、零数字改动）：`sections/04-conclusion.tex` 结论段补三句，
把离散恒等式定性为 construction 而非几何发现，明确承载论文的测量是
动作空间 0/10→20/20 与判据边界及其代价，两者都不依赖连续几何零过切。
摘要与 cover letter 保持原样（它们本就把负结果写在正结果旁边，动反而破坏诚实结构）。

`bash build.sh` 重编通过：21 页、摘要 236 词、20 篇、6 图 4 表、
零 undefined citation / 零 LaTeX Warning / 零 Overfull，禁用词扫描干净。
待办仍只剩作者信息、APC、双 proposal 关系、术语拍板（见 `latex/README.md`）。

---

### D12（2026-11-13）修订轮：容差缺陷、布尔精化与部分审计

用户授权按 `docs/REVISION-HANDOFF-2026-11-13.md` 继续修订，覆盖旧「实验冻结」判断。

**改动**
1. `audit_workorder_reference.normalized()` 对容差字段跳过 8 位小数舍入；此前
   `volume_relative_tolerance=1e-9` 被写成 `0.0`，人为收紧所有体积检查。
2. `_workorder_reference_worker.compare()` 改为 General Fuse 单元分片优先，逐单元归一朝向，
   并强制总体积/source-volume/provenance 校验；`measure()` 对 fuse 与 common 做
   `removeSplitter()` 精化，并显式诊断「共面外壳导致 stock.cut 返回整块毛坯」。
3. 独立参考审计状态如实写入 Methods/Results/Conclusion：contract latent 完整（0.75264476，
   9/20 ≥0.9）、visible 条件均值 0.787095（19 题）、script 仅报 bounds
   [0.17877054,0.22877054] / [0.18112994,0.28112994]；NL03 与 NL07(visible) 未解。
4. 六图英文化并修正：fig5 显式 family 顺序、删除 0.60 线；fig2 node-only 用 test
   (0.6969/0.4462)，决策树退出图；fig4A 哑铃图；fig6 逐件最坏深度 + symlog 零值。
5. 正文修正：M2 test 数字、CAMotics「remaining volume」、删除 direct G-code 对 Table 3
   的误引、去掉无条件 safe/correct 措辞、`hyperref[hidelinks]`。

**验证**：211 passed；23 页、摘要 219 词、21 篇、6 图 4 表；零 warning；PDF 无中文、
无禁用词；关键图页逐页目视核对。外部集新增逐件 Wilson 区间、配对 McNemar、阈值敏感性与覆盖率分层。

**未完成**：作者信息/ORCID/基金/AI 版本/人工复核声明；APC 与双 proposal 关系；
hard 30 无独立重建；NL03/NL07 仍为 null。**不得用放宽容差或补造均值消除。**

---

### D13（2026-11-19）Genesis World + SolidCraft 能力审计，不建训练线

用户提出用 Genesis World（机器人/具身 AI 多物理仿真）结合 SolidCraft 搭建训练并写入论文。

**裁决**：不做训练。Genesis 的状态里没有「去除体积 / 过切深度」，无法定义强化学习的 reward；
SolidCraft 是参数化建模器，没有刀具路径。二者都不产生本文研究的对象。用户的既定口径
「真实铣削没意义」与「图表 6+4 已用满」也排除新增物理仿真实验线。

**落地**：
1. 新增可执行判定程序 `scripts/physical_sim_feasibility.py` 与报告
   `reports/physical_sim_feasibility.json`：以六项必要能力（B-rep 导入、刀具扫掠、
   去除体积、过切判据、操作归因、确定性复现）为契约，逐项记录候选平台的状态与证据来源
   （documentation / local_report），结论为 Genesis 与 SolidCraft 均不合格、现有 dexel-OCL-CAMotics
   链合格。`--check` 模式可锁死判定，6 个单元测试。
2. 论文 Introduction 相关工作段补 Genesis World 定位与边界，Conclusion limitation 段补
   「无 reward 信号故未训练策略」；新增 `Genesis2024` 引用（按官方 README 的软件条目，
   不抄搜索结果中与 arXiv ID 不符的引用）。图表数量不变。

**验证（回退后）**：217 passed；23 页、219 词摘要、21 篇文献、6 图 4 表；零 warning、零 CJK、禁用词 CLEAN。

**2026-11-19 补记**：用户判定 Genesis / SolidCraft 对论文无实质帮助，要求不硬补。Genesis 相关的
Introduction / Conclusion 文字与 `Genesis2024` 引用已从投稿包移除，文献回到 21 篇。内部判定程序
`scripts/physical_sim_feasibility.py` 保留在工程内、不进论文。

---

## 8. 遗留问题

| # | 问题 | 需要谁定 | 状态 / 结论 |
|---|------|----------|--------------|
| ~~O1~~ | ~~FreeCAD 是否升级到 1.1.4~~ | — | **已关闭 2026-11-05**：1.1.4 已安装并作为主内核（`polarity_worker.py` / `geom_worker.py` 均在其上跑通） |
| O2 | 是否接受 Zenodo 10 件作为"锚点"而非主基准 | 你 | 待定 |
| O3 | 知识图谱是否只覆盖 3 轴铣（型腔/槽/孔/轮廓/台阶），还是加钻孔循环与攻丝 | 你 | 待定 |
| O4 | 推理栈改 Ollama（本机 WSL 被拦），是否接受约 20–30% 吞吐折损 | 你 | 待定 |
| ~~O5~~ | ~~NX 学生版可用性~~ | — | **已关闭 2026-11-05**：试用未获批；用户决定完全移除该论点，见 §6 |
| ~~O6~~ | ~~M3 复现误差验收口径~~ | 你 | **已关闭 2026-11-05**：用户确认「按 C 改」，`DEVELOPMENT-PLAN.md` §6 与两个评估脚本已改为 ADR-001 双线；B2 10/10、A 2/10。见 `docs/decisions/ADR-001-reproduction-criterion.md` |

## 未决项（标注侧，2026-11-04）

| ID | 内容 | 谁 | 阻塞什么 | 怎么关 |
|---|---|---|---|---|
| ~~A1~~ | ~~件 3 的 F14 属腔外壁还是岛侧壁~~ | 我 | **已关闭 2026-11-04** | 四壁法向全朝外 + 顶面 4585.8 = 4900 − 4×78.5 + 肩面 1500 = 6400 − 4900 → 70×70 凸台；件 3 已改判（pocket → boss + step） |
| ~~A2~~ | ~~真人 κ~~ | 4 位受试者 | **已关闭 2026-11-05** | 4 人×2 轮×186 题实测：T0 κ=0.556 [0.499,0.608] → T1 κ=0.766 [0.715,0.813]，两轮 CI 不重叠；规则基线一致率 0.543、人-规则 κ 0.263–0.432，落在人类一致性带外。全量结论 `docs/STUDY-RESULTS.md` |
