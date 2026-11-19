# 审稿弱点清单：逐条裁决与应答口径

> **2026-11-13 修订警示**：以下“不补实验／已披露所以不是弱点”的裁决已被本次代码与原始数据核查部分推翻，不得直接用于答审稿。应优先读取当前英文稿和 `reports/revision_archive_audit.json`。具体包括：0/10 与 20/20 非同题对照；原始 gold 与被测契约共用失败路径；13/20 常规工单存在执行错误；15 模型并非全30/30；71.104 mm 也出现在真实尺寸刀具的独立预测字段，缩小点探针不能排除该违规；外部94/130件有 >0.5 mm 违规，半格余量后仍8/130。公开披露限制是必要的，但不能将安全失效、评价循环或未控制混杂自动转为方法有效性的证据。用户已明确授权重新补做有价值的实验。

> 日期：2026-11-12。绑定 `latex/main.pdf`（v0.3，21 页）与 `latex/cover-letter.pdf`。
> 用途：收到审稿意见时逐条对照，避免临时补实验或改口径。
> **结论：8 条里 6 条现稿已主动披露、1 条被读反、1 条属用户已裁定的结案范围。
> 不需要补任何实验。真正要防的是两处叙事/流程风险，见 §3。**

---

## 1. 逐条裁决

严重度按 *Intelligent Computing* 特刊（基础模型 / 多模态 / trustworthy & evaluation protocols）
重评，不按制造类期刊标准。清单原评级用的是后者，所以整体偏高。

| # | 清单条目 | 原评 | 重评 | 现稿状态 |
|---|---|---|---|---|
| 1 | 缺乏真实 CNC 物理加工实验 | 很高 | **已结案 / 不适用** | Conclusion 明写 "Nothing was cut on a machine; verification is simulation-based"；未来工作段列出真机会额外测什么（刀具挠曲、装夹、控制器行为）。用户 2026-11-07 裁定真实铣削无意义。特刊不要求实物验证 |
| 2 | 主要核心实验只有 10 个基准零件 | 高 | **事实误读 / 低** | 实验共四族：10 锚点件、20 编排任务、15 模型 × 3 任务集、130 外部件 + 两个第三方内核。10 件只是「刀路几何」一族。Statistical Analysis 已给 Wilson 区间：10/10 下界 0.7225，130/130 下界 0.9713 |
| 3 | 130 个外部零件仍然偏合成化 | 高 | **属实、已披露 / 中** | Conclusion 原句 "All parts are synthetic regular solids"，并列出缺席的自由曲面、薄壁、倒扣。MFCAD++ 是公开基准，合成件是该领域标准做法。全文不蹭 general / 跨任务泛化 |
| 4 | 第三方验证发现 3.275% 刀具中心位置存在穿越问题 | 高 | **读反了 / 不是弱点** | 术语应为「过切标记」，非刀具穿越或碰撞。分母 9,660,095 个采样刀心点，316,398 个标记。这是**验证器自审计的产出**，写进摘要、Introduction 第四条发现、Results §第三方审计、Conclusion 与 cover letter 第三条理由。是卖点 |
| 5 | 大模型实验缺乏充分重复采样 | 中高 | **属实、已披露 / 中** | Statistical Analysis 与 Conclusion 两处明说单次 `temperature=0`，并给出具体反例：同一云端模型同一题两次 0.60 / 1.00。因此跨模型结论写成 trends and boundaries，不做显著性检验、不排名 |
| 6 | 20/20 成功率依赖预设修复策略 | 中高 | **属实、已披露 / 低** | Introduction、Results、Conclusion 三处主动写 "constructed result"。并把注意力引到 2/20 无修复基线与 0.90→0.15 平均修复轮数 |
| 7 | 缺少更充分的工业场景验证 | 中高 | **属实、已披露 / 中** | Conclusion 写 "one industrial domain"；单值高度场、2.5D、无五轴、无倒扣全部明写；CFP 契合度评审时已主动写进 limitation，不宣称 general |
| 8 | 0.5 mm 网格下离散验证不保证连续几何零过切 | 最警惕 | **属实、刻意设计 / 低—中（叙事风险）** | 全文 6 处限定，见 §2。**摘要与 cover letter 主动写明**，不是被审稿人挖出来的 |

---

## 2. 第 8 条的完整防线

这不是「论文自己承认了致命伤」，是论文把判据的适用边界量化出来了。六处限定：

| 位置 | 限定方式 |
|---|---|
| Abstract | "to zero **within our discrete representation**" 紧跟 "Two independent open-source kernels ... **bounding the zero-gouging claim**" |
| Introduction | "we report no cross-kernel claim"；第四条发现直接写 3.275% |
| Methods（掩码定义处） | "gouging is identically zero" 后立即 "We stress that this identity holds **inside the stated representation**" |
| Methods（第三方链路） | "It is **not a second boundary-representation kernel** ... not that two CAD kernels agree" |
| Results | "every statement of zero gouging in this paper is **scoped to a single-valued height field ... with one shared discrete disc**. We do not claim continuous-geometry zero gouging or a guarantee over arbitrary geometry" |
| Conclusion | "Zero gouging therefore holds inside a single-valued height field at a stated grid resolution with a shared discrete representation, and we do not state it more broadly" |

