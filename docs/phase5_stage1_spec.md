# SimKit Corner Manager — Phase 5 Stage 1 §1 规格

> 日期:2026-05-20。本文是 **Stage 1 的 §1 冻结规格**,依据 `docs/corner_manager_plan.md`
> §3 Stage 1 + §6 reconciliation。Stage 1 只解决**痛点 b**(改模式寄存器时无法全局
> 更改);痛点 a/c/d/e/f/g/h 分别归属 Stage 2–5,本文明确标注延后项。
>
> 本规格在 `docs/dispatch_mandates.md` 的 4 条流程护栏下交付 —— §11 逐条说明合规要求。

---

## 1. 问题陈述与范围

用户为一个 7 模式 LO 系统设计 corner。每个模式(`BT_2G_RX`、`BT_2G_TX`…)是一组
**寄存器配置**。当电路结构变化、某个寄存器(如 `d_en_dummy`)对某模式要从 1 改成 0,
用户当前只能逐列手改该模式的每一列 corner —— 没有"单一数据源 + 全局生效"。

**Stage 1 交付**:把**模式(mode)**做成一等对象,作为寄存器配置的单一数据源;corner
列引用模式取值;改一次模式,所有引用列同步。配套:自动命名、Cadence 双向 round-trip、
托管/未托管 reconciliation。

**范围内:**
- mode 数据模型 + `.cornermodel.json` sidecar。
- corner 列 = `mode 引用 + 列自有 PVT var + 手改覆盖 + models`。
- 全局编辑:改 mode 的一个 var → 所有引用列即时同步。
- 自动命名 `<mode>_<pvt_label>` + alias。
- 物化:cornermodel → Phase 2 union → CSV/push → Maestro。
- reconciliation:pull 时托管列对账、未托管(foreign)列标记 + 收编。
- 覆盖优先级 Stage 1 子集:`手改 > 模式base`,D1 标红。

**范围外(延后,本文不实现):**
- PVT 模板 / 聚合 corner / 复合轴 → Stage 2(痛点 a + h)。
- 变体 + 三层回落链 + D2/D3 → Stage 3(痛点 c)。
- 组合/运行集 + 列筛选 → Stage 4(痛点 d + f)。
- 行筛选 / 行拖拽 / 加载校验 / 跨工程模板库 → Stage 5(痛点 e + g)。
- mode 管理 models(process section):Stage 1 中 `section` 属列自有 PVT,不归 mode。
- mode var 取 sweep 数组值:Stage 1 中 mode var 一律标量;sweep 属 PVT,见 Stage 2。

---

## 2. 数据模型

### 2.1 三个概念

| 概念 | 定义 |
|---|---|
| **模式 mode** | 命名的寄存器配置:一组 `var → 标量值`。单一数据源。 |
| **列 column** | 一列 corner。托管列 = `mode 引用 + pvt_label + 列自有 PVT var + 手改覆盖 + models`;未托管列 = 从 Maestro 反推、不归任何 mode 的列。 |
| **cornermodel** | 一个 testbench 的整套 corner 模型 = `modes + columns`,序列化为一个 `.cornermodel.json` sidecar。 |

### 2.2 物化:一列 corner 的最终 var 集

对托管列 `X`、其 mode `M`,物化后的 corner 行 var 集按下式合成:

```
materialized_vars(X) = ( M.vars  覆盖以  X.overrides )  ∪  X.pvt_vars
materialized_models(X) = X.models
```

不变式(加载期校验,违反 = load error):
- `X.overrides` 的键 **必须是 `M.vars` 键的子集** —— 只能覆盖模式管理的 var。
- `X.pvt_vars` 的键 **必须与 `M.vars` 的键不相交** —— PVT var 与寄存器 var 不得同名。

合成结果是一个 Phase 2 **union row**(§3.3 of `phase2_pvt_union_spec.md`),交给 Phase 2
explode 引擎。`M.vars` / `overrides` 值为标量;`pvt_vars` 值可为标量或 sweep 数组
(Phase 2 语义),`models.section` 可 sweep。

### 2.3 全局编辑(痛点 b 的核心)

改 `M.vars["d_en_dummy"]` `"1"→"0"`:对每个 `mode == M` 的列 `X`——
- `d_en_dummy ∉ X.overrides` → 物化值即时变 `"0"`(同步)。
- `d_en_dummy ∈ X.overrides` → 保留 override 值;若 `override 值 ≠ "0"` → 该格**标红**
  (D1,见 §6)。

