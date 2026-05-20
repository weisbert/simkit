# SimKit Corner Manager — Phase 5 Stage 2 §1 规格

> 日期:2026-05-20。Stage 2 §1 冻结规格,依据 `corner_manager_plan.md` §3 Stage 2。
> 解决 **痛点 a**(PVT corner 按模式重复设计 N 次)+ **痛点 h**(VCO 复合轴)。
> 建立在 Stage 1(`phase5_stage1_spec.md`)之上,沿用其护栏(`dispatch_mandates.md`)。

---

## 1. 范围

| 范围内 | 范围外(延后) |
|---|---|
| PVT 模板:可复用的列清单;套到模式自动生成列 | 变体 → Stage 3 |
| 模板持续绑定 + 解绑(D3 冻结) | 运行集 / 列筛选 → Stage 4 |
| 聚合 corner:一列内含多个 sweep 点 | 行筛选 / 校验 → Stage 5 |
| 复合轴 correlated axis:绑定 var 捆,叉乘算一个轴 | |

---

## 2. 数据模型(在 Stage 1 之上新增)

### 2.1 复合轴 correlated axis(痛点 h)

一组**必须同步变化**的 var,叉乘时算**一个轴**。

```json
"correlated_axes": {
  "proc_ct": {
    "members": ["process", "CT"],
    "tuples": [
      {"label": "tt", "values": {"process": "tt", "CT": "100"}},
      {"label": "ff", "values": {"process": "ff", "CT": "88"}},
      {"label": "ss", "values": {"process": "ss", "CT": "120"}}
    ]
  }
}
```

| 字段 | 说明 |
|---|---|
| `members` | 该轴绑定的 var 名清单(≥1)。 |
| `tuples` | 轴上的点;每个 `values` 的键**必须恰好等于** `members`(load error 否则)。 |
| `tuples[].label` | 该点的短标签,用于物化命名;`^[A-Za-z0-9_]+$`,轴内唯一。 |

复合轴在 cornermodel 顶层定义,可被多个模板/列引用 —— 跨工程复用留 Stage 5。

### 2.2 PVT 模板(痛点 a)

可复用的**列清单**;套到模式 → 每个 spec 生成一列。

```json
"pvt_templates": {
  "rx_full": {
    "columns": [
      {"pvt_label": "TT",  "pvt_vars": {"temperature": "55",  "VDD": "0.9"}},
      {"pvt_label": "SS_1","pvt_vars": {"temperature": "125", "VDD": "0.85"}},
      {"pvt_label": "PVT_45",
       "pvt_vars": {"VDD": ["0.9", "0.85", "0.95"]},
       "correlated_axes": ["proc_ct", "temp_s5p"]}
    ]
  }
}
```

每个模板列 `TemplateColumn`:`pvt_label`(必需)、`pvt_vars`(独立 PVT 轴,标量或
sweep 数组,默认 `{}`)、`correlated_axes`(引用的复合轴名清单,默认 `[]`)。

- 全标量 + 无复合轴 → 单点列(如 `TT`)。
- 含 sweep 或复合轴 → **聚合列**(一列多点,如 `PVT_45`)。

### 2.3 绑定 binding

`template_bindings`:`[{"mode": "BT_2G_RX", "template": "rx_full"}]`。

- 绑定后,该模式的列 = 模板每个 `TemplateColumn` 物化出的一列(托管列,带
  provenance `template` = 模板名)。
- **持续绑定**:模板列变化 → 重新生成绑定模式的列。
- **解绑(D3)**:`unbind` 后,已生成的列**冻结** —— provenance `template` 清空,
  变成 Stage 1 风格的普通托管列,值保留,不删。

### 2.4 Column 扩展

Stage 1 的 `Column` 新增可选字段:
- `correlated_axes: tuple[str,...]` —— 该列引用的复合轴名(默认空)。
- `template: str | None` —— provenance:该列由哪个模板生成(解绑后清空)。

约束:`correlated_axes` 引用的轴必须存在;轴 `members` 与该列 `pvt_vars` 键、
`mode.vars` 键三者**两两不相交**(一个 var 只能属于一个轴/层)。

---

## 3. 物化(含复合轴)

Stage 1:一列 → 一个 union row。**Stage 2:一列可 → 多个 union row。**

