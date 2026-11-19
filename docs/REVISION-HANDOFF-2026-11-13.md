# 论文与实验修订交接（2026-11-13）

> 本文件是当前接手入口，覆盖旧 HANDOFF 中“实验已冻结、没有技术缺口、只等投稿”的判断。用户已明确允许继续修改实验与论文。状态以文件与实际命令输出为准，不以助手的完成声明为准。

## 可直接发给下一位助手的提示词

你正在接手 E:\CAD2CAM，目标是提升论文对 Intelligent Computing 特刊 “Foundation Models Beyond Text: Towards General Multimodal Intelligence” 的证据质量与主题契合度。请先读 docs/REVISION-HANDOFF-2026-11-13.md，再看当前 latex/main.tex 和两份 reports/revision_*_audit.json。用户已允许突破旧交接的冻结边界，但不要保证录用，不要伪造实验或隐藏失败。保留原始归档；默认只做本地离线分析，不调用付费模型、不启动 Ollama、不读取云端密钥、不发布或投稿。需要这些动作时另行征求授权。

论文已从“action space 而非模型规模决定成功”的强因果叙事，改为“工具介导的空间/制造 grounding，以及参考几何与验证器的双重审计”。不要恢复不受支持的 0/10→20/20 配对对照、15 模型全部 30/30、legal=semantic success、内部零过切=物理安全等说法。优先收尾未完成的独立几何审计与图文一致性，然后运行测试、编译并目视检查图和 PDF。请维护本交接文档，明确哪些是部分完成。

## 当前实质发现

- 20 中文工单与 10 锚点直接 G-code 不是同题对照；contract/script 提示、接口与 token 预算也不同，不支持隔离 action space 的因果结论。
- 原 easy/contract 的 20/20 exports、20/20 old-gold agreement 并非 20/20 语义正确。13 题有 19 条执行错误；原 gold 和候选经过同一个 gateway，可共同漏掉特征。
- 原 easy/script 的 removed IoU 0.174 是 19 个成功导出的条件均值；20 题失败记零均值约 0.165。
- 15 个可用模型中 Llama3.1:8b 是 29/30 exports，其余 14 个 30/30。另一个 endpoint 的 HTTP400 不应记成模型能力失败。
- 外部 OCL 按真实工序刀径检查：B2 的 94/130 件存在 >0.5 mm 违规，最大 71.104 mm；半格 guard 后仍 8/130、最大 42.288 mm。不能用另一个点探针的缩径实验解释掉实际刀具违规。
- 内部 dual-line 是最大过切 <=0.5 mm 且平均绝对偏差 <=0.5 mm；不是工业公差保证，也不是连续刀盘安全证明。
- 20 编排任务来自 10 设计×2 余量；已知 deterministic fallback 的成功不等于开放世界 autonomous repair。

## 已有产物与备份

- 英文稿：latex/main.tex、latex/sections/01-introduction.tex 到 05-backmatter.tex、latex/cover-letter.tex、latex/refs.bib。
- 当前标题：Auditing Foundation Model Grounding in Design and Manufacturing。
- 原稿备份：build/submission_before_revision_20261113.zip（19 个原始 tex/bib/pdf 和交接文档文件）。本项目不是 git 仓库。
- 原端点抬升脚本备份：build/m5_autonomy_probe_before_revision.py。当前脚本只改解释，未改算法、未重写原报告。
- 新归档指标审计：scripts/audit_archived_metrics.py、tests/test_archived_metrics.py、reports/revision_archive_audit.json。16 个针对性测试已通过，固定 seed 的 task bootstrap 仅条件化于归档回答及旧参考，不是重复推理试验。
- 新独立构造审计：scripts/audit_workorder_reference.py、scripts/_workorder_reference_worker.py、tests/test_workorder_reference.py、reports/revision_reference_audit.json，工作目录 build/revision_reference_audit/。
- 没有覆盖原 reports/m5_nl_eval.json 或重新运行模型生成脚本。

## 独立参考审计：必须区分“构造完成”和“全部比较完成”

已读取的报告状态：audit_completed=true、reference_complete=true、archive_pairing_complete=true，但 complete=false。

- 重建了 20 题 latent 与 visible 参考；visible 对所有打印字段复现 %.0f，through-hole 未打印深度不参与几何。
- 当前生成器重建的文本不等于有证据保存的原始 prompt。检查 seed、ID、stock、first-pocket、原指标重现与 SHA256，只能证明归档一致性，不能证明原始提示完全相同。
- 独立的是构造逻辑，仍然使用 FreeCAD/OCC，不是第二几何内核。
- latent gold 不一致 12/20；visible gold 不一致 18/20。13/20 题有格式化引起的规格差异，不能归为模型错误。
- NL02 无 CAD 的矩形容斥 oracle：6400+8000-2400=12000 mm³。
- 目前 contract 重评分完整：latent removed IoU 均值 0.75264476、>=0.9 为 9/20；visible 均值 0.76036001、>=0.9 为 9/20。
- script 的 NL03 在 latent/visible/archived_gold 三种参考下均有 Boolean 审计错误；NL07 在 visible 下有错误。失败导出记零，审计错误必须保持 null。
- script latent 全题均值界 [0.17877054,0.22877054]；visible 界 [0.18112994,0.28112994]。不是点估计，也不是置信区间。
- worker 已尝试 removeSplitter、1e-7 fuzzy、共同刚体旋转、General Fuse cell partition；仍有违反体积恒等式或 provenance 映射的结果，不可盲信内核返回值。
- 待修缺陷：driver/worker 的 normalized() 统一 round(value,8)，把配置 relative tolerance=1e-9 写成 0.0。不要通过随意加大容差来让错误消失；NL03/NL07 的失败量级远不只是舍入噪声。
- 测试会允许 complete=false，只验证错误保留、参考有效性、archive pairing 和 hashes；“测试通过”不能写成“全部独立重评分完成”。修改 driver/worker/tests 后必须重跑审计刷新证据 hashes。

