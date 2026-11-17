# S3-MATCH-020 本地交付原型

本目录实现市场/服务地域因子、层级加载器及纯本地测试。ClawHunt 任务没有配置 GitHub 仓库，也没有提供 S2-TAX schema；因此这是一个归一化 DTO 原型，不声称已接入上游系统。测试使用虚构编码，不读取真实客户资料，也不访问任何外部数据服务。

## 代码仓库

公开仓库：https://github.com/PikachuGits/s3-match-020-market-region-factor

## 任务验收映射

| 验收标准 | 对应测试 |
|---|---|
| 完全一致、父子、兄弟、全国各有测试 | `test_exact_match_scores_one`、`test_direct_parent_child_scores_point_seven_in_both_directions`、`test_direct_siblings_score_point_three`、`test_national_special_overrides_direct_parent_child` |
| 多地域逐项最佳值并正确平均 | `test_two_candidate_sources_are_unioned_and_best_is_per_demand` |
| 任一侧整体缺失时 0.5 且无理由 | `test_empty_demand_side_is_neutral_without_reason_but_with_evidence`、`test_empty_candidate_side_is_neutral_without_reason_but_with_evidence` |
| 边界默认值明确且可复现 | `test_boundary_defaults_are_declared`、`test_parent_child_depth_boundary_is_direct_edge_only`、`test_non_direct_ancestor_uses_zero_assumption` |

## 运行测试

从本任务目录运行：

```bash
PYTHONPATH=. python3 -m unittest -v test_market_region_factor
```

测试只依赖 Python 标准库。平均值测试以 `abs_tol=1e-12` 容差比较；单条规则常量按字面值断言，不做舍入。当前环境没有安装 pytest，因此测试使用 `unittest`/`math.isclose` 的等价容差断言，不额外安装测试依赖。

## 明确区分：任务要求与实现假设

任务原文明确：完全一致 `1.0`、父子 `0.7`、同一父级兄弟 `0.3`、候选“全国”对国内需求 `0.6`、每个需求地域取最佳后平均、任一侧缺失 `0.5` 且不生成理由，并输出编码对和来源字段。

原文没有定义以下情况；原型先采用可复现假设，交付前应向买方确认：

1. 评分顺序：完全一致 `1.0` → 候选全国且需求国内 `0.6` → 直接父/子 `0.7` → 直接兄弟 `0.3` → 其他有效配对 `0.0`。全国特例优先于直接父子；全国与全国的完全一致仍为 `1.0`。
2. 父子暂按树中的直接边；多层祖先/后代不算直接父子。若没有其他已定义规则，按 `0.0` 假设。
3. 整个需求侧或合并后的候选侧在清理后没有可用 code，才按缺失返回 `0.5`。列表内的 `None`/空白项跳过；有合法值时仍基于合法值计算。非空但不在字典中的 code 抛 `UnknownRegionCodeError`，作为数据契约错误处理。
4. 若供给条目和能力卡都提供地域，候选 code 取并集、每个需求地域取全体候选中的最高分；同 code 的来源分别保留在证据中，并列最佳全部输出。
5. 分数返回未舍入的 Python `float`，平均用 `math.fsum`；目标工程若规定不同精度，以买方契约为准。
6. “不生成理由”与“证据是否存在”分开处理。原型 `reason=None`；`evidence` 独立返回。缺失时仍保留 `missing_fields`，候选缺失时逐需求证据记中性分且匹配对为空；无匹配时逐需求分为 `0.0`、匹配对为空。
7. 全国特殊分仅用于 domestic demand；全国候选对非国内需求未命中其他规则时按 `0.0` 假设。
8. 重复需求 code 在归一化时去重，按一个需求地域计分；列表内的空项跳过。

## 边界默认值（任务原文未规定，买方未确认）

以下两项已在代码中固化为模块常量；默认值既是当前实现的边界，也是提交前仍要向买方确认的假设：

| 边界项 | 默认值 | 代码常量 | 固定行为 |
|---|---|---|---|
| 有效但无已定义关系的配对分 | `0.0` | `UNRELATED_PAIR_SCORE` | 两个非空 code 均在字典中，但未命中完全一致、全国、父子、兄弟时，按 `0.0` 计入该需求地域的最佳分与平均值；不生成匹配对证据。 |
| 父子最大深度 | `1`（仅直接边） | `PARENT_CHILD_MAX_DEPTH` | 只有直接 parent/child 才记 `0.7`；祖父/孙等更深祖先不按父子计分，按无关系 `0.0` 处理。 |

- 这两个默认值不冒充买方规则；若买方确认不同口径，需同时修改常量、测试与本文档；若改变关系语义，再改匹配逻辑。
- `parent_child_depth()` 返回实际层数；默认边界为 1，2 层及以上不会进入 `0.7`。

本地字段 `RegionRecord(code, parent_code, is_domestic)`、`demand.market_codes`、`supply_item.market_codes`、`enterprise_capability_card.service_area_tags` 均为适配层内部 DTO/来源标签。接入 S2-TAX 时须先按其正式 schema 映射。

## 平台交付路径

平台任务字段为 `delivery_channel=platform_only`，平台侧未配置 repo owner/name 或 repo access。交付以平台 `solution_text` 正文 + archive 附件为主；本单另外创建了公开 GitHub 仓库用于匿名审核（见上方链接）。本地压缩包尚未上传，未向平台提交。