模式是唯一数据源:这一条就是 Stage 1 要兑现的承诺。

### 2.4 自动命名

托管列名 = 自动派生,**不落盘存**(避免"名字存了两处会漂移"):

```
col_name(X) = X.alias            若 X.alias 非空
            = X.mode + "_" + X.pvt_label   否则
```

- `mode` 名:`^[A-Za-z][A-Za-z0-9_]*$`(首字母,Maestro 标识符规则)。
- `pvt_label`:`^[A-Za-z0-9_]+$`。派生名 `mode_pvtlabel` 自然满足 Maestro 规则。
- `alias`:`^[A-Za-z][A-Za-z0-9_]*$`,设了就完全顶替派生名。
- 重命名 mode → 所有该 mode 托管列的 `col_name` 自动重新派生(名字也享受单一数据源)。
- 未托管列没有 mode,名字存在显式 `name` 字段(§3.3)。
- 两列派生出同名 → load error(catches 命名碰撞)。

---

## 3. Sidecar 文件格式 `.cornermodel.json`

### 3.1 位置与命名

| 项 | 规则 |
|---|---|
| 扩展名 | `.cornermodel.json`(两段式,`find -name '*.cornermodel.json'` 可用,沿用 `.union.json` 惯例) |
| 工程目录 | `<cornerModelsDir>/`,`.pvtproject` 新增可选字段,默认 `./corner_models`(相对 `.pvtproject` 所在目录解析)。**附加键,不 bump `.pvtproject:schema_version`**;需对 `docs/schema.md` §1 做附加更新。 |
| 一文件一 model | 文件 basename(去 `.cornermodel.json`)必须等于内部 `name` 字段,否则 load error。 |

### 3.2 顶层结构

```json
{
  "_doc": "...",
  "cornermodel_schema_version": 1,
  "name": "lo_corners",
  "project": "1AXX",
  "testbench_id": "sim_yusheng/Test/maestro",
  "modes": {
    "BT_2G_RX": {
      "vars": { "d_en_dummy": "0", "div_sel": "2" },
      "_doc": "BT 2.4G receive path register config"
    }
  },
  "columns": [ { ...列对象... } ]
}
```

| 字段 | 类型 | 必需 | 说明 |
|---|---|---|---|
| `cornermodel_schema_version` | int | 是 | Stage 1 固定为 `1`。 |
| `name` | str | 是 | `^[a-z0-9_-]+$`,等于文件 basename。 |
| `project` | str | 是 | 必须等于所属 `.pvtproject:project`,否则 load error。 |
| `testbench_id` | str | 是 | `lib/cell/view`。 |
| `modes` | object[str → mode] | 是 | mode 名 → mode 对象。可为空 `{}`(全是未托管列的退化情形)。 |
| `columns` | array[列对象] | 是 | 一个或多个列。空数组 = load error。 |
| `_doc` | str \| object | 否 | 内联文档,loader 忽略。 |

### 3.3 mode 对象与列对象

**mode 对象:**

| 字段 | 类型 | 必需 | 说明 |
|---|---|---|---|
| `vars` | object[str → str] | 是 | 寄存器 var → **标量**值。非空。值为非字符串/数组 = load error。 |
| `_doc` | str \| object | 否 | loader 忽略。 |

**列对象** —— 由 `mode` 是否为 `null` 区分两类:

| 字段 | 类型 | 托管列 | 未托管列 | 说明 |
|---|---|---|---|---|
| `mode` | str \| null | 必需,引用 `modes` 的键 | 必需,`null` | 引用不存在的 mode = load error。 |
| `pvt_label` | str | 必需 | 不允许 | `^[A-Za-z0-9_]+$`。 |
| `name` | str | 不允许 | 必需 | 未托管列的显式 Maestro 行名。 |
| `alias` | str \| null | 可选 | 不允许 | 顶替派生名。 |
| `enabled` | bool | 必需 | 必需 | 对应 union row 的 `enabled`(见 fixture)。 |
| `pvt_vars` | object[str → (str\|array[str])] | 可选,默认 `{}` | 必需 | 列自有 var(温度/电压等)。 |
| `overrides` | object[str → str] | 可选,默认 `{}` | 不允许 | 手改覆盖,键 ⊆ `mode.vars` 键。 |
| `models` | array[model entry] | 可选,默认 `[]` | 可选,默认 `[]` | Phase 2 model entry 格式(§3.3 phase2 spec)。 |
| `_doc` | str \| object | 否 | 否 | loader 忽略。 |