## 待办优先级

1. 核验上述 tolerance 序列化问题，保留旧 partial 报告后修复并离线重跑；如 OCC 错误仍存在，以透明 bounds 报告，不无限堆叠 fallback、不伪造最终均值。
2. 图 5 family 必须显式按 levels_le_2 / levels_3_4 / levels_ge_5；不能 sorted 后套固定标签。移除无依据的 0.60 接受线。
3. 图 2 node-only 应采用已有 held-out test 报告 mfr_ablation_test_v2.json（accuracy .6969、macro IoU .4462），不是 val .7042/.4529。决策树仍是 validation，必须明确 split 或移除不匹配比较。
4. 图 1–6 改为英文并更新 latex/figures/fig1.pdf 到 fig6.pdf；默认 scripts/figures.py 只写 reports/figures，需要明确复制。图 6 对零违规数和小于 1 mm³ 残留用了 display floor，caption 需说明或改用保留零的坐标。
5. 清理残留的无条件 safe/correct 叙述、CAMotics “removed workpiece volume”（实际为 remaining volume）、direct G-code 段对 Table 3 的错误引用。
6. 将部分独立审计的状态如实写进 Methods/Results/Conclusion；保留已验证的 contract 结果，不能暗示 script 全部比较已成功。
7. 最终跑完整 pytest，串行编译 LaTeX、检查 warning、渲染关键页面目视核验。作者姓名、ORCID、AI 工具版本和人工复核声明需要用户提供，不能代填。
8. hard 30 题尚无独立构造审计；旧 hard 8 题逐题 JSON 已丢失，不要把现在 30 题前 8 个冒充原 pilot。

## 安全的本地复现命令（Git Bash，工作目录 E:/CAD2CAM）

```bash
PYTHONPATH=src /d/Anaconda3/python -m pytest tests/test_archived_metrics.py tests/test_workorder_reference.py tests/test_m5_autonomy.py -q
/d/Anaconda3/python scripts/audit_archived_metrics.py
/d/Anaconda3/python scripts/audit_workorder_reference.py
PYTHONPATH=src /d/Anaconda3/python scripts/figures.py
PYTHONPATH=src /d/Anaconda3/python -m pytest -q
bash latex/build.sh
```

FreeCAD 默认 E:/FreeCAD_1.1.4-Windows-x86_64-py311/bin/freecadcmd.exe；driver timeout 900 秒。worker 即使返回码 0 也可能失败，必须检查 JSON。审计未完整时 driver 返回 1 是有意设计。原始 reproduce.sh 会重跑并覆盖部分原报告，不要作为无害验证命令直接运行。不要对唯一原始 m5_nl_eval.json 直接 rescore；旧缓存目录没有批次隔离。

## 验证记录（2026-11-13 下午更新，已由下方续修记录细化）

基线 177 tests passed；当前全量 **211 passed**。修订稿编译为 **23 页**、摘要 219 词、21 篇文献、6 图 4 表，零 undefined / 零 LaTeX Warning / 零 Overfull。图表已英文化并逐页目视核对。**仍不是无需作者信息即可投稿的终稿**：作者/ORCID/基金/AI 版本/人工复核声明待填，NL03 与 NL07(visible) 仍为未解审计项。

---

## 续修记录（2026-11-13 12:xx，本轮执行）

### 已完成

1. **tolerance 序列化缺陷已修**。`scripts/audit_workorder_reference.py` 的 `normalized()` 对
   `volume_abs_tolerance_mm3` / `volume_relative_tolerance` / `metric_tolerance` 及其
   `method.tolerances` 同名字段跳过 `round(..., 8)`。重跑后 `build/revision_reference_audit/input.json`
   中 `volume_relative_tolerance = 1e-9`，不再被写成 `0.0`。新增回归测试
   `test_tolerance_configuration_survives_serialization`。
2. **OCC 布尔健壮性修复**（不改容差、不伪造分数）：
   - `compare()` 改为 **General Fuse 单元分片优先**，成对布尔仅作回退；
   - 分片逐单元归一朝向，并强制 `sum(cells) == stock volume` 与 source-volume/provenance 校验；
   - `measure()` 对 `removed_fused`、`removed_common` 做 `removeSplitter()` 精化（NL07 的
     fuse 原始体积 32,722.260 → 精化 31,779.783，正好等于解析恒等式）；
   - 对「输出与毛坯共面导致 `stock.cut(output)` 返回整块毛坯」增加显式诊断，
     NL03 不再产生伪体积。
