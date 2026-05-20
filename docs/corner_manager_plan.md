# SimKit Corner Manager — 建设计划

> 日期：2026-05-20。本文为**计划**,不是冻结的 §1 spec。需求依据见
> `docs/corner_manager_user_story.md`(痛点 a–h + 5 项补充缺口)。计划经用户确认后,
> 再按项目惯例写各阶段 §1 spec。

---

## 1. 定位

Corner manager 不是新起的子系统,是在两个已交付件之上加一层抽象:

- **Phase 2 PVT-union builder** —— 声明式描述一个 PVT、机械展开成 exploded corner 表。
  Corner manager 复用它的 explode 引擎,不重写。
- **Phase 4 GUI CornersEditor** —— 已有的 corner 表格编辑器。Corner manager 扩展它,
  布局保持和 Cadence corner manager 一致(用户硬要求)。

它解决的是 Phase 2 没覆盖的维度问题:**模式 / 变体 / 模板 / 组合**,以及 Phase 2 union
行无法表达的 **复合轴(correlated axis)**。

---

## 2. 需求模型(抽象一览)

| 抽象 | 含义 | 对应痛点 |
|---|---|---|
| **模式 mode** | 命名的寄存器配置集,**单一数据源**;改一次,所有引用它的 corner 列同步 | b |
| **PVT 模板** | 可复用的 PVT 点清单;与模式**持续绑定**,可解绑 | a |
| **复合轴 correlated axis** | 绑定在一起、必须同步变化的 var 捆(CT↔工艺角、s5p↔温度);叉乘时算**一个轴** | h |
| **聚合 corner** | 一个 corner 列内含多个 PVT 点(如 PVT_45 = 45 点)= 模板物化成单列 | a |
| **变体 variant** | 模式的差量覆盖(diff-overlay);本身是一等公民,可被模板套 | c |
| **组合 / 运行集 set** | 命名的、跨模式的 corner 勾选清单,可一键切换 | d |

**覆盖优先级(已与用户确认):** `手改 > 变体 > 模式base/模板`,带回落链。
- D1:模板/base 更新撞上手改 → 手改赢,**标红**冲突,绝不静默覆盖。
- D2:变体对它覆盖的 var 存绝对值(钉死),未覆盖的 var 继承 base。
- D3:解绑后已生成的列**冻结当前值**保留,不删。

**自动命名:** corner 名按 `模式_变体_PVT` 自动拼,允许加别名(alias)。

**Cadence 双向:** 能从 Maestro 拉现有 corner 反推模型,改完推回 —— 与 simkit 现有
pull/push 设计逻辑一致。

---

## 3. 分阶段建设

原则沿用项目惯例:**一个阶段一个端到端薄切片,有真实用户(LO 系统 / VCO)在 dogfood
验证**。每阶段都含"数据模型 + GUI + Cadence round-trip"三件套。

### Stage 1 —— 模式作为一等对象 + 全局编辑(痛点 b)

- 数据模型:`mode` = 命名寄存器配置集,单一数据源。
- corner 列 = `模式 × PVT点`,列里的寄存器格从 mode 取值。
- 自动命名规则 + 别名。
- Cadence 双向:从 Maestro 现有 corner 反推 mode,改完推回。
- **Dogfood gate:** LO 系统建一个 `BT_2G_RX` 模式 → 生成几列 → 改一个寄存器
  `d_en_dummy` → 验证所有 BT_2G_RX 列同步 → 推回 Maestro。

### Stage 2 —— PVT 模板 + 聚合 + 复合轴(痛点 a + h)

- PVT 模板:可复用 PVT 点清单;持续绑定 + 解绑。
- 复合轴:把绑定的 var 捆成单轴(CT↔工艺角、s5p↔温度);叉乘时不爆笛卡尔积。
- 聚合 corner:模板物化成一个多点列。
- 套模板到模式 → 自动叉乘生成列。
- 复用 Phase 2 union 的 explode 引擎。
- **Dogfood gate:** 一个 PVT 模板一键套到 LO 的 7 个模式;VCO 复合轴验证
  `[工艺角+CT] × [电压] × [温度+s5p]` = 45 点(而非 405)。

