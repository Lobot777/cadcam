# 还需要你做的事

> **2026-11-13 修订覆盖**：本文件的「实验已冻结」判断已失效，当前入口是
> `docs/REVISION-HANDOFF-2026-11-13.md`。论文已改为审计叙事，作者侧待办仍在，
> 但不要在旧数字与旧结论上继续施工。

> 更新于 2026-11-07 10:00。**M0–M6 全部完成；LLM 侧共 8 组实验（本地 5 + 云端 10 个模型、三套任务集）**：
> **177 passed**，`bash scripts/reproduce.sh` 全链路通过，论文初稿 / 数据卡 / 复现指南 / 6 张主图都在仓库里。
> **本轮补充（2026-11-07）**：OCL 第三方判据扩到 130 件外部集（图 6）、STEP→STL 独立几何链路、
> 点刀半径敏感性、Wilson 95% CI；见 `docs/M6-OCL-EXTERNAL.md` 与 `docs/M6-OPENCAMLIB.md` §3.5。
> **外部验证已完成：MFCAD 130 件全通过（双线），CAMotics 体积差 1.15%，
> OpenCAMLib 逐点几何交叉验证（含分辨率收敛）已完成。Fusion 360 待办已关闭，你不用做任何导出。**
> **改投决定（2026-11-09，D8）**：目标定为 *Intelligent Computing*（IF 6.4 / JCR Q1）特刊
> "Foundation Models Beyond Text"。论文已升 **v0.3**：§0 提案对齐表、§0.1 英文 Title + Abstract、
> §0.2 自主性层级（**权限上移而非降级**），重写摘要 / §1 / §2（逐篇区分 6 篇 2025–2026 撞车文献）。
> **2026-11-09 晚：英文投稿包已生成于 `latex/`**（`main.pdf` 21 页 + `cover-letter.pdf` 2 页），
> 按期刊作者要求与官方 LaTeX 模板排版：四节结构、Title 77 字符、Abstract 236 词、
> 6 图 4 表、20 篇参考文献、AI 使用与特刊归属声明、编译零警告。
> **用户裁定：NX 直接不选，投稿材料中一律不出现、不解释、不致歉。**
> **实验数据一字未动**；deadline 已确认 2027-06-30。
> 下面按「只有你能做」→「审稿人会问」→「我可以继续做」三档列，**A 档不做就投不出去**。

---

## A 必做（作者身份与合规，我替不了）

| # | 事项 | 说明 |
|---|---|---|
| A1 | 论文元信息 | 作者、单位、通讯邮箱、致谢、基金号、作者贡献声明。`latex/main.tex` 与 `latex/sections/05-backmatter.tex` 的 `[...]` 占位需替换；清单见 `latex/README.md` |
| A2 | ~~定目标与格式~~ **已定（D8）** | **Intelligent Computing 特刊 "Foundation Models Beyond Text"，Research Article，deadline 2027-06-30**。仍待你确认：当前 APC、两个 proposal 的关系（防重复发表）。CFP 唯一明文要求「cover letter 声明特刊归属」已落实。见 `docs/DECISIONS.md` §7 |
| A3 | **英文稿通读定稿** | 英文全文已完成于 `latex/`（`main.pdf` 21 页、`cover-letter.pdf` 2 页），按官方 LaTeX 模板与作者要求排版、编译零警告。**仍需你通读拍板**：术语（region-masked toolpath / dual-line acceptance / verification-grounded）与文风 |
| A4 | 开源三件套 | ① `git init` + 首次提交（**仓库至今不是 git 仓库**）；② `LICENSE`（代码 Apache-2.0、数据 CC-BY 4.0，`pyproject.toml` 已声明前者）；③ 仓库地址或 Zenodo DOI（同时填入 A1 / A5 的占位） |
| A5 | 数据可用性声明 | 已写入 `latex/sections/05-backmatter.tex`：外部件集 CC-BY 4.0 可再分发；**MFCAD++ 与 MFCAD 需第三方下载，不随仓库提供**。仓库 URL 与 Zenodo DOI 占位待填；cover letter 还差作者 ORCID / 通讯 / 推荐审稿人 |

