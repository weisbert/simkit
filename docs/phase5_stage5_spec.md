# SimKit Corner Manager — Phase 5 Stage 5 §1 规格

> 日期:2026-05-20。Stage 5 §1 冻结规格,依据 `corner_manager_plan.md` §3 Stage 5。
> 解决 **痛点 e**(变量行多难查找)+ **痛点 g**(行无法拖动)+ 补充缺口 #4(加载校验)
> #5(跨工程模板库)。Phase 5 的收尾阶段。

---

## 1. 范围

| 范围内 | 实现层 |
|---|---|
| 行筛选:按变量名,支持 and / or / 通配 `*` | GUI(`setRowHidden`) |
| 行拖拽重排 + 持久化行序 | `var_order` 字段 + 表头可拖动 |
| 加载校验:文件存在性、run-set 悬空引用 → 标红/告警 | `check_cornermodel` |
| 模板 / 复合轴跨工程复用库 `.cornerlib.json` | `import_library` / `export_library` |

---

## 2. 行序 var_order(痛点 g)

`CornerModel` 新增可选字段 `var_order: tuple[str,...]` —— 变量行的显式顺序。
- 在 `var_order` 里的变量按其顺序排在前;不在的按默认序(寄存器在前、PVT 在后,
  各自字母序)接在后面。
- 空 = 纯默认序(Stage 1 行为)。
- 表模型 `_rebuild` 按 `var_order` 排 `_var_rows`。
- GUI:corner 表竖向表头 `setSectionsMovable(True)` → 用户拖动行;`sectionMoved`
  → 计算新行序 → `set_var_order` 持久化 → cornermodel 重建。
- 操作:`set_var_order(model, ordered_vars)`。

---

## 3. 行筛选(痛点 e)

纯 GUI,不改 cornermodel。corner 表顶部筛选框输入表达式:
- 词 = 子串 或 `fnmatch` 通配(`LDO_*`)。
- `and` / `or` 组合(`or` 优先级低)。例:`ldo or div12`、`d_* and en`。
- 匹配变量名(行)→ 用 `QTableView.setRowHidden` 隐藏不匹配行。

---

## 4. 加载校验(缺口 #4)

`check_cornermodel(model, base_dir=None) -> list[CheckIssue]` —— 非阻塞软校验,
返回问题清单(GUI 标红/状态栏列出),不抛异常:

| 检查 | 问题 |
|---|---|
| 复合轴 tuple 值看起来是模型文件(`.s5p`/`.scs`/`.mod`)但磁盘不存在 | `missing_file` |
| run-set `columns` 引用的有效名在 cornermodel 中无对应列(加载期容忍的悬空引用) | `dangling_column` |
| 列的 `models[]._file_abs` 非空但文件不存在 | `missing_file` |

`CheckIssue(severity, code, where, message)`。`severity` ∈ `error` / `warning`。
复合轴 tuple 完整性(values 键 == members)等硬约束仍在加载期(Stage 2 §6)强制。

---

## 5. 跨工程模板库(缺口 #5)

模板 / 复合轴定义抽出成独立 sidecar `.cornerlib.json`,不绑死单个 testbench:

```json
{
  "cornerlib_schema_version": 1,
  "name": "rfic_std_lib",
  "correlated_axes": { ... },
  "pvt_templates": { ... }
}
```

- `load_library(path) -> CornerLibrary`。
- `export_library(model, name) -> CornerLibrary` —— 把当前 cornermodel 的
  `correlated_axes` + `pvt_templates` 抽出。
- `import_library(model, library) -> CornerModel` —— 合并进 cornermodel;名字冲突
  → error(不静默覆盖)。模板引用的复合轴一并带入。

`.cornerlib.json` 工程目录由 `.pvtproject` 可选键 `cornerLibsDir` 给出(默认
`./corner_libs`);跨工程复用 = 把库文件拷到别的工程或共享路径。

---

## 6. GUI(在 Stage 4 CornerManagerView 之上)

- corner 表顶部新增**行筛选框**。
- corner 表竖向表头可拖动(行重排)。
- 新增**校验状态条**:`check_cornermodel` 的问题数 + 点开看清单。
- 模板区新增 "导出库" / "导入库" 按钮。

---

## 7. 校验(加载期)

`var_order` 是字符串数组(附加键);`run_sets` 已 Stage 4 覆盖。schema 版本仍 `1`。
`.cornerlib.json` 有独立 `cornerlib_schema_version`,严格 `== 1`。

---

## 8. 验收 —— Stage 5 dogfood gate(plan §3)

1. 在 >100 行的 corner 上按变量名筛选(`ldo or div`),确认只显示匹配行。
2. 拖动一个变量行到第 3 行,保存重载后行序保留。
3. 故意让复合轴引用一个不存在的 `.s5p` → `check_cornermodel` 报 `missing_file`。
4. 导出模板库 → 在新工程导入 → 模板 + 复合轴可用。

**本 session 无 Virtuoso → NOT live-verified,dogfood 入清单。**