**证据链（答辩时按此顺序报）**：

1. 这不是自证。CAMotics（体积网格）与 OpenCAMLib（连续刀具—三角相交）是两条独立实现，锚点件上体积差均值 1.15%、点刀高度图逐柱一致 ≥99.828%。
2. 盲区被定位到具体机理：半径膨胀按「柱心到柱心」量距离，连续几何按「柱心到台阶边」量距离；圆弧附近离散掩码少盖半格；生成器和验证器共用同一个离散圆盘。
3. 盲区是分辨率伪影，有三条独立佐证：网格 0.5 → 0.25 → 0.125 mm 时第三方过切率单调下降 1.332% → 0.432% → 0.143%；半格余量把锚点件过切位从 2,822 降到 41（消掉 98.4%）；STEP→STL 独立几何链路逐柱一致 ≥99.984%，说明不是输入网格偏差。
4. 边界的代价也量化了，不是一句「未来工作」：半格余量使残留体积 ×4.42，双线通过从 130/130 掉到 107/130。所以 **0.5 mm 分辨率下连续几何零过切与精度线不可同时满足**，这是实测结论而非免责声明。
5. 结论段已明写贡献重心不在零过切："The discrete identity is a construction, not a finding about geometry, and we do not ask it to carry the paper." 承载论文的是动作空间 0/10 → 20/20 与对判据边界的定位及代价，两者都不依赖连续几何零过切。

**若审稿人仍追问**：不要改数据、不要把 limitation 藏起来。正确做法是把上面 1–5 复述一遍，并指出特刊 CFP 明文把 *robustness / trustworthy / evaluation protocols / real-world reliability* 列为主题，主动量化判据边界正是该主题要的证据。

---

## 3. 真正的两处风险（都靠文字/流程解决，不补实验）

### R1 claim 层级错配（已修补）

标题 "Verification-Grounded"、摘要首句把「可判定」立为主张，而 limitation 集中在倒数第二段。
审稿人可能读成「既然不能保证连续几何零过切，verification-grounded 还剩什么」。

**已在 `sections/04-conclusion.tex` 补三句**：把离散恒等式定性为 construction 而非几何发现，
明确承载论文的两个测量（动作空间对比 + 判据边界及其代价），并点明二者都不要求连续几何零过切。
摘要、cover letter 保持不动——它们原本就把负面结果写在正面结果旁边，动反而会破坏这个诚实结构。

### R2 审稿人构成（流程，作者侧处理）

客座编辑偏机器学习，但可能邀请 CAD / 制造背景审稿人，后者会用「无真机、只有 10 件、
无工业件」的标准打分。这不是数据能补的。

**处置**：cover letter 第 2 段已把定位钉在 *evaluation protocol for real-world reliability*，
第 3 段已主动写自审计；投稿时在 checklist 的 potential referees 里优先推荐多模态 agent /
可信 AI / 评测协议方向的审稿人。这一栏仍待作者填写（`latex/README.md` 第 4 项）。

---

## 4. 明确不做的事

| 提议 | 裁决 |
|---|---|
| 补真机切削 | **不做**。用户已裁定无意义；且要机床、刀具、测量与新的误差口径，等于开新实验线 |
| 扩工业真实件 | **不做**。需外部 STEP；图表 6 图 + 4 表已用满期刊 ≤10 上限，加数据必然挤掉现有图 |
| 云端多次采样报方差（B16） | **不做**。要重启本地模型 / 花积分；且已如实披露单次与 0.60/1.00 反例，补了只会把 trends 变成仍不显著的区间，不改变任何结论 |
| 参数化中间动作空间 | **不做**（上一轮已判）。会稀释「架构有效」这一主张，样本量也撑不住梯度 |
| 把 limitation 从摘要挪走 / 弱化 3.275% | **不做**。这是全文最经得起查的部分，删了才会被判选择性报告 |

**唯一触发补实验的条件**：审稿意见逐字点名要某项（真机 / 工业件 / 重复采样）。
那时目标单一、只答一个问题，rebuttal 里性价比最高；现在预先铺开等于自乱口径。

---

## 5. 收到意见后的自检顺序

1. 先在第 1 节表格里找对应条目，确认是否已披露，不要凭印象重做实验。
2. 定位到现稿具体段落与 `reports/*.json` 原始数字，答复里给证据指针而非形容词。
3. 数字一律照抄归档结果文件，不重新估算、不四舍五入改口径。
4. 若确实要补，只补被点名的那一条，同步 `scripts/reproduce.sh`、`docs/DATA-CARD.md`
   与 `latex/README.md` 的图表/引用计数，再跑 `bash build.sh`。