> A4 我可以代做（`git init` + `.gitignore` + LICENSE + 首提交），但**推哪个远端、用哪个账号**得你定。

---

## B 审稿人一定会问（研究内容，建议补）

| # | 问题 | 现状 | 建议 |
|---|---|---|---|
| B1 | ~~只有 10 件~~ **已补 130 件外部集** | MFCAD（MIT）筛出 130 件、缩放到 80mm，双线判据 **130/130 通过**、最坏过切 0.000、最坏均偏差 0.306 | 见 `docs/M6-EXTERNAL.md`。定位可写"10 件锚点 + 130 件外部验证" |
| B2 | **凭什么叫"智能体"？** | 四个实验做完：编排/闭环/记忆支持，生成/权衡/读错误不支持（见 `docs/AGENT-CASE.md`） | 建议改成"**Verification-Grounded Agent Harness**"（仍是 agent，但立足点在编排与验证）；若要更强的 agent 故事，做 B9 |
| B3 | ~~和商用 CAM 比呢~~ **已做，且不用 Fusion 360** | 免费非 GUI 的 **OpenCAMLib 2023.1.11**（LGPL-2.1）替代：目标几何 256 000 柱一致 ≥99.828%、B2/B3 过切率 0.76%/10.59%、OCL 自生成刀路双线 9/10。另**查出离散膨胀在圆弧边界少盖半格**（分辨率收敛 1.332→0.432→0.143%） | 见 `docs/M6-OPENCAMLIB.md`。论文写「与两个独立开源内核交叉验证」，**不要写与 Fusion 360 对比** |
| B4 | 真机切过吗？ | **不需要**：生成的是 G-code，用第三方仿真器（CAMotics）交叉验证即可，真实铣削对本研究没有增量信息 | 已做 CAMotics 交叉验证（1.15%）；论文里写明"仿真验证，未做真机"，并给出交叉验证证据 |
| B5 | ~~统计效力：只报点估计~~ **已做：Wilson 95% CI** | `src/cadcam/verify/stats.py` + `bench.py` 从报告现算，`reports/bench.json::wilson_95ci`；10/10 → [0.7225, 1.0000]、130/130 → [0.9713, 1.0000]、9/10 → [0.5958, 0.9821] | 论文 §5.4 已加表并定口径：**只写 k/n，不写百分比**。外部 130 件的主要统计价值就是把下界从 0.7225 抬到 0.9713 |
| B6 | ~~相关工作够新吗~~ **已做（v0.2 §2）** | §2 重写为 6 小节：几何学习 / **基础模型 CAD 生成（逐表区分 CAD-Coder NeurIPS 2025、PTA & Graph-CAD ICLR 2026）** / LLM-G-code（Šket 2025、Eniş 2026、Pour 2025）/ CAM 与仿真 / agent 与 MCP / **§2.6 三点区别**。文献信息已联网核实（arXiv、ICLR virtual、APEM DOI） | 剩余仅「按期刊模板统一参考文献格式 + ≤60 篇终检」，可代做。门禁裁决留档在 `.workbuddy/memory/2026-11-09.md` |
| B7 | ~~LLM 直接生成 G-code 的对照~~ **已做** | G-code 直写 **0/10**（8/10 语法合法、几何全错）；建模脚本 3/5 能跑、**1/5** 体积达标 | 见 `docs/M5-LLM.md` §5.3。这条现在是最有力的"为什么需要验证器"证据，别删 |
| B9 | ~~自然语言工单 → 契约调用~~ **已做，且是正面结果** | 20 条：执行 20/20、合法率 1.000、特征去除体积 IoU **0.985**；8 条难工单 8/8、0.768 | 见 `docs/M5-LLM.md` §5.4。agent 叙事可以立在这个结果上 |
| B10 | ~~NL 任务接上多轮修复~~ **已做** | 10 条难工单：9/10 在 ≤2 轮解决；结构化反馈**又一次**没有行为收益；语义误读（R1）三轮修不动，给坐标 3/3 立改 | 见 `docs/M5-LLM.md` §5.5 |
| B11 | ~~扩难工单到 30 条 + 重复 + 多模型~~ **已做** | 30 条 × 两轮（契约 0.789/0.769，逐任务一致）；5 个开源模型对照，**14B 让结构化反馈从 0/4 变 4/4** | 见 `docs/M5-MODELS.md` |
| B12 | ~~接云端大模型做对照~~ **已做**（10 个云端模型，账号积分） | 云端最好 **0.987**（本地最好 0.822）；**6/10 云端模型剔除歧义题后 IoU = 1.0000** → 任务集对云端已饱和 | 见 `docs/M5-CLOUD.md` |
| B13 | ~~H06 题目欠约束~~ **已改并补跑** | 改成"下边缘留 10、中间留 40"后：**云端 10/10 全对、本地仍全错** → 干净地把"题目问题"与"模型读不懂"分开了 | 见 `docs/M5-CLOUD.md` §4.2；补跑脚本 `scripts/m5_patch_orders.py` |
| B14 | ~~30 条对云端饱和，要更难~~ **已做 20 条** | 云—本差距 0.158 → **0.239**；云端满分从 6 个压到 3 个；云端残余错误集中在"多约束联合定位"（E06/E14） | 见 `docs/M5-HARD2.md`。**仍未完全分开旗舰** |
| B15 | ~~继续顶天花板~~ **已做：换成"澄清判据"** | 5 条欠约束工单 + `clarify` 出口：云端命中 **3.1/4**、本地 **0.8/4**（两个 0/4）。**分数与谨慎不相关**（mimo IoU 0.973 但 1/4；gemma3 0.412 但 2/4） | 见 `docs/M5-AMBIGUITY.md` |
| B17 | ~~追问质量需人工评分~~ **已做** | 0–2 分 × 4 题：云端均值 **6.8/8**（6 个并列 8/8）、本地 **0.8/8**；人工 vs 规则 57/60 一致，规则偏保守、读不出反问语气 | 见 `docs/M5-AMBIGUITY.md` §4.5 |
| B19 | **判据升级三次都分不开云端旗舰** | IoU → 精确命中 → 追问质量，第一集团始终"多个并列"；说明这套任务已测不出 8–15B 云端的差异 | 要分开只能换任务形态（多工序依赖、薄壁、倒扣），不是换判据 |
| B18 | ~~扩工具集到非轴向特征~~ **已做：斜底槽** | 新增 `cad.pocket_ramp`；探针证明 Z-dexel 支持斜面（无需 3D dexel）。**9/10 云端与 3/5 本地精确 6/6** → 加几何复杂度没加难度 | 见 `docs/M5-RAMP.md` |
| B20 | 真·非轴向（倒扣/侧向孔） | 斜底槽仍是单值高度场；倒扣需要 3D dexel 或多方向 dexel | 架构工作，3–5 天，不建议现在做 |
| B21 | **连续几何零过切 vs 双线精度线不能兼得** | OCL 查出离散膨胀在圆弧边界少盖半格（B2 0.76% 刀心点）；加 `guard_mm=res/2` 可把过切点打到 41，但残留 4 606→15 886 mm³、双线 10/10→9/10 | 已在论文 §5.3b 主动披露为 limitation。真解法是连续几何偏置或更细栅格，属架构工作；**不要把 guard 默认值改掉**，会推翻 M3 全部数字 |
| B22 | **稀疏网格上的第二类盲区（130 件外部集新发现）** | OCL 判 B2 过切 **3.275%**（316 398/9 660 095，锚点集 0.76%）、最深 **71.104 mm**；24.5% 深于 0.5 mm。43.5% 落在网格未覆盖柱（验证器假阴性）、56.5% 落在覆盖 100% 件的斜边边界（柱心膨胀失效）。`guard=0.25` 后过切降 98.4%，但双线 **107/130**、残留 **4.42×** | **已在论文 §5.3b / §7 主动披露**。「零过切」必须限定为「Z-dexel 单值高度图 + 同一套离散圆盘」口径内，不得写连续几何零过切。71 mm 几何差另有解释：Ø0.02 点刀擦竖直壁的探针伪影，缩到 Ø0.002 后超差柱 199→2（`reports/m6_ocl_point_cutter.json`）。见 `docs/M6-OCL-EXTERNAL.md` |
| B16 | **云端单次不可复现** | 同一题（E14）space-bunny 两次分别 0.60 / 1.00，`temperature=0` 也不保证 | 关键结论应跑 3 次报均值±区间；我估半天 |
| B8 | 结构化错误对 LLM 的价值 | 本题集三组行为**完全相同**，只有 token 差异 | 要证明它有用，得构造"局部补偿 vs 整体重规划"这种必须读数值才能分辨的题；否则论文里只能写"对程序有用，对 LLM 无证据" |

