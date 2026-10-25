## Intent

feature 是整条 pipeline 的入口实体:先有 feature,才谈得上 task、run、归属与 ship。但浏览器里没有创建它的地方 —— 人口页只读,创建只能在命令行。于是网页上的自然动作变成「先建一个 task」,而 task 不是入口:它的 run 不会给任何 feature 记一轮,从它报的 defect 无处归责。

界面缺一个控件,就让整个模型的使用顺序在实践中被倒过来。这个 feature 补上那个控件:人口页就是入口,新建之后直接落在这个 feature 自己的页上 —— 下一步要做的事(写 spec、归属会话、ship)都在那里。

## Non-goals

- **不做收编远端 issue。**创建端点有两种形态,这次只上 native 一种。收编要活的 tracker 凭据与它自己的失败态,是另一个 feature。
- **不改后端。**创建端点已经存在并且是唯一写入者;这次补的是浏览器上的那一半,以及它至今没有的测试。
- **不做删除。**feature 只被 abandon,界面上不该出现删除。

## Acceptance

验收项即测试名,实现前先让它们红。

- test_web_create_declares_a_native_feature
- test_web_create_refuses_an_empty_title
- test_web_create_is_editor_only
- the index carries the control that declares a feature
- declaring lands on the new feature page
- a viewer sees no declare control