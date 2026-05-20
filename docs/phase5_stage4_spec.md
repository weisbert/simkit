# SimKit Corner Manager — Phase 5 Stage 4 §1 规格

> 日期:2026-05-20。Stage 4 §1 冻结规格,依据 `corner_manager_plan.md` §3 Stage 4。
> 解决 **痛点 d**(模式多 corner 多难管理 —— 要"组合"概念)+ **痛点 f**(列筛选)。

---

## 1. 范围

| 范围内 | 范围外 |
|---|---|
| 运行集 run-set:命名的跨模式 corner 勾选清单,一键切换 | 行筛选 / 行拖拽 / 校验 / 跨工程库 → Stage 5 |
| 列筛选:按名字(保留 Cadence 行为)+ 按运行集 | |

---

## 2. 数据模型(新增)

### 2.1 运行集 run-set

一个命名的、跨模式的 corner 勾选清单。例:`All_Mode_TT` = 7 个模式的 TT 列;
`BT_2G_RX_Fast_check` = `BT_2G_RX_TT` / `_SS_1` / `_FF_1`。

```json
"run_sets": {
  "All_Mode_TT": {"columns": ["BT_2G_RX_TT", "BT_2G_TX_TT"]},
  "BT_2G_RX_Fast_check": {
    "columns": ["BT_2G_RX_TT", "BT_2G_RX_SS_1", "BT_2G_RX_FF_1"]
  }
}
```

| 字段 | 说明 |
|---|---|
| 键(运行集名) | `^[A-Za-z][A-Za-z0-9_]*$`,唯一。 |
| `columns` | 该运行集勾选的 corner **有效名**清单。允许引用尚不存在的列名(forward-compat,加载期只 warn 不 error)。 |

### 2.2 切换

"切换到运行集 S" = 把每个列的 `enabled` 设为 `有效名 ∈ S.columns`。运行集本身不改
cornermodel 结构,只批量改 `enabled`(`enabled` 字段 Stage 1 已有,push 时映射到
Maestro 的 Enable 列)。

---

## 3. 关键操作

| 操作 | 行为 |
|---|---|
| `add_run_set(model, name, columns)` | 新增运行集。 |
| `apply_run_set(model, set_name)` | 返回新 cornermodel:`column.enabled = 有效名 ∈ set.columns`,其余列 `enabled=False`。 |
| `run_set_membership(model, set_name)` | 返回该集勾选的列有效名集合(GUI 列筛选用)。 |

---

## 4. 列筛选(痛点 f)

纯 GUI 行为,不改 cornermodel:

- **按名字**:顶部筛选框输入子串 → 隐藏有效名不含该子串的列(保留 Cadence 的
  按名字筛选)。
- **按运行集**:选一个运行集 → 只显示该集的列。
- 两者可叠加。实现用 `QTableView.setColumnHidden`,不引入 proxy(列隐藏足够,
  且保持单元格编辑直达 source model)。

---

## 5. GUI(在 Stage 3 CornerManagerView 之上)

- 左面板新增 **运行集区**:列表 + "新建运行集" + "切换到此集"。
- 顶部新增 **列筛选框**(QLineEdit)+ "按运行集筛选" 下拉。
- "新建运行集"动作:输入名字 + 勾选当前列(多选对话框,或输入有效名清单)。
- corner 表列头:运行集切换后 `enabled=False` 的列保持显示但可视化区分
  (置灰列头),与 Stage 1 的 enabled 语义一致。

---

## 6. 校验(加载期)

1. 运行集名合法、唯一。
2. `columns` 是字符串数组;指向不存在的列名 → **warn 不 error**(forward-compat:
   运行集可能在列建好前先 author)。
3. schema 版本仍 `1`(`run_sets` 是附加键)。

---

## 7. 验收 —— Stage 4 dogfood gate(plan §3)

1. 建 `All_Mode_TT`、`BT_2G_RX_Fast_check` 两个运行集。
2. 切换:applies enabled → 验证只有该集的列 enabled。
3. 列筛选:按名字 `BT_2G_RX` 过滤、按运行集过滤,叠加。
4. push 回 Maestro,确认 enabled 列正确。

**本 session 无 Virtuoso → NOT live-verified,dogfood 入清单。**