---

## C 我可以继续做（说一句就开工）

### 2026-11-07 本轮已补完（不用再安排）

| 原编号 | 补了什么 | 落点 |
|---|---|---|
| C2 / B5 | Wilson 95% CI + 论文表 + 台账 §3b | `src/cadcam/verify/stats.py`、`scripts/bench.py`、`PAPER-DRAFT.md` §5.4 |
| C6 | OCL 第三方判据扩到 **130 件外部集**，含过切浅/深分类与半格余量对照 | `scripts/m6_ocl_external.py`、`reports/m6_ocl_external.json`、`docs/M6-OCL-EXTERNAL.md`、图 6 |
| C7 | STEP → FreeCAD Mesh → 二进制 STL → OCL 独立几何链路 | `scripts/m6_step_stl_xcheck.py`、`reports/m6_step_stl*`、`M6-OPENCAMLIB.md` §3.5 |

> C7 的措辞已收紧：B-rep 内核仍是 OCC，**不是第二 CAD 内核**，只能写
> 「独立几何链路复核输入网格」。C6 顺带查出稀疏网格上的第二类盲区，见 B22。

### 2026-11-09 本轮已补完（不用再安排）

| 原编号 | 补了什么 | 落点 |
|---|---|---|
| A2 / D8 | 定下特刊目标；论文升 v0.2 | `docs/DECISIONS.md` §7、`docs/PAPER-DRAFT.md` §0 / §0.1 / 摘要 / §1 / §2 / §9 |
| — | 提案逐条对齐 + 给客座编辑的英文说明；**NX 直接不选，全文不出现、不解释、不致歉** | `docs/PAPER-DRAFT.md` §0、`docs/DECISIONS.md` D9、`latex/cover-letter.tex` |
| — | **自主性上移（autonomy relocation）+ 离线乐观上界**：零 LLM 调用，模型原始 G-code 刀心投影到验证器安全高度后双线仍 0/10、残留 25 503→25 623 mm³ → 缺的是覆盖规划而非下刀深度，L0 不靠「重写」救 | `docs/PAPER-DRAFT.md` §0.2 / §5.6、`scripts/m5_autonomy_probe.py`、`reports/m5_autonomy_probe.json` |
| — | 英文 Title + Abstract（≤250 words，符合期刊要求） | `docs/PAPER-DRAFT.md` §0.1 |
| B6 | §2 相关工作重写，逐篇区分 6 篇 2025–2026 撞车文献 | `docs/PAPER-DRAFT.md` §2.2 / §2.6 |
| 6b / B2 | 摘要加离散口径限定、标题改 verification-grounded、§1 把分工写成实证结论 | 同上 |
| C3 | **取消「压缩 §5.6」**：15 000 词容量足够，8 组实验全保留 | `docs/PAPER-DRAFT.md` §9.2 |

