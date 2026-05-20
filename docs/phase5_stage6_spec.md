# SimKit Corner Manager — Phase 5 Stage 6 §1 规格:PVT Profile 语义映射层

> 日期:2026-05-20。Stage 6 §1 冻结规格。Stage 1–5 把 Corner Manager 建起来后,
> 用户指出一个真实缺口:模板 / 跨工程库里的 **process / voltage / temperature 都是
> 字面值**,工程间命名和取值不同 → "可复用模板"(痛点 a)和"跨工程库"(缺口 #5)
> 名不副实。Stage 6 补一层 **PVT Profile 语义映射**,让模板写语义、profile 管具体。

---

## 1. 问题

| 维度 | Stage 1–5 现状 | 问题 |
|---|---|---|
| Process | 工艺角是 `column.models[].section` 字面串;模板 `apply_template` 还把 `models` 丢了(`models=()`)→ 模板根本不带 process | 换工程 section 名变(`tt` → `TOP_TT_RFTYP`),拆分角(`ssMOS_ffRC`)无法表达 |
| Voltage | 普通 `pvt_var`,名字(`VDD`/`LDO_VSET`)+ 值字面 | 模板写死 `VDD` 套不到用 `LDO_VSET` 的工程 |
| Temperature | 同 voltage | 各工程温度档不同;温飘要扫多点 |

依据 = 用户 2026-05-20 口述需求:三轴档位都开放可扩展;档位值可为标量或 sweep;
`常压/低压/高压` 等的实际值"项目设计之初定下"。

---

## 2. 核心概念:PVT Profile

**每工程一份**的稳定件(项目 kickoff 时定),声明若干**语义轴**;每轴一组开放的
**档位(level)**;每个档位解析成一组具体赋值。

### 2.1 统一形式

**一个档位 = `{vars?, models?}`** —— 任意一组 var 赋值 + 任意一组 model-section 赋值:

```json
{
  "pvtprofile_schema_version": 1,
  "name": "rf018_1AXX",
  "project": "1AXX",
  "axes": {
    "process": {
      "levels": {
        "TT":         {"models": [{"section": "tt"}]},
        "SS":         {"models": [{"section": "ss"}]},
        "ssMOS_ffRC": {"models": [
                         {"file": "mos.scs", "section": "ss"},
                         {"file": "rc.scs",  "section": "ff"}]}
      }
    },
    "voltage": {
      "levels": {
        "nominal": {"vars": {"LDO_VSET": "20"}},
        "low":     {"vars": {"LDO_VSET": "15"}},
        "high":    {"vars": {"LDO_VSET": "25"}}
      }
    },
    "temperature": {
      "levels": {
        "nominal": {"vars": {"temperature": "55"}},
        "hot":     {"vars": {"temperature": "125"}},
        "cold":    {"vars": {"temperature": "-40"}},
        "drift":   {"vars": {"temperature": ["-40", "0", "55", "85", "125"]}}
      }
    }
  }
}
```

- **轴、档位都开放** —— 用户随意增删轴和档位;process 不限 5 个工艺角,可加
  `ssMOS_ffRC`、`tt_lowVth` 等任意键。
- 档位的 `vars` 值可为标量或 sweep 数组(温飘 = 一个档位一串温度)。
- **process 档位的 `models`**:条目无 `file` = "该列每个模型都用这个 section"
  (经典 TT);带 `file` = 指定模型用指定 section(拆分角 `ssMOS_ffRC`)。
- 这一个形式同时覆盖:voltage(纯 `vars`)、process(纯 `models`,可多模型)、
  温飘(`vars` 带 sweep)、以及"`.s5p` 跟着温度走"这类绑定(一个档位的 `vars`
  里同时塞 `temperature` + s5p 文件名)。

### 2.2 落盘

`.pvtprofile.json` sidecar,`.pvtproject` 新增可选键 `pvtProfilesDir`(默认
`./pvt_profiles`)。一个工程通常一份 profile;cornermodel 通过新增可选字段
`pvt_profile`(= profile 名)绑定它。

---

## 3. 模板 / 列引用语义 token

`TemplateColumn` 与 `Column` 新增可选字段 `axis_levels: dict[str,str]` ——
轴名 → 档位名。例:

```json
{"pvt_label": "TT", "axis_levels": {"process": "TT", "voltage": "nominal",
                                    "temperature": "nominal"}}
```

- 模板列既可用语义 `axis_levels`,也可仍用 Stage 2 的字面 `pvt_vars` /
  `correlated_axes` —— 两者并存,`axis_levels` 是 Stage 6 新增的可移植写法。
- 跨工程库(`.cornerlib.json`)里的模板**只写 `axis_levels`** → 真正可移植:
  换工程换一份 profile 即可,模板不动。

---

## 4. 物化(resolve 经 profile)

`materialize(model, profile=None)` 新增可选 `profile` 参数。对每个带 `axis_levels`
的列:

1. 对每个 `轴: 档位`,查 `profile.axes[轴].levels[档位]`。
2. 把该档位的 `vars` 并入列的 row vars(sweep 保留为 Phase 2 sweep)。
3. 把该档位的 `models` 解析到列的模型上:无 `file` 的条目 → 该列每个模型该
   section;带 `file` 的 → 对应模型该 section。
4. 解析顺序与覆盖优先级:`axis_levels` 解析出的值视为"模板/base 层",仍低于
   变体、低于手改(沿用三层链 §Stage 3);两个轴若解析出同名 var → load error
   (轴之间 var 不相交,见 §6)。

无 `profile` 或列无 `axis_levels` → 退回 Stage 1–5 行为(纯字面),完全兼容。

`column_point_count` / `column_display_vars` 等随之经 profile 解析。

---

## 5. GUI(在 Stage 5 CornerManagerView 之上)

- 新增 **Profile 区**:显示当前绑定的 profile + 各轴档位清单(只读查看 + "重新
  绑定 profile" 选择)。profile 本身的编辑走 author `.pvtprofile.json`(项目
  kickoff 件,不是高频操作)—— GUI 提供查看,不强求内置编辑器。
- 新建模板列对话框:档位用**下拉选 profile 里的轴/档位**,而不是手敲字面值。
- corner 表单元格:profile 解析出的格标注来源(tooltip "process=ssMOS_ffRC →
  mos.scs:ss, rc.scs:ff")。
- 校验条(Stage 5 `check_cornermodel`)新增:列引用了 profile 里不存在的
  轴/档位 → `unknown_axis_level`。

---

## 6. 校验

加载期(`.pvtprofile.json`):
1. `pvtprofile_schema_version == 1`;`name` == 文件 basename;`project` 匹配。
2. 每个轴 `levels` 非空;每个档位 `vars` / `models` 至少一非空,值类型合法。
3. process 档位 `models` 条目 `section` 必填。

cornermodel 侧(需 profile 在手时):
4. 列 / 模板列的 `axis_levels` 引用的轴 + 档位必须在 profile 里存在。
5. 不同轴解析出的 `vars` 键两两不相交;解析出的 var 与 `mode.vars` /
   `pvt_vars` 也不相交。
6. cornermodel `pvt_profile` 字段引用的 profile 名应能在 `pvtProfilesDir` 找到
   (找不到 → `check_cornermodel` 报 `missing_profile`,非阻塞)。

`cornermodel_schema_version` / `cornerlib_schema_version` 仍 `1`(`axis_levels` /
`pvt_profile` 是附加键)。

---

## 7. 验收 —— Stage 6 dogfood gate

1. author 一份 `.pvtprofile.json`(process 含一个拆分角 `ssMOS_ffRC`、voltage 用
   `LDO_VSET` 三档、temperature 含一个 `drift` 多点档)。
2. 建一个只写 `axis_levels` 语义 token 的 PVT 模板,套到模式 → 物化经 profile
   解析出正确的 section / var 值;`ssMOS_ffRC` 列两个模型各拿到 ss / ff。
3. 把这个模板导出成 `.cornerlib.json`,在**另一个 profile 不同**的工程导入 +
   绑定该工程的 profile → 同一模板物化出该工程的具体值(真正跨工程复用)。
4. 温飘:`temperature: drift` 档位物化成多点扫描。

**本 session 无 Virtuoso → NOT live-verified,dogfood 入清单。**

---

## 8. 与 Stage 2 复合轴的关系

Stage 2 的 `correlated_axes`(绑定 var 捆)与 profile 轴档位在"一个键 → 一组绑定
赋值"上形态相近。**不重写 Stage 2** —— 复合轴仍是 cornermodel 内的叉乘单元;
profile 是其上的、跨工程可移植的语义层。一个 profile 档位的 `vars` 可表达
"`.s5p` 跟着温度"这类绑定,功能上与单点复合轴 tuple 重叠;两者并存,文档注明
推荐:跨工程复用走 profile,工程内一次性叉乘走复合轴。
