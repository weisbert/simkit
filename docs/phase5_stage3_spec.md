# SimKit Corner Manager — Phase 5 Stage 3 §1 规格

> 日期:2026-05-20。Stage 3 §1 冻结规格,依据 `corner_manager_plan.md` §3 Stage 3。
> 解决 **痛点 c**(给模式加变体测试要把 corner 重编一次)。建立在 Stage 1/2 之上。

---

## 1. 范围

| 范围内 | 范围外 |
|---|---|
| 变体 variant:模式的 diff-overlay | 运行集 / 列筛选 → Stage 4 |
| 三层覆盖回落链 `手改 > 变体 > 模式base/模板` | 行筛选 / 校验 → Stage 5 |
| D1 标红 / D2 绝对值+继承 / D3 解绑冻结 | |
| 变体是一等公民:可被 PVT 模板套用 | |

---

## 2. 数据模型(在 Stage 1/2 之上新增)

### 2.1 变体 variant

变体 = 某个模式的**差量覆盖层**。例:`BT_2G_RX` 模式跑 trans;要跑 PSS 时建变体
`BT_2G_RX_PN`,把 DIV12 相关寄存器关掉。

```json
"variants": {
  "BT_2G_RX_PN": {
    "base_mode": "BT_2G_RX",
    "vars": {"d_div12_en": "0", "d_div12_rst": "1"}
  }
}
```

| 字段 | 说明 |
|---|---|
| 键(变体名) | 全局唯一,`^[A-Za-z][A-Za-z0-9_]*$`。它是 corner 名的命名根。 |
| `base_mode` | 必需,引用一个已定义的模式。 |
| `vars` | 变体覆盖的寄存器 → **绝对值**(D2)。键**必须 ⊆ `base_mode.vars` 键** —— 变体只覆盖模式已有的寄存器,不引入新 var。 |

### 2.2 Column 扩展

`Column` 新增可选字段 `variant: str | None`。
- `variant` 设了:该列挂在变体上;`mode` 仍 = 该变体的 `base_mode`(一致性)。
- 有效名:`variant` 设了 → `<variant>_<pvt_label>`;否则沿用 Stage 1 的
  `<mode>_<pvt_label>`。`alias` 仍可顶替。

### 2.3 模板绑定扩展

`TemplateBinding` 新增可选 `variant: str | None`。变体也是一等公民,能套 PVT 模板
生成 `BT_2G_RX_PN_TT` / `BT_2G_RX_PN_SS_1` 等列。

---

## 3. 三层覆盖回落链(plan §2 已确认)

一个变体列对寄存器 var `X` 的物化值,按优先级:

```
1. column.overrides[X]      —— 手改(最高)
2. variant.vars[X]          —— 变体覆盖(若 X 被变体覆盖)
3. base_mode.vars[X]        —— 模式 base / 模板(最低)
```

非变体列退回 Stage 1 的两层 `手改 > 模式base`。PVT var 仍逐列(`pvt_vars`),复合轴
仍按 Stage 2。

**D1 标红:** 某格红 ⟺ `X ∈ overrides` 且 `overrides[X] ≠ 它下一层的值`(变体列下一层 =
`variant.vars.get(X, base.vars[X])`;非变体列下一层 = `base.vars[X]`)。模式 base 或
变体更新撞上手改 → 手改赢,标红,绝不静默覆盖。

**D2:** 变体对它**覆盖**的 var 存绝对值(钉死);**未覆盖**的 var 继承 base —— 即
base 改了,变体列未覆盖的 var 跟着变。

**D3:** 解绑模板(Stage 2 `unbind_template`)后生成列冻结 —— 已覆盖 Stage 2,变体列
同理。解绑变体本身不在 Stage 3 范围(变体一旦建立即长期存在;删变体 = 另一动作)。

---

## 4. 关键操作

| 操作 | 行为 |
|---|---|
| `add_variant(model, variant)` | 新增变体;校验 base_mode 存在、`vars` 键 ⊆ base 寄存器、名字唯一。 |
| `set_variant_var(model, variant, var, value)` | 改变体覆盖值;全局生效到所有该变体的列(未手改的格)。 |
| `apply_template(model, mode, template, variant=?)` | `variant` 给定 → 为变体生成列(provenance `template` + `variant`)。 |
| `set_mode_var`(Stage 1) | base 改动 → 变体列未覆盖 var 跟着变(D2 继承);被变体覆盖的 var 不受影响。 |

覆盖优先级、`set_column_override` 不变 —— 手改仍是最高层。

---

## 5. GUI(在 Stage 2 CornerManagerView 之上)

- 左面板模式区下新增 **变体区**:列出变体(显示 `名字 → base_mode`);选中变体显示其
  覆盖寄存器表(可编辑,= `set_variant_var`)。
- "新建变体"动作:选 base_mode + 变体名 + 覆盖 `var=value` 清单。
- 模板"套用"对话框:目标可选 模式 或 变体。
- corner 表:变体列列头标 provenance(`变体: BT_2G_RX_PN`);三层回落的标红规则
  §3 已统一,表模型 `is_cell_red` 按新链判定。

---

## 6. 校验(加载期,新增)

1. 每个变体 `base_mode` 存在;`vars` 键 ⊆ `base_mode.vars` 键;`vars` 值为标量。
2. 变体列:`variant` 引用存在;`column.mode == variant.base_mode`。
3. 变体列 `overrides` 键仍 ⊆ `base_mode.vars` 键(手改的是寄存器)。
4. `template_bindings` 的 `variant`(若有)必须存在。
5. schema 版本仍 `1`(`variants` 是附加键)。

---

## 7. 验收 —— Stage 3 dogfood gate(plan §3)

1. 给 `BT_2G_RX` 建变体 `BT_2G_RX_PN`,关掉 DIV12 寄存器。
2. 把 PVT 模板套到该变体 → 生成 `BT_2G_RX_PN_TT` 等列。
3. 验证回落链:变体列某寄存器手改 → 标红;改 base 未覆盖 var → 变体列继承;改 base
   已被变体覆盖 var → 变体列不变(D2)。
4. push 回 Maestro。

**本 session 无 Virtuoso → NOT live-verified,dogfood 入清单。**