未托管列没有 mode,直接存物化后的 `pvt_vars` + `models`(它本身就是反推产物,见 §6)。

### 3.4 与 Phase 2 union 的关系(plan D2 "union 下沉")

`.cornermodel.json` 是**真相**;`.union.json` **下沉为底层序列化/交换格式**。物化路径:

```
.cornermodel.json  ──materialize──▶  union 对象(内存)  ──Phase 2 explode──▶  corners CSV / SKILL push  ──▶  Maestro
```

- 物化产物是合法 union 对象(每列 → 一个 union row,§2.2)。**默认只在内存中存在**,
  不落盘 —— cornermodel 才是落盘真相,避免双真相漂移。
- `pvt corner build`(§8)可显式把物化 union 写盘,供调试 / Phase 2 CLI 复用。
- explode、CSV、push、pull 全部复用 Phase 2 既有实现(`simkit.union` / `corners_csv` /
  `skill_bridge`),Stage 1 不重写。

---

## 4. Maestro 双向 round-trip

沿用 Phase 2 §4 的双向、Phase 4 的 divergence-strip 哲学。**cornermodel = 真相,
Maestro corner 表 = 展开产物。**

### 4.1 Push(cornermodel → Maestro)

1. 物化每一列 → union 对象。托管列按 §2.2 合成;未托管列按存盘 `pvt_vars`+`models` 原样。
2. union → Phase 2 explode → corners CSV / SKILL push。
3. **未托管列原样推送,绝不静默删除**(plan §6)。

### 4.2 Pull(Maestro → cornermodel)—— reconciliation

pull 不直接覆盖 cornermodel,走对账(§6)。详见 §6。

### 4.3 归属是被 authored 出来的

`模式 → 列` 归属、`pvt_label`、`overrides` 的拆分**不靠名字前缀猜**(plan §6:前缀匹配
脆弱)。归属只来自:用户在 GUI 里显式建模式、建列、收编未托管列。pull 只更新已托管列的
**值**,不改变**归属结构**。

---

## 5. 加载期校验

loader 在解析 `.cornermodel.json` 时强制(违反 = load error,定位到具体列/mode):

1. `name` == 文件 basename;`project` == `.pvtproject:project`。
2. `cornermodel_schema_version == 1`,否则 `CornerModelSchemaVersionError`。
3. 每个 mode `vars` 非空、值全为标量字符串。
4. 托管列:`mode` 引用存在;`overrides` 键 ⊆ 该 mode `vars` 键;`pvt_vars` 键 ∩
   该 mode `vars` 键 = ∅(§2.2 不变式)。
5. 未托管列:有 `name`,无 `mode`/`pvt_label`/`alias`/`overrides`。
6. 全部列的有效名(§2.4)互不相同。
7. 每列物化后满足 Phase 2 "vars 或 models 至少一非空"。

> 加载期文件存在性校验(`.s5p` 等)、复合轴完整性 → Stage 5,本文不做。

---

## 6. 托管 / 未托管 reconciliation(plan §6)

### 6.1 三类列

pull 把 Maestro 当前 corner 表的每一行,按名字匹配到 cornermodel:

| 情形 | 处理 |
|---|---|
| 匹配某**托管列**的有效名 | 对账该列的值(下 §6.2)。 |
| 匹配某**未托管列**的 `name` | 更新该未托管列存盘的 `pvt_vars`/`models`。 |
| 谁都不匹配(foreign) | 新增为**未托管列**,GUI 置灰 + badge。 |

### 6.2 托管列对账(复用 Phase 4 divergence-strip)

托管列的物化值 vs Maestro 拉回值不一致时,**不静默覆盖**,弹 divergence-strip,
逐 var 给用户三选一(沿用 `show_diff` / `pull_overrides` / `keep_sidecar` 模式):
- **接受 Maestro 值** → 若该 var 属 mode 管理 → 写入该列 `overrides`(成为手改);
  若属 `pvt_vars` → 直接更新 `pvt_vars`。
- **保留 cornermodel 值** → 不变(下次 push 会把 cornermodel 值推回 Maestro)。

### 6.3 未托管列的收编

