# 试件清除（保密试件合规清除）测试说明

本目录存放 gb-0044「槐序干燥时长试算台」试件清除功能的独立测试，不混入源码目录。

## 运行方式

```bash
python -m venv .venv && source .venv/bin/activate   # 或任意已装依赖的解释器
pip install fastapi==0.115.6 uvicorn==0.34.0 SQLAlchemy==2.0.36 redis==5.2.1 \
            PyJWT==2.10.1 httpx pytest numpy==2.1.3 scikit-learn==1.5.2
python -m pytest test-artifacts/gb-0044/test_purge.py -v
```

测试使用临时 SQLite 与临时 ARTIFACT_DIR，进程内 FakeQueue 替代 Redis，
通过 `src.worker.execute()` 驱动真实计算，不依赖 Docker。

## 覆盖要点（对应需求条目）

- `test_purge_full_flow_with_inflight_download`：按编号追溯全部副本（来源样本/冻结输入/结果/结果文件/已批准备忘），
  原结论立即停止查看与下载，已发起未传完的下载在清除完成前被终止，受影响研究保留无原始数值的失效说明，
  其他试件可继续研究，重复导入被墓碑拦截，重新分析仅用剩余试件。
- `test_permissions`：仅负责人可发起/重试清除；匿名 401。
- `test_worker_completion_during_purge_is_discarded`：清除期间计算完成不得带回该试件（原子状态切换丢弃结果）。
- `test_file_failure_is_resumable_and_retry_completes`：数据库删完但文件清除中断可恢复，重试不遗漏副本；
  清除期间并发建研究不得带回该试件。
- `test_active_download_blocks_completion_until_terminated`：在途下载未终止前保持“清除中”，页面可见去向与原因。
- `test_seed_does_not_resurrect_purged_reference`：演示 seed 重复执行不得带回该试件。
