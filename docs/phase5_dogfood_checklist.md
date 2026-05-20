# Phase 5 Corner Manager — Dogfood 验收清单

> 日期:2026-05-20。Phase 5 的全部 5 个 Stage 已**建成 + 离线测试全覆盖**
> (Python 1882 passed)。但 `corner_manager_plan.md §4` 把每个 Stage 的"完成"
> 定义为一次真实 Maestro round-trip dogfood —— **那一步需要你 + 运行中的
> Virtuoso,Claude 无法代做**。本清单是交给你执行的验收步骤。
>
> 每个 Stage 的 §1 spec 见 `docs/phase5_stage{1..5}_spec.md`,§7/§10 的 gate
> 即下列各节。

---

## 准备

1. 在你的工程里放一个 `.cornermodel.json`(下面是最小示例,文件名必须
   `<name>.cornermodel.json`,`name` 字段 == 文件 basename):

```json
{
  "cornermodel_schema_version": 1,
  "name": "lo_corners",
  "project": "<你的 .pvtproject:project>",
  "testbench_id": "<lib/cell/view>",
  "modes": {"BT_2G_RX": {"vars": {"d_en_dummy": "1", "d_div12_en": "1"}}},
  "columns": [
    {"mode": "BT_2G_RX", "pvt_label": "TT", "enabled": true,
     "pvt_vars": {"temperature": "55", "VDD": "0.9"},
     "models": [{"file": "rf018.scs", "section": "tt"}]}
  ]
}
```

2. 离线先验证(无需 Virtuoso):
   - `pvt corner-model explode lo_corners.cornermodel.json` —— 应打印子 corner。
   - `pvt corner-model build lo_corners.cornermodel.json` —— 应生成 `.union.json`。
3. 打开 GUI:`pvt gui --module <你的 .pvtproject>`,菜单 **File → Open Corner
   Model…** 选这个 `.cornermodel.json`。右侧出现 "Corner: lo_corners" 标签页。

---

## Stage 1 — 模式 + 全局编辑(痛点 b)

1. GUI 左侧"模式"面板选 `BT_2G_RX`,在寄存器表里把 `d_en_dummy` 从 `1` 改成 `0`。
2. **验证:** 所有 `BT_2G_RX` 列的 `d_en_dummy` 格即时变 `0`(单一数据源同步)。
3. 在 corner 表里手改某列的 `d_en_dummy` → 该格**标红**(D1),且模式面板再改时
   该格不被覆盖。
4. 点 **Push** → 确认 Maestro corner 表正确。
5. 在 Maestro 原生 corner manager 里 `create corner copy` 造一个 foreign 列 →
   (pull 路径见下注)→ 确认它被标为未托管、push 不删它。

> 注:GUI 的 corner-model **pull** 尚未接线(spec §6 reconciliation 的交互式
> 回填留待后续)。当前从 Maestro 拉 corner 请用 **Corners** 标签页的 Pull;
> cornermodel 侧的 `classify_pull` / `adopt_column` 数据层已实现并测试。

---

## Stage 2 — PVT 模板 + 聚合 + 复合轴(痛点 a + h)

1. 在 `.cornermodel.json` 里 author 一个 `pvt_templates` + `correlated_axes`
   (格式见 `phase5_stage2_spec.md` §2),重新 Open Corner Model。
2. 左侧"PVT 模板"区选模板 → **套用到模式** → 选一个模式 → 确认自动生成对应列。
3. **VCO 复合轴验证:** 一个聚合列 `[工艺角+CT] × [电压] × [温度+s5p]` 的列头
   应显示 `·45`(45 点,而非 405)。
4. 改模板列后重新套用 → 绑定的模式同步;右键/解绑 → 该模式列**冻结保留**(D3)。
5. Push → 确认聚合列摊开成正确的多个 corner row。

---

## Stage 3 — 变体 + 三层回落链(痛点 c)

1. 左侧"变体"区 → **新建变体** → base_mode `BT_2G_RX`、名字 `BT_2G_RX_PN`、
   覆盖 `d_div12_en=0`。
