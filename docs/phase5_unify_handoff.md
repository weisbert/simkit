# Phase 5 收尾交接 —— Corner / Corner Manager 合一 + GUI 英文化

> 写于 2026-05-20。Phase 5 Corner Manager 已建成并提交(commit `1b4dee5`,
> 6 stage 模型层 + CLI + GUI,离线测试全覆盖)。本文件是交给下一个对话执行的
> 两项收尾任务 —— 来自用户(RFIC designer)的两条指示。

## 背景:用户的两条指示

1. **corner 和 corner manager 在用户逻辑上是一个东西** —— GUI 不能把同一个
   "corner" 概念劈成两个标签页。
2. **GUI 全部用英文** —— 用户可见字符串一律英文(对话回复仍用中文)。

## 任务 A —— 合并成单个 "Corners" 标签

**现状(劈成了两个视图):**
- `main_window.py:272` 静态 `"Corners"` 标签 = Phase 4 `CornersEditor`(简易网格)。
- `main_window.py:566` `_open_corner_model` 动态加 `"Corner: <name>"` 标签 =
  Phase 5 `CornerManagerView`(完整 manager)。

**目标(用户已拍板「永远是 Corner Manager」):**
- `"Corners"` 标签永远承载 `CornerManagerView`,全程只有这一个标签。
- 删掉动态 `"Corner: <name>"` 标签路径。`File → Open Corner Model…` 改为
  「把选中的 sidecar 加载进那个唯一的 Corners 标签」,不再新开标签。
- 工程里**没有 `.cornermodel.json` 时**:从 Maestro 当前 corners 自动生成一个
  minimal `.cornermodel.json`(建议落在 `.pvtproject` 同级,名 `<project>.cornermodel.json`),
  使 manager 永远有内容 —— 空态也不退回简易网格。
- **删除** `python/simkit/gui/views/corners_editor.py`(Phase 4 `CornersEditor`)。
- 把旧 Corners 标签独有的能力迁进 `CornerManagerView` —— 主要是 **Pull**
  (`main_window.py` Corners §7 区,约 1371–1404 行 + `_handle_pull`)。

**依赖审计要点:**
- `main_window.py:74` `from simkit.gui.views.corners_editor import CornersEditor`
- `main_window.py:260` `self.corners_editor = CornersEditor()`
- `tests/gui/test_main_window.py` 引用 `corners_editor`
- `docs/phase5_dogfood_checklist.md` Stage 1 注解写「pull 走 Corners 标签」——
  合并后这条失效,需同步改。

## 任务 B —— GUI 全英文

把用户可见字符串改成英文。含中文的 GUI 文件(2026-05-20 审计):
- `gui/views/corner_manager.py` —— 72 行含 CJK
- `gui/corner_model_table.py` —— 10 行
- `gui/main_window.py` —— 45 行(**区分**用户可见字符串 vs 注释/docstring;
  仅前者是硬要求)
- `gui/views/corners_editor.py` —— 任务 A 会删除,免做

收尾时全量审计 `python/simkit/gui/**` 是否还有遗漏。用户可见 = QLabel / 按钮 /
tooltip / 菜单项 / QMessageBox / 标签页名 / 状态栏文案。

## 验收

1. 离线 `pytest` 全绿(当前 1902 passed —— 删 `CornersEditor` 后相关测试要调整,
   数会变;新增合并逻辑要补测)。M2 view-render 测试是硬门。
2. 按 `docs/phase5_dogfood_checklist.md` 跑 live Maestro dogfood(Virtuoso 当前在线,
   `/tmp/skill-server-default.sock` 存在)。checklist 里凡提「Corners 标签」
   「Open Corner Model 新增标签」的措辞合并后已过时,需同步改。
3. dispatch 子代理时按 `docs/dispatch_mandates.md §2` 注入。

## 不在本次范围(仍 deferred)

corner-model GUI pull 的交互式 reconciliation 回填、`pvt corner-model push/pull`
CLI、多 sidecar 标签去重、`.pvtproject` 自动发现 `cornerModelsDir`。见
`docs/phase5_dogfood_checklist.md` 末表。