`materialize_column_rows(model, column) -> list[UnionRow]`:

1. 设列有复合轴 `A1…Ak`(长度 `m1…mk`)、独立 sweep pvt_vars。
2. 对复合轴做叉乘:`m1 × … × mk` 个组合。每个组合 = 选定每轴一个 tuple。
3. 每个组合 → 一个 union row:
   - row vars = `mode.vars`(被 overrides 覆盖)∪ 列标量 pvt_vars ∪ 选定 tuple 的
     `values` ∪ 独立 sweep pvt_vars(保留为 Phase 2 sweep)。
   - row 名 = `<列有效名>__<tuple1.label>_<tuple2.label>…`;无复合轴时退回 Stage 1
     的单 row,名 = 列有效名(不加后缀)。
4. 独立 sweep 仍交 Phase 2 `explode` 在 row 内展开成子 corner。

净效果:`[proc_ct]×[temp_s5p]×[VDD]` = 3×3 复合组合 → 9 个 union row,每行 VDD
sweep(3)→ Phase 2 explode → 9×... 实际 45 点(痛点 h:45 而非 405)。

复合轴**对 Maestro 透明** —— Maestro 无复合概念,故复合叉乘在 simkit 侧展开成多个
corner row;独立 sweep 仍用 Maestro 原生 sweep。GUI 里该聚合列仍显示为**一列**
(带点数 badge),`materialize` 时才摊开。

`materialize(model)` 遍历所有列、拼接 `materialize_column_rows`,产出一个 `Union`。

---

## 4. 关键操作

| 操作 | 行为 |
|---|---|
| `apply_template(model, mode, template)` | 为 `mode` 按模板每个 `TemplateColumn` 生成托管列(provenance `template`),登记 binding。已存在同名列 → 复用,不重复。 |
| `unbind_template(model, mode, template)` | 删 binding;生成列的 `template` provenance 清空(D3 冻结,值保留)。 |
| 改模板列 | 对所有绑定该模板的模式重新 `apply`(持续绑定)。 |
| 全局编辑(Stage 1) | 不变 —— 模板生成的列仍是托管列,模式寄存器仍单一数据源。 |

覆盖优先级 Stage 2 子集:`手改 > 模式base/模板`。模板只供 PVT 轴,寄存器仍归模式;
D1 标红规则不变。变体(D2)仍属 Stage 3。

---

## 5. GUI(在 Stage 1 CornerManagerView 之上)

- 左面板新增 **模板区** + **复合轴区**(列表)。
- 模板区:选模板 → 看其 `TemplateColumn` 清单;"套用到模式"动作(选模式 → 生成列)。
- 复合轴区:看/编辑 tuple 表。
- corner 表:聚合列列头加点数 badge(如 `PVT_45 ·45`);模板生成列列头标 provenance。
- 解绑动作:右键模式 → "解绑模板"。

M2:CornerManagerView 已有 `test_*render*`;新增子部件(模板区表格)若独立成 view
模块才触发 M2,内嵌则随主 view 测试覆盖。

---

## 6. 校验(加载期,在 Stage 1 §5 之上新增)

1. 复合轴每个 tuple 的 `values` 键 == `members`;`label` 轴内唯一、合法。
2. 模板/列引用的复合轴、模板名必须存在。
3. 列的 `correlated_axes` members ∩ `pvt_vars` 键 ∩ `mode.vars` 键 两两不相交。
4. `template_bindings` 的 mode/template 必须存在。
5. `cornermodel_schema_version` 仍为 `1`(`correlated_axes`/`pvt_templates`/
   `template_bindings` 是附加键,不 bump —— 沿用 Stage 1 §13)。

---

## 7. 验收 —— Stage 2 dogfood gate(plan §3)

1. 建一个 PVT 模板,一键套到 LO 的多个模式,确认每模式自动生成对应列。
2. VCO 复合轴:`[工艺角+CT] × [电压] × [温度+s5p]` 物化 = **45 点**(非 405)。
3. 改模板一列 → 绑定的模式同步;解绑一个模式 → 该模式列冻结保留。
4. push 回 Maestro,确认聚合列摊开成正确的 corner row。

Python 全绿 + Tier-1 全绿是必要条件,门槛是这条 round-trip。**本 session 无 Virtuoso
→ NOT live-verified,dogfood 入清单交用户。**
