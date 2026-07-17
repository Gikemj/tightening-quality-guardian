---
name: "质控前哨"
description: "面向总装关键拧紧工位的设备质量风险主动管控原型"
colors:
  calibration-mark: "oklch(0.55 0.15 338)"
  equipment-ink: "oklch(0.34 0.08 220)"
  canvas: "oklch(1 0 0)"
  panel: "oklch(0.965 0 0)"
  ink: "oklch(0.19 0.01 320)"
  muted: "oklch(0.46 0.015 320)"
  border: "oklch(0.88 0.01 320)"
  risk-high: "oklch(0.50 0.18 25)"
  risk-medium: "oklch(0.72 0.14 75)"
  state-ok: "oklch(0.45 0.10 155)"
typography:
  headline:
    fontFamily: "Inter, PingFang SC, Microsoft YaHei, system-ui, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 700
    lineHeight: 1.25
    letterSpacing: "-0.02em"
  title:
    fontFamily: "Inter, PingFang SC, Microsoft YaHei, system-ui, sans-serif"
    fontSize: "1rem"
    fontWeight: 650
    lineHeight: 1.4
  body:
    fontFamily: "Inter, PingFang SC, Microsoft YaHei, system-ui, sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 400
    lineHeight: 1.65
  label:
    fontFamily: "Inter, PingFang SC, Microsoft YaHei, system-ui, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 600
    lineHeight: 1.35
    letterSpacing: "0.01em"
  data:
    fontFamily: "SFMono-Regular, Consolas, Liberation Mono, monospace"
    fontSize: "0.8125rem"
    fontWeight: 550
    lineHeight: 1.45
rounded:
  control: "8px"
  panel: "12px"
  pill: "999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "16px"
  lg: "24px"
  xl: "32px"
components:
  button-primary:
    backgroundColor: "{colors.calibration-mark}"
    textColor: "{colors.canvas}"
    rounded: "{rounded.control}"
    padding: "10px 16px"
    typography: "{typography.label}"
  button-secondary:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "10px 16px"
    typography: "{typography.label}"
  panel:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.ink}"
    rounded: "{rounded.panel}"
    padding: "20px"
  risk-chip-high:
    backgroundColor: "{colors.risk-high}"
    textColor: "{colors.canvas}"
    rounded: "{rounded.pill}"
    padding: "4px 9px"
    typography: "{typography.label}"
---

<!-- SEED -->

# Design System: 质控前哨

## 1. Overview

**Creative North Star: "校准台账"**

质控前哨像一份放在工位旁、可以随时追溯的电子校准台账。白色画布承担大部分面积，信息通过字号、间距、细线和状态标签建立层级。少量玫红色像工程师的校准印记，只用于主操作和当前选中状态；红、黄、绿专门表达风险与处理状态。

参考 Linear 的层级克制、Grafana Explore 的证据密度，以及 Bosch Rexroth 工业界面的操作确定性。拒绝赛博霓虹大屏、装饰性工厂照片和把所有能力塞进聊天窗口的做法。

**Key Characteristics:**

- 数据先于结论，证据可展开、可定位。
- 高密度但不拥挤，适合常见笔记本屏幕和评委快速浏览。
- 风险、推断、人工审批和结案状态使用不同的视觉语法。
- 动效只解释状态变化，不安排入场表演。

## 2. Colors

色彩策略为 restrained：中性白与灰承担结构，品牌色在单屏内不超过约 10%。

### Primary

- **校准印记** (`oklch(0.55 0.15 338)`): 主按钮、当前导航和图表选中点。填充时使用白字。

### Secondary

- **设备墨蓝** (`oklch(0.34 0.08 220)`): 设备名称、证据来源和次级强调，表达技术信息而不与风险色竞争。

### Neutral

- **画布白** (`oklch(1 0 0)`): 页面与主要内容背景。
- **仪表浅灰** (`oklch(0.965 0 0)`): 侧栏、表头、静态信息区。
- **正文墨色** (`oklch(0.19 0.01 320)`): 主体文字。
- **注释灰** (`oklch(0.46 0.015 320)`): 辅助文字，仍需满足正文对比度。
- **分隔线** (`oklch(0.88 0.01 320)`): 容器边界和表格分隔。

### Named Rules

**The Signal Color Rule.** 红、黄、绿只表示风险或处置状态；校准印记色只表示交互与选择，二者不能混用。

## 3. Typography