2. 把 PVT 模板套用到该变体 → 生成 `BT_2G_RX_PN_TT` 等列。
3. **验证回落链:**
   - 变体列某寄存器手改 → 标红(`手改 > 变体`)。
   - 改 base 模式一个**变体未覆盖**的寄存器 → 变体列跟着变(D2 继承)。
   - 改 base 一个**变体已覆盖**的寄存器 → 变体列不变(D2 绝对值钉死)。
4. Push。

---

## Stage 4 — 运行集 + 列筛选(痛点 d + f)

1. 左侧"运行集"区 → **新建运行集** `All_Mode_TT`(勾选各模式的 TT 列)、再建
   `BT_2G_RX_Fast_check`。
2. 选一个运行集 → **切换到此运行集** → 验证只有该集的列 `enabled`。
3. 顶部"列筛选"框输入 `BT_2G_RX` → 确认只显示名字含该串的列;**筛选到选中
   运行集** → 只显示该集的列;两者叠加。
4. Push → 确认 Maestro 的 Enable 列正确。

---

## Stage 5 — 行筛选 / 行拖拽 / 校验 / 模板库(痛点 e + g)

1. 在一个 >100 行的 corner 上,顶部"行筛选"框输入 `ldo* or div12` → 确认只显示
   匹配的变量行。
2. 拖动 corner 表的竖向行表头,把一个变量行拖到第 3 行 → 保存重载后行序保留。
3. 故意让某复合轴引用一个不存在的 `.s5p` → 底部"校验"状态条应报
   `missing_file`。
4. **导出模板库** → 写出 `.cornerlib.json`;在另一个工程 **导入模板库** →
   模板 + 复合轴可用。

---

## Stage 6 — PVT Profile 语义映射层(痛点:模板跨工程不可移植)

> Stage 6 在 Stage 1–5 之后补的一层。spec:`docs/phase5_stage6_spec.md`。
> 冒烟材料:`/tmp/rf018.pvtprofile.json`(含拆分角 `ssMOS_ffRC` + 温飘档 `drift`)
> + `/tmp/smoke6.cornermodel.json`(列写语义 `axis_levels`,绑定 `pvt_profile: rf018`)。

1. 离线先验(已验证):
   `pvt corner-model explode /tmp/smoke6.cornermodel.json --profile /tmp/rf018.pvtprofile.json`
   → `BT_2G_RX_TT` 应解析出 `LDO_VSET=20, temperature=55, model.section=tt`。
2. GUI:**File → Open Corner Model…** 选 `/tmp/smoke6.cornermodel.json`
   (profile 会自动从同目录的 `rf018.pvtprofile.json` 加载)。
3. **看:** 左侧顶部"PVT Profile"面板显示 `profile: rf018` + process/voltage/
   temperature 三轴及其档位;corner 表 `BT_2G_RX_TT` 列已解析出 `LDO_VSET` /
   `temperature` / section。
4. 选模板 `rx_full` → 套用到 `BT_2G_RX` → 生成三列;`split` 列(`ssMOS_ffRC`)
   应解析成两个模型 `mos.scs:ss` + `rc.scs:ff`;`drift` 列点数 badge 显示温飘的
   多点(`·6`)。
5. **跨工程验证(真正的卖点):** 把 `rf018.pvtprofile.json` 换成另一工程的
   profile(process/voltage 名值不同),同一个 cornermodel 重新打开 → 同一套
   `axis_levels` 解析出新工程的具体值,模板不用改。

---

## 已知未接线项(需后续窗口 + Virtuoso)

| 项 | 状态 |
|---|---|
| corner-model GUI **pull** + 交互式 reconciliation 回填 | 数据层(`classify_pull`/`adopt_column`)已实现测试;GUI 接线 deferred |
| `pvt corner-model push` / `pull` CLI | deferred(spec §8;GUI 承担 live;CLI 待真机验证后补) |
| 多个 `.cornermodel.json` 同时打开的标签页管理 | 当前每次 Open 新增一个标签页,无去重 |
| `.pvtproject` 自动发现 `cornerModelsDir` 下的 cornermodel + 左树分组 | deferred(当前靠 File → Open Corner Model 显式打开) |

这些都不阻塞上述 5 个 Stage 的 dogfood —— push 路径已接线,pull 暂走 Corners 标签页。