3. **审计状态（可复现，`reports/revision_reference_audit.json`）**：
   `audit_completed=true`、`reference_complete=true`、`archive_pairing_complete=true`、
   `complete=false`（5 个 audit error）：
   - latent contract 完整：removed-IoU 均值 **0.75264476**，≥0.9 为 **9/20**；
   - visible contract 条件均值 **0.787095**（19 题可评），NL07 因四舍五入参考产生
     退化布尔单元而未解；
   - latent script 全题均值界 **[0.17877054, 0.22877054]**（NL03 未解）；
   - visible script 全题均值界 **[0.18112994, 0.28112994]**（NL03、NL07 未解）；
   - gold disagreement：latent 12/20、visible 18/20；hidden-coordinate drift 13/20，
     均值 removed-IoU 0.99073469（归因为文本格式化信息损失，不计模型错误）。
   旧 partial 报告保留在 `build/revision_reference_audit/report_before_tolerance_fix_20261113.json`。
4. **图 1–6 全部英文化并重画**：
   - `scripts/figures.py` 新增 `--sync-latex`，显式复制到 `latex/figures/fig1–6.pdf`；
   - 图 5 family 改为显式 `levels_le_2 / levels_3_4 / levels_ge_5`，标签带 n，删除无依据的 0.60 线；
   - 图 2 node-only 改用 `mfr_ablation_test_v2.json`（0.6969 / 0.4462，与 GNN 同 test split），
     决策树不再进图，只在正文以「不同 validation split、不可比」说明；
   - 图 4A 改为哑铃图（6/8/10 mm → 0），图 4B 仅标注 Part 2/8/10，消除标签堆叠；
   - 图 6A 改为逐件最坏过切深度（94/130、guard 后 8/130），图 6B 保留 Pareto；两轴改 symlog，
     零值保留、不再用 display floor；标题缩短避免与 B 面板相撞；
   - 画布降到 6.9–7.6 in、正文改 `width=\textwidth`，避免排入版心后字号缩到约 4.5 pt；
     逐页目视核对了图 1 的框内溢出并修掉。
5. **正文修订**：
   - M2 node-only 由 0.7042/0.4529 改为 **0.6969/0.4462**，明确同 test split；
   - CAMotics 改为「remaining workpiece volume」，并说明同毛坯下等价于去除体积的聚合陈述；
   - 删除 direct G-code 段对 Table 3 的错误引用（Table 3 不含该实验）；
   - 把「部分独立审计」写入 Methods / Results / Conclusion：contract 完整、visible 与
     script 各有一题未解，script 只报 bounds，hard 30 无独立重建；
   - 去掉无条件 safe/correct 措辞（"safe height-map" → "conservative"；"kept outcome correct"
     → "decided whether the repaired outcome was acceptable"）；
   - `hyperref` 改 `[hidelinks]`，去掉图表/公式引用的红框。
6. **外部集逐件统计补充**（新增 `scripts/m6_external_stats.py`、`tests/test_external_stats.py`、
   `reports/m6_external_stats.json`）：94/130 的 Wilson 95% CI 为 [0.6406, 0.7928]，
   guard 后 8/130 为 [0.0315, 0.1167]；配对 McNemar 显示 86 件改善、0 件恶化、8 件仍超标、
   36 件始终合格（精确双侧 p = 2.58e-26）；阈值 0.5–5 mm 内计数稳定在 94/130，
   0.25 mm 为 102/130，10 mm 为 87/130，20 mm 为 49/130；覆盖率 <96% 的 34/36 件超标，
   96–99% 为 28/32，≥99% 为 32/62（Spearman ρ = −0.42）。正文、摘要、方法与结论已同步。

7. **验证**：`PYTHONPATH=src pytest -q` → **211 passed (19.93 s)**；
   `bash latex/build.sh` → **23 页**、摘要 **219 词**、**21** 篇文献、**6 图 4 表**、
   `main.log` 零 undefined / 零 LaTeX Warning / 零 Overfull；目视核对第 1、5、9、10、11、12、18、20–23 页。

### 仍未完成（不阻塞，但必须如实保留）

- 作者姓名、单位、通讯、ORCID、基金/无基金句、AI 工具名称与版本、人工复核声明：**只能作者提供**。
- 当前 APC、两个 proposal 的关系：外部确认。
- hard 30 题没有独立参考重建；归档只保留 calls 与指标，**没有导出实体 STEP**，离线无法重评，除非重新调用模型。论文已写明，不能当作已验证语义基准。
- NL03（script，三种参考）与 NL07（visible）仍是 `audit_error`；论文以 bounds/null 报告，
  未用放宽容差或伪造均值消除。
- 图 1 为手绘式示意图，非矢量美术终稿；如需期刊级重绘，另开任务。