**Display Font:** Inter（中文回退至 PingFang SC / Microsoft YaHei）
**Body Font:** Inter（中文回退至 PingFang SC / Microsoft YaHei）
**Label/Mono Font:** SFMono-Regular / Consolas

**Character:** 单一无衬线字体保持工程工具的熟悉感；设备编号、时间戳、规则编号和量测值使用等宽字体，让数据对齐更可靠。

### Hierarchy

- **Headline** (700, 24px, 1.25): 页面标题与当前风险名称。
- **Title** (650, 16px, 1.4): 面板标题和关键分组。
- **Body** (400, 15px, 1.65): 说明文字，长段落限制在 70ch 内。
- **Label** (600, 12px, 1.35): 字段名、状态、按钮和短标签，不使用全大写句子。
- **Data** (550, 13px, 1.45): 量测值、批次、规则和时间。

### Named Rules

**The Measured Number Rule.** 数字不能只显示结果；数值、单位、基线或阈值至少同时出现两项。

## 4. Elevation

默认无阴影。层级由画布、浅灰区域和 1px 分隔线完成。仅浮层和键盘焦点可出现短而清晰的阴影或焦点环，避免把常规面板做成漂浮卡片。

### Shadow Vocabulary

- **浮层** (`0 4px 8px oklch(0.19 0.01 320 / 0.12)`): 仅用于弹出帮助和场景选择菜单。
- **焦点** (`0 0 0 3px oklch(0.55 0.15 338 / 0.24)`): 键盘焦点，不与边框阴影叠加。

### Named Rules

**The Flat Ledger Rule.** 常规内容平铺在同一工作面上；阴影表示临时浮层或当前焦点，不表示装饰。

## 5. Components

### Buttons

- **Shape:** 8px 圆角，不使用药丸形主按钮。
- **Primary:** 校准印记色底、白字，10px 16px 内边距。
- **Hover / Focus:** 180ms 颜色或位移反馈；焦点使用 3px 半透明环。
- **Secondary:** 白底、1px 分隔线、正文墨色。
- **Disabled:** 降低对比度并保留说明文本，不只改变鼠标样式。

### Chips

- **Style:** 药丸形仅用于短状态，如“高风险”“待审批”“合成数据”。
- **State:** 高风险红底白字，中风险淡黄底深字，完成态淡绿底深字；标签同时写出文字。

### Cards / Containers

- **Corner Style:** 12px。
- **Background:** 白色为主，静态说明可用仪表浅灰。
- **Shadow Strategy:** 默认无阴影。
- **Border:** 1px 分隔线；禁止彩色侧边粗条。
- **Internal Padding:** 16px 至 24px，按信息密度选择。

### Inputs / Fields

- **Style:** 白底、1px 分隔线、8px 圆角，最小高度 40px。
- **Focus:** 校准印记色边框加 3px 焦点环。
- **Error / Disabled:** 错误同时给出文字；禁用状态保持可读。

### Navigation

桌面端使用窄侧栏，当前项以浅色底和文字加粗表示，不依赖一条彩色侧线。移动端折叠为顶部跳转菜单。导航名称使用业务名词，如“风险台账”“证据链”“任务闭环”。

### Evidence Chain

每条证据标出来源类型、时间和定位信息。事实使用实线连接；模型推断使用虚线并附置信度和待验证项。PFMEA、SPC、设备信号和历史案例不能折叠成一段无法追溯的结论。

## 6. Do's and Don'ts

### Do:

- **Do** 让每条风险结论回到具体数据点、规则编号、文档条目或历史案例。
- **Do** 在图表旁提供文本摘要，颜色之外同时使用标签和形状区分状态。
- **Do** 把“已观测事实”“候选原因”“建议动作”“人工决定”分区呈现。
- **Do** 使用 8px 控件圆角、12px 面板圆角和 180ms 状态反馈。
- **Do** 明确标注“比赛合成数据”和“非赛力斯官方系统”。

### Don't:

- **Don't** 做成一个没有任务闭环的通用 AI 聊天窗口。
- **Don't** 使用赛博霓虹、暗色大屏或装饰性工厂照片。
- **Don't** 用只给评分、不说明证据和责任人的管理驾驶舱。
- **Don't** 暗示系统会自动停线、修改 PLC 参数或替工程师确认根因。
- **Don't** 写未经验证的效果数字，也不要暗示使用赛力斯内部数据。
- **Don't** 在面板、告警或说明框上使用超过 1px 的彩色侧边条。