### 2026-11-09 晚本轮已补完（不用再安排）

| 原编号 | 补了什么 | 落点 |
|---|---|---|
| A3 | **英文全文**：按 SPJ 官方 LaTeX 模板生成完整 Research Article（四节结构、Title 77 字符、Abstract 236 词、6 图 4 表、20 篇已核实参考文献），编译零警告 | `latex/main.tex`、`latex/sections/01–05`、`latex/refs.bib`、`latex/main.pdf` |
| — | **cover letter**：特刊归属声明、意义与新颖性、未发表声明、AI 使用双重声明 | `latex/cover-letter.tex`、`latex/cover-letter.pdf` |
| A5 | 数据可用性、作者贡献、基金、利益冲突、知情同意占位 | `latex/sections/05-backmatter.tex` |
| §9.1 #8 | 参考文献逐篇核实（arXiv / OpenReview / 期刊目录 / DOI），按首次引用编号 | `latex/refs.bib` |
| — | **NX 按用户裁定直接不选**：投稿材料中彻底移除，不解释、不致歉 | `docs/PAPER-DRAFT.md`、`latex/*`、`docs/DECISIONS.md` D9 |
| — | 作者要求逐条核对（四节、单栏行号、图表顺序、AI 声明等） | `latex/README.md` 合规对照表 |