### Stage 3 —— 变体 + 覆盖优先级链(痛点 c)

- 变体 = 模式的 diff-overlay,可被模板套(生成 PN_TT / PN_SS_1…)。
- 实现三层回落链 + D1 标红 / D2 绝对值+继承 / D3 解绑冻结。
- **Dogfood gate:** 建 `BT_2G_RX_PN` 变体关掉 DIV12 → 套模板 → 验证回落链 +
  手改格标红 + base 改动对变体未覆盖 var 的继承。

### Stage 4 —— 组合 / 运行集 + 列筛选(痛点 d + f)

- 运行集 = 命名的跨模式 corner 勾选清单,一键切换。
- 列筛选:保留 Cadence 的按名字筛选 + 新增按运行集筛选。
- **Dogfood gate:** 建 `All_Mode_TT`、`BT_2G_RX_Fast_check` 两个运行集,切换验证。

### Stage 5 —— 视图层 + 校验 + 跨工程复用(痛点 e + g,补充缺口 #4 #5)

- 行筛选:按变量名,支持 and / or / 通配。
- 行可拖拽重排。
- 加载/保存校验:复合轴绑定完整性、`.s5p` 等文件存在性、寄存器名有效性 → 标红。
- 模板 / 复合轴定义提取成**跨工程可复用库**,不绑死单个 testbench。
- **Dogfood gate:** 在 >100 行的 corner 上做筛选 + 拖行;新工程直接复用模板库。

---

## 4. 跨阶段约束

- GUI 布局始终和 Cadence corner manager 一致(用户已用习惯)。
- 每阶段结束:Python 测试全绿 + SKILL Tier-1 全绿 + 一次真实 Maestro round-trip
  dogfood,而不是"测试数达标"。

---

## 5. 已定决策(2026-05-20)

| # | 问题 | 决定 |
|---|---|---|
| 1 | VCO/复合轴是否提前 | 不提前。用户已手动加完当前 VCO,不急。Stage 2 复合轴照旧在计划内,dogfood 优先级降低,1→5 顺序不变 |
| 2 | 与 Phase 2 union sidecar 的关系 | union 下沉为底层序列化格式,corner manager 是其上的模型层 —— 不破坏 Phase 2 已交付件 |
| 3 | 阶段归属 | 单列为 **Phase 5 = Corner Manager**。公式编辑器/批量仿真/数据处理三条精细化轨进 PHASE_PLAN.md 当候选,Phase 5 后再挑 |

---

## 6. 托管模型与 reconciliation(Stage 1 核心机制)

**问题:** corner manager 怎么知道 `BT_2G_RX_TT`、`BT_2G_RX_SS_1`、`BT_2G_RX_PVT_45`
属于同一个模式 bundle?

**不靠名字前缀猜。** 前缀匹配脆弱:`BT_2G_RX` 与 `BT_2G`、`BT_2G_RX_TX` 会互相吃前缀;
Cadence 也不强制命名规则。

**靠 sidecar 显式记录归属。** 沿用 Phase 2 设计哲学(声明式 sidecar = 真相,Maestro
corner 表 = 展开产物):corner manager 维护一份 sidecar,显式记录 模式→列 归属、模板
绑定、变体。归属关系是**被 authored 出来的,不是从 Maestro 反推的**。

**未托管列(foreign column):** 用户若在 Cadence 原生 corner manager 里
"create corner copy" 生成 `BT_2G_RX_TT_copy`,这列在 Maestro 里存在、但不在 sidecar
里。pull / refresh 时 corner manager:

- 标为**未托管**(置灰 / badge),照常显示在表里,但不归任何模式;
- 用户二选一:**收编**(指定它归入某模式/变体 → 补进 sidecar)或 **留作未托管**;
- push 时**绝不静默删除**未托管列 —— 托管列按 sidecar 覆盖,未托管列原样保留。

复用 Phase 4 GUI 已有的 divergence-strip 模式(show_diff / pull_overrides / keep_sidecar)。
