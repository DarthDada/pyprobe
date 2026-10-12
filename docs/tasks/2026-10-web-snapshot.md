# 任务：Web 按需单次栈快照（`GET /api/snapshot`）

> 状态：已完成（2026-10，批 16）——本文件留存为批次验收档案
> 契约 SSOT：contracts.md 批次 8 表增补（WM9 / WA6 / E2E-W2）。
> 需求来源：TODO「Web 监控后续迭代」；同批裁撤「多 PID 监控」迭代项（需求删除）。

## 背景

面板数据经 SSE 每 interval 推送一帧（采样口径、空闲线程剪枝），程序化消费
（curl/脚本/IDE 集成）缺少「现在就要一份完整栈」的入口。快照走 stack 语义：
全线程完整帧链（含空闲线程），与 `stack --json` 对齐而与 stats 帧互补。

## 设计

- `Monitor.snapshot()`：exited 粘性 → 直接 `ProcessExited`；否则
  `collect_snapshot(sampler.session)` 复用已解析会话；`NoThreadState` →
  `ProcessExited`（SA2 承接）但不置粘性、不广播——状态转移归采样线程（WM6）；
  不触碰 TopStats 与最新快照留存
- `GET /api/snapshot`：200 `{process, threads}`（process 块同 WA2；threads 键集
  即 D1 的 asdict schema）；目标退出 → 409 `{"detail": "target process exited"}`

## 验收

- [x] 契约：WM9 / WA6 / E2E-W2 + monitor 模块表 API 增补（contracts.md）
- [x] Red/Green：test_web_monitor.py TestSnapshot（3）+ test_web_app.py
  TestApiSnapshot（2）+ test_web_live.py E2E-W2（docs/red/batch16-{red,green}.txt）
- [x] 全套件 + lint + 文档检查绿
- [x] TODO 同步：裁撤多 PID 项、勾销快照项（索引清理，档案留本文件）