用户对一个未托管/foreign 列可二选一:
- **收编**:指定归入某 mode → 工具把该列的 var 三向拆分:
  - 与 mode `vars` **同名同值** → 该 var 由 mode 接管,从列里移除;
  - 与 mode `vars` **同名异值** → 进 `overrides`(标红);
  - 不在 mode `vars` 里 → 留作 `pvt_vars`。
  用户给一个 `pvt_label`,列转为托管列,写进 cornermodel。拆分结果用 divergence-strip
  式预览,用户确认后才落盘。
- **留作未托管**:保持 `mode: null`,照常显示、push 时原样保留。

### 6.4 D1 标红规则(Stage 1 子集)

`手改 > 模式base` 两层。某托管列某格**标红** ⟺ `var ∈ column.overrides` 且
`overrides[var] ≠ mode.vars[var]` —— 即该列对此 var 有手改、且与当前模式 base 不一致。
含义:这一格故意脱离了模式,而模式 base 现在又变了。绝不静默用 base 覆盖手改。

> D2(变体存绝对值 + 继承)、D3(解绑冻结)涉及变体/模板,Stage 3/2 引入,本文不实现。

---

## 7. GUI —— Corner Manager 视图

布局保持像 Cadence 原生 corner manager(用户硬要求):变量为**行**、corner 为**列**的
表格 + 左侧 corner/模式清单。Stage 1 在 Phase 4 既有 `CornersEditor` 之上扩展。

### 7.1 新增 view 模块

`python/simkit/gui/views/corner_manager.py` —— 新 view。布局:

- **左:模式面板** —— 列出所有 mode;选中 mode → 下方显示其寄存器 var 表(可编辑)。
- **中:corner 表** —— 行=var,列=corner;列头按 mode 分组着色;托管列的 mode 管理格
  显示 mode 值,手改格显示 override 值,标红格按 §6.4。
- **顶:Pull / Push 按钮**(复用 Phase 4 corners pull/push,经 §3.4 物化)。

### 7.2 交互

| 动作 | 行为 |
|---|---|
| 模式面板里改一个寄存器值 | 全局编辑:该 mode 所有托管列即时同步(§2.3),标红格即时重算。 |
| 在 corner 表里手改一个 mode 管理格 | 在该列 `overrides` 写入,按 §6.4 判断是否标红。 |
| 新建模式 | 输入 mode 名 + 初始寄存器 var。 |
| 新建列 | 选 mode + 输入 `pvt_label` + 填 PVT var/section。 |
| foreign 列右键 | "收编到模式…" / "留作未托管"(§6.3)。 |
| 未托管列 | 置灰 + badge,不归任何模式,push 不删。 |

### 7.3 M2 合规

`corner_manager.py` 是 Phase 5 新 view,**不在** `test_view_coverage.py` 的
`_GRANDFATHERED` 列表里 → 必须配 `tests/gui/test_corner_manager.py`,内含至少一个
`def test_*render*` 函数,断言渲染几何(`rowHeight()` / `sectionSize` /
`visibleRegion`)。否则整套测试 fail。这是流程护栏 M2 在 Phase 5 的第一次落点。

---

## 8. CLI 表面

新增 `pvt corner-model` 子命令组(`pvt corner` 已被 Phase 2 占用):

| 命令 | 作用 | 离线 | Stage 1 |
|---|---|---|---|
| `pvt corner-model build <name>.cornermodel.json [--out <path>]` | 校验 + 物化成 union 写盘(默认 `<name>.union.json` 同目录) | 是 | ✅ |
| `pvt corner-model explode <name>.cornermodel.json [--json]` | 物化 + Phase 2 explode,打印子 corner | 是 | ✅ |
| `pvt corner-model push <name>.cornermodel.json` | 物化 → push 到 live session | 否 | 延后 |
| `pvt corner-model pull <name>.cornermodel.json` | 从 Maestro 拉回,走 §6 对账 | 否 | 延后 |

GUI 是 Stage 1 的首要入口(记忆:用户从未用过 CLI);CLI 表面为脚本/调试,不作 dogfood
门槛。**Stage 1 只交付离线可测的 `build` / `explode`**;`push` / `pull` 的 live 路径由
GUI 承担(§7),CLI 的 push/pull 留待有 live session 能真机验证后再补 —— 不预先 ship
未经验证的 bridge 代码(护栏 M4)。

