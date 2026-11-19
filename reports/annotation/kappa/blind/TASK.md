# 盲标任务：30 个面的独立标注

你是一名独立标注员。任务是给 30 个面各打一个"工艺家族"标签，用于计算两名标注员之间的一致性（Cohen's κ）。

## 硬约束（必须遵守）

1. **只允许读本目录 `reports/annotation/kappa/blind/` 下的文件**：`samples.json`、`img/*.png`、本文件。
2. **禁止打开**：`data/zenodo_rptu/labels/`、`scripts/annotate/overrides/`、`reports/annotation/kappa/answer_key.json`、
   `docs/ANNOTATION*.md`、`reports/annotation/summary.json`、`reports/annotation/review.html`。
   这些文件里有标准答案，读了这次 κ 就作废。
3. **不要猜"出题人想要什么"**，按你自己的判读给出标签；判不准就标 `unknown` 并写明缺什么信息。
   低置信度不扣分，盲从才扣分。

## 输入

- `samples.json`：30 条，每条含 `sample_id`、零件包围盒、零件面数、该面的面型/面积/法向/包围盒/形心/参数、
  以及 1-hop 邻面摘要（邻面的面型、面积、法向）。
- `img/<sample_id>.png`：两个视角，红色高亮的就是该面。`view A` 是从该面法向看的视角，`view B` 是俯视。

## 要判什么

给每个面一个 `family`，取值必须在这 9 个里：

| family | 含义 | 典型判据 |
|---|---|---|
| `stock` | 毛坯原始表面（未加工的六面、基准面） | 平面、法向为轴向（±x/±y/±z）、面落在零件包围盒的某个面上 |
| `hole` | 被切除的孔（盲孔/通孔/台阶孔） | 圆柱面且**法向朝轴线**（凹），或锥面围成的凹坑 |
| `boss` | 凸出的实体台/岛 | 圆柱面法向**背离**轴线（凸），或平面围成的凸起实体 |
| `pocket` | 型腔：有底、被壁围合、材料被挖掉 | 水平底面 + 若干竖壁，封闭或半封闭 |
| `slot` | 槽：细长，两侧壁平行 | 两平行竖壁 + 底（或贯通），长度 >> 宽度 |
| `step` | 台阶：一级高度差形成的肩面 | 水平肩面 + 一段竖壁，把零件分成两个高度 |
| `fillet` | 圆角/倒圆（切向过渡） | 圆柱面或环面，与相邻面**相切**（光滑边），radius = 相邻壁圆角 |
| `chamfer` | 倒角（平直斜面过渡） | 与两邻面成固定角度的窄平面，通常是 45° |
| `freeform` | 自由曲面 | BSpline/Bezier 等非解析面，无固定半径 |

再加：

- `mfcad_class`：能对上 MFCAD++ 的 25 类就写类名，对不上写 `null`（**不许硬塞**）。
  常用类名：`stock`、`rectangular_pocket`、`circular_end_pocket`、`rectangular_blind_slot`、
  `rectangular_through_slot`、`rectangular_blind_step`、`rectangular_through_step`、
  `slanted_through_step`、`blind_hole`、`through_hole`、`round`、`chamfer`、
  `rectangular_passage`、`triangular_passage`、`circular_through_end`、`triangular_blind_step`。
- `confidence`：0–1，你对这个标签的把握。
- `reason`：一句话（≤ 60 字），说明依据（用面积/法向/邻面这些量说话）。

## 输出

写到 `reports/annotation/kappa/subagent.json`，格式：

```json
{
  "annotator": "subagent-blind-v1",
  "items": [
    {"sample_id": "S01", "family": "stock", "mfcad_class": "stock", "confidence": 0.9,
     "reason": "平面，法向 (-1,0,0)，面积 2720 落在包围盒 x=-40 面上"}
  ]
}
```

必须 30 条，`sample_id` 覆盖 S01–S30，不得重复、不得缺。

## 建议工作方式

1. 先读 `samples.json` 建立整体印象（哪些面是平面、哪些是圆柱、哪些是 BSpline）。
2. 逐条看图（`img/S01.png` … `img/S30.png`），结合几何量判断；**图与量冲突时以量的数值为准**，
   图只用来确认"这个面在零件的哪个部位"。
3. 全部判完后自查一遍：30 条齐不齐、family 拼写是否在 9 类之内、有没有把 `null` 写成字符串 `"null"`。
