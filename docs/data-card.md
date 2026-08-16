# 数据卡

## 数据集

`data/tightening_events_demo.csv` 是比赛演示用的确定性合成数据，共 820 条记录，覆盖五个紧固点。数据不包含个人信息、车辆 VIN、真实工厂编号或赛力斯内部参数。

## 字段

| 字段组 | 字段 | 用途 |
|---|---|---|
| 标识 | event_id, timestamp, station_id, tool_id, controller_id | 追溯事件和设备 |
| 生产上下文 | model_code, program_id, fastening_point, batch_id, shift | 分层基线与影响范围 |
| 工艺量测 | torque_nm, angle_deg, current_a, cycle_time_s | 过程与设备信号 |
| 设备状态 | retry_count, alarm_code, calibration_days_remaining | 设备健康和维护上下文 |
| 质量结果 | result | 当次设备判定 |
| 验证标签 | scenario_label | 离线评估使用，不进入线上推理 |

## 生成方式

脚本 `scripts/generate_demo_data.py` 使用固定随机种子。正常数据围绕每个紧固点的模拟目标值波动；P03 末段逐步注入漂移和设备侧变化。每次运行应生成完全相同的 CSV。

另有 [`src/torque_guard/scenarios.py`](../src/torque_guard/scenarios.py) 按独立种子动态生成正常、规格内漂移、传感器零漂和重复报警案例，各 30 个。该套件不写入上述 820 条 CSV，指标单独保存在 [`outputs/scenario_metrics.json`](../outputs/scenario_metrics.json)，不能与滚动窗口指标合并。

## 不适用范围

这些数据不能用于训练生产模型、计算企业收益或推断赛力斯真实工艺能力。它们只用于验证代码、界面和受控业务流程的数据合同；不代表真实工程师审批、飞书/Aily 闭环或现场结案已经发生。
