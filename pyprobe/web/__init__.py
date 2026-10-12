"""Web 监控模式（ADR A13）：与 cli 平级的顶层消费形态。

本包仅可依赖 observe 与横切层（dto/errors）；禁 kernel/target/cpython/
present/cli（contracts.md ST-W1）。``pyprobe/__init__.py`` 不得 import 本包
（核心零依赖红线，ST-W2）——web 只经 ``pyprobe.web`` 显式触达。
"""