### 剩下的

1. **英文稿通读定稿**（A3）：全文已生成，只剩你通读拍板术语与文风。
2. **B16 多次采样（可选）**：`temperature=0` 下云端仍有方差（E14 两次 0.60/1.00）；
   英文稿已在 Statistical Analysis 与 Conclusion 如实写为 limitation，不补实验也能投。
3. **扩数据**（B1，可选）：给 STEP 就扩，每件约 10 分钟。
4. **git 初始化**（A4）：建仓库、`.gitignore`、LICENSE、首提交；远端与账号需你定。
5. **参数化中间动作空间实验 —— 已评估，不做**：11-09 结论为收益仅一条方法学完整性，
   却可能稀释「架构有效」这一核心贡献，且 10 题样本量统计上分不出中间档；
   已用两三句话在论文 limitation 中兑现大部分收益，等审稿人明确要求再作为 rebuttal 补做。

---

## D 环境事实（省得你踩坑）

- **云端 LLM 走的是 WorkBuddy 云服务数据面**，`keyless`、按账号积分。配置在
  `.workbuddy/cloud.json`（已 gitignore），换机器要重新开一次云服务拿 `endpoint`/`publishableKey`。
  `LLMClient(base_url="", model=..., api="cloud")` 即用，本地后端一个字节都不用改。
- **本地 Ollama 服务已于 2026-11-09 停止**（此前自 11-06 起常驻 71h 51m，本地 LLM 实验早已做完）。
  所有 `m5_*` 本地模型实验脚本需要它。要重跑时在 Git Bash 里执行：
  ```bash
  cd /e/tools/ollama && OLLAMA_MODELS="E:/tools/ollama/models" \
    OLLAMA_KEEP_ALIVE=30m OLLAMA_HOST=127.0.0.1:11434 ./ollama.exe serve
  ```
  跑完记得停止，避免长期占用。`KEEP_ALIVE=30m` 意味着显存里的模型会在空闲 30 分钟后自动卸载；
  停服务只是关掉常驻进程，模型文件都还在 `E:/tools/ollama/models`，不会丢。