---

## 9. Python 模块布局

| 模块 | 职责 |
|---|---|
| `python/simkit/corner_model.py` | cornermodel 数据类 + `.cornermodel.json` load/save + §5 校验 + §2.2 物化成 union 对象 + §6 对账逻辑 |
| `python/simkit/gui/views/corner_manager.py` | Corner Manager view(§7) |
| `python/simkit/gui/corner_model_table.py` | `QAbstractTableModel` —— 行=var/列=corner,承载标红/分组着色 |
| `python/simkit/cli/...` | `pvt corner-model` 子命令(§8) |

复用:`simkit.union`(explode)、`simkit.corners_csv`、`simkit.skill_bridge`(push/pull)、
Phase 4 divergence-strip 组件。**不重写** Phase 2 explode 引擎。

---

## 10. 验收门槛 —— dogfood gate

按 `corner_manager_plan.md §4`:不是"测试数达标",而是一次真实 Maestro round-trip。

**Stage 1 dogfood gate**(plan §3 Stage 1):
1. 在 LO 系统(live `sim_yusheng/Test/maestro` 或用户真实 LO workarea)建一个
   `BT_2G_RX` 模式。
2. 生成几列 corner(`BT_2G_RX_TT` / `BT_2G_RX_SS_1` / …)。
3. 改一个寄存器 `d_en_dummy` → 验证所有 `BT_2G_RX` 托管列**即时同步**。
4. push 回 Maestro,确认 corner 表正确。
5. 验证 reconciliation:在 Maestro 原生 corner manager 里 "create corner copy" 造一个
   foreign 列 → pull → 确认它被标为未托管、push 不被删除。

Python 全绿 + SKILL Tier-1 全绿 是必要条件,但门槛是上面这条 round-trip。

---

## 11. 流程护栏合规(`docs/dispatch_mandates.md`)

Stage 1 是新护栏下交付的第一个特性,逐条兑现:

| Mandate | Stage 1 落点 |
|---|---|
| **M1** live fixture | `corner_model.py` 的物化↔union↔Maestro pull/push 测试,fixture 必须来自真机 pull,存 `tests/fixtures/live/`,禁手写 dict。Stage 1 实现期需真机 pull 一份 corner 表落盘。 |
| **M2** view 渲染测试 | `corner_manager.py` 必须配 `test_corner_manager.py::test_*render*`,否则 `test_view_coverage.py` 硬 fail(§7.3)。 |
| **M3** controller connect 测试 | 若 Stage 1 引入接 Qt 信号的 controller(如模式编辑/全局同步 controller),必须有实例化 + `.connect()` 路径测试。 |
| **M4** DoD 清单 | 每个特性 "done" 前填 DoD 清单进 handoff;接 SKILL/bridge 的路径若未真机验证须显式标 "NOT live-verified"。 |

agent dispatch 时,orchestrator 把 `dispatch_mandates.md §2` 注入块按 agent 触碰的层
贴进 prompt(subagent 冷启动看不到记忆)。

---

## 12. 待澄清 / 延后

| # | 项 | 去向 |
|---|---|---|
| 1 | mode var 取 sweep 值(寄存器也想扫) | Stage 1 标量;若 dogfood 暴露需求,Stage 2 随 PVT sweep 一并考虑 |
| 2 | mode 管理 models/section | Stage 1 不管,section 属列自有 PVT |
| 3 | PVT 模板 / 聚合 / 复合轴 | Stage 2 |
| 4 | 变体 + D2/D3 + 三层回落链 | Stage 3 |
| 5 | 组合/运行集、列筛选、行筛选、行拖拽、加载文件校验、跨工程模板库 | Stage 4 / 5 |

---

## 13. 版本策略

- `cornermodel_schema_version` 每个 sidecar 必带,Stage 1 起为 `1`。loader 严格
  `== 1`,未知 = `CornerModelSchemaVersionError`。
- Stage 2–5 给 `.cornermodel.json` 加 `pvt_templates` / `template_bindings` /
  `variants` / `sets` / `correlated_axes` 等键 —— **附加键不 bump 版本**(沿用 Phase 1
  unknown-key 政策);破坏性变更才 bump 并在 `DECISIONS.md` 记迁移说明。
- `.pvtproject` 新增 `cornerModelsDir` 是附加可选键,不 bump `.pvtproject:schema_version`。
