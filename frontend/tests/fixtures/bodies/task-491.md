## Delivers

lib/features/links.py`（R4 规则，含 follows 环检查）。`web/blueprints/features.py` 增加 links 的 POST/DELETE，ledger body 增加 `links`。`POST /api/features` 接受 `follows`。CLI 增加 `feature link/unlink`、`new --follows`。前端增加 `FeatureRelated.vue`（挂入 FeatureLedgerView 与 FeatureSectionNav）、「New follow-up」按钮、Features 列表的 follows chip。测试：`tests/features/test_feature_links.py`、`frontend/tests/f155-feature-links.spec.js

## Spec 依据

R4, I4

## 验收测试名

- `test_feature_link_rules`
- `test_feature_page_related_section`
- `test_new_follow_up_links_feature`

## 收尾动作

合回 `f155/integration`（这是报告完成的前置条件，不是可选步骤）