| 事实 | 说明 |
|---|---|
| **这台机器没有 `make`** | Git Bash 报 `make: command not found`。用 `bash scripts/reproduce.sh` 或直接 `python scripts/xxx.py` |
| pytest / matplotlib 在 `/d/Anaconda3/python` | PATH 里第一个 `python`（WorkBuddy 自带）两者都没有 |
| torch 在 `/d/Anaconda3/envs/cadcam` | `m2_demo.py`、`eval_mfr.py` 必须用这个解释器，否则 `No module named 'torch'` |
| FreeCAD 1.1.4 已就位 | `m1_demo.py` **13/13**、`m2_demo.py` **10/10**、`m3_demo.py` 全 10 件实测通过 |
| 全流程确定性 | 连跑两次 `bench` 数字完全相同；M2 权重→指标复算（0.966 / 0.9042）逐位一致 |

---

## E 交付清单（核对用）

| 类别 | 文件 |
|---|---|
| 论文 | `docs/PAPER-DRAFT.md`（6 图 + 9 项投稿前 TODO） |
| 图 | `reports/figures/fig1–fig6.{png,pdf}` |
| 数据 | `data/DATASET-MANIFEST.json`（60 文件 sha256）、`data/zenodo_rptu/labels/` |
| 台账 | `reports/bench.json`、`reports/bench.md` |
| 复现 | `docs/REPRODUCE.md`、`scripts/reproduce.sh`、`Makefile`、`scripts/demo.py` |
| 合规 | `docs/DATA-CARD.md` |
| 第三方对照 | `reports/m6_ocl.json`、`reports/m6_ocl_gcode/*.nc`、`reports/m6_ocl_external.json`、`reports/m6_ocl_point_cutter.json`、`reports/m6_step_stl_xcheck.json`、`docs/M6-OPENCAMLIB.md`、`docs/M6-OCL-EXTERNAL.md` |
| 里程碑 | `docs/M0-REPORT.md` … `M4-FINAL.md`、`docs/M5-M6-FINAL.md` |
| 决策 | `docs/decisions/ADR-001-*.md`、`OPEN-DECISIONS.md`、`docs/DECISIONS.md` |

---

## F 我没做的事（如实列出，别在答辩时被问住）

1. **物理切削验证**（无机床）。按你的决定不做：生成物是 G-code，
   用 CAMotics（体积）+ OpenCAMLib（逐点几何）两个独立内核交叉验证即可，
   真机对本研究没有增量信息。论文里要写明"仿真验证，未做真机"。
2. **商用 CAM 对照**。未做，也不需要：商用套件要账号 + GUI、
   版本参数不可控、不可复现。已用免费非 GUI 的 OpenCAMLib 替代（B3），
   论文只写「与两个独立开源内核交叉验证」。
   **如果审稿人点名要商用 CAM，再单独说明为什么用开源内核替代，别假装做过。**
3. **LLM 完全自主的端到端闭环**（自己读工单→自己写 G-code→自己判→自己修，无契约层）。
   没做。做了的是：契约调用 20/20、直写 G-code **0/10**、建模脚本 **1/5**（B7）、
   两个决策点 + 澄清出口、多轮有界修复（B10）。缺口要如实说。
4. **真·非轴向零件**（倒扣 / 侧向孔）。斜底槽仍是单值高度场，3D dexel 未做（B20）。
5. **人工构念效度实验**已降级为 sanity check 并披露（题目包"红=过切"图例泄漏，
   平凡基线 1.0000），见 `docs/M3-HUMAN-QA.md`。
6. **连续几何零过切**：0.5 mm 栅格下离散膨胀在圆弧边界少盖半格，
   OCL 检出 0.76% 刀心位置；加半格余量能消除但残留涨 3.4 倍、双线掉到 9/10。
   已量化披露，未从架构上解决（B21）。
7. **STEP→STL 的独立几何链路**：OCL 用的仍是同一份 FreeCAD 剖分缓存，
   独立的是 CAM 算法，不是 CAD 内核（`docs/M6-OPENCAMLIB.md` §5.1）。

> 已从本清单移除的旧条目（都已完成）：数据集扩充（130 件 MFCAD 外部集）、
> LLM 直写 G-code / 建模脚本对照、Fusion 360 人工导出。
