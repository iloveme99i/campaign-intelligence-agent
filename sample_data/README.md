# 演示数据

所有行由 `scripts/export-public-samples.py` 确定性生成，不包含公司内部数据、真实商家或用户数据。
活动名称只表示参考的公开机制，不表示合作、服务交付或真实经营结果。

首页导入 campaigns、events、orders、costs 四份 CSV；incrementality 是可选同期对照面板。
金额字段以整数分存储，事件按匿名用户和发生时间串联。

重新生成：`uv run python scripts/export-public-samples.py`。
