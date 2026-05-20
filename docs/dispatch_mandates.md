# SimKit 开发流程护栏 — Agent-Dispatch Mandates

> 立项:2026-05-20 流程改进讨论(DECISIONS #79 D13 的 7 个缺陷模式为证据)。
> 生效范围:**Phase 5 = Corner Manager 起的所有新规代码**。Phase 4 GUI 不做全量回填
> (用户决定 2026-05-20)。Phase 4 的 3 个遗留小项单独清。

---

## 0. 为什么需要这份文档

7 个"漏到用户 dogfood 才被发现"的缺陷,收敛成 3 个根因:合成 fixture 不匹配 Maestro
真实形状 / 断言打在数据层而非视图层 / 没有端到端真机验证。

**元问题:** 这 3 个根因在缺陷发生前就已各有一条记忆精确记录过
(`feedback_mock_match_production_shape`、`feedback_pyqt5_widget_visibility_test`、
`feedback_pm_mode_verification`)。失败的不是知识,是执行。

- 记忆是"软"的:orchestrator 只在"看起来相关"时调用它。
- subagent 是冷启动:**它看不到任何记忆**。Phase 4 大部分代码由 4 个并行 agent 写,
  它们对这些教训一无所知。

所以修法只有两条腿:
1. **机械闸门** —— 失败时大声报错的测试/检查,不依赖谁"记得"。
2. **prompt 注入** —— orchestrator 派活时,把相关 mandate 整段贴进 agent 的 prompt。

---

## 1. 四条 Mandate

### M1 — 数据 I/O 测试只吃 live-shape fixture

任何测试覆盖 union / corner / measure 的 pull / push 路径时,**必须**消费从真机
Maestro pull 下来的 fixture,存于 `tests/fixtures/live/`,文件头带 provenance
(来源 testbench / session / pull 日期)。**禁止在 I/O 测试里手写 dict 充当 Maestro
返回值。**

- 理由:手写 dict 反映的是作者对数据形状的*猜测*;真机会用 `model.section` 数组、
  空 `_file_abs`、逗号分隔的多段值等作者想不到的形状。
- 怎么拿:Virtuoso 在线时用 skillbridge 探针 pull 一次,落盘进 `tests/fixtures/live/`。
- 防住:#1 `_file_abs` 空、#3 process 列。

### M2 — 每个 GUI view 必须有视图层渲染测试 【硬闸门】

每个有 bulk-load API 的 `gui/views/*.py`,必须有 pytest-qt 测试断言**渲染几何属性**
(`rowHeight() > 0` / `viewport().visibleRegion()` / `header sectionSize`),
不能只断言 `model.rowCount()`。

- 机械闸门:`tests/gui/test_view_coverage.py` —— meta-test 枚举 `gui/views/`,
  任何非 grandfathered 的 view 若缺渲染测试,**整个测试套件 fail**(用户拍板:硬 fail,
  无白名单豁免)。
- 约定:渲染测试 = `tests/gui/test_<view>.py` 里至少一个函数名含 `render` 的
  `test_*` 函数。
- Phase 4 的 11 个现有 view 列入 `_GRANDFATHERED`,不回填;新增 view 不在表里,
  必须配渲染测试才能让套件转绿。
- 防住:#2 0-px 行、#3。

### M3 — 接 Qt 信号的 controller 必须有 connect-path 测试

任何 wiring Qt 信号的 controller,必须有测试**真正实例化它并触发 `.connect()` 路径**。

- 理由:PyQt5 在 `.connect()` 时就严格校验 slot 签名,签名不匹配直接 `abort()` +
  core dump —— 不用跑仿真,一个实例化测试就能抓到。
- 防住:#4 RunController `@pyqtSlot` 签名 abort。

### M4 — 每个特性 "done" 前填 Definition-of-Done 清单

orchestrator 在宣布任一特性 "done" 前,逐项填以下清单,并把填好的清单写进 handoff
commit:

```
[ ] M1  数据 I/O 路径用了 tests/fixtures/live/ 下的 live fixture(无 I/O 改动则 N/A)
[ ] M2  新增/改动的 view 有名字含 render 的渲染测试,test_view_coverage 转绿
[ ] M3  新增/改动的 Qt-signal controller 有 connect-path 实例化测试
[ ] V   真机验证:已跑 skillbridge 探针确认 —— 或显式标 "NOT live-verified" + 原因
[ ] R   agent 报的 "done" 已由 orchestrator 独立复核(agent 的完成声明只是假设)
```

- 真机验证(V):用户拍板 —— 允许标 `NOT live-verified` 进 handoff(承认 Virtuoso
  不总在线),但**必须显式标注**,且下一个开 Virtuoso 的 session 必须先清这些欠账。
- 防住:#5 #6 #7 + 兜底。

---

## 2. Agent-dispatch prompt 注入

subagent 冷启动看不到记忆。orchestrator 每次派 agent 时,**把下面这段整体贴进 prompt
的开头**(按 agent 实际触碰的层删掉不相关条目):

> **流程护栏(必须遵守,完成前自查):**
> - 改数据 I/O(union/corner/measure pull/push):测试只能用 `tests/fixtures/live/`
>   下的 live fixture,禁止手写 dict 当 Maestro 返回值。
> - 新增/改 GUI view:必须配一个函数名含 `render` 的 pytest-qt 测试,断言
>   `rowHeight()/sectionSize/visibleRegion` 等渲染几何,不能只断言 `rowCount()`。
>   否则 `tests/gui/test_view_coverage.py` 会让整套测试 fail。
> - 新增/改 Qt-signal controller:必须有测试实例化它并触发 `.connect()`。
> - 报 "done" 前自查上面各条;凡是接 SKILL/bridge 的路径,若没真机验证,明确写
>   "NOT live-verified" 而不是默认当通过。

---

## 3. 贯穿规则

- **agent 报 "done" ≠ done。** agent 的诊断和完成声明都是*假设*;orchestrator 独立
  复核(看真实 diff、跑测试、必要时真机探针)后才算数。见
  `feedback_reproduce_before_fix`。
- 每阶段结束门槛仍是 `corner_manager_plan.md §4`:Python 测试全绿 + SKILL Tier-1
  全绿 + 一次真实 Maestro round-trip dogfood —— 不是"测试数达标"。
