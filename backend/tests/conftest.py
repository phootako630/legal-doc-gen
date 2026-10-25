# 测试隔离：checkpoint 库、上传目录与运行日志指向临时目录，并在每个测试后重置图缓存，
# 避免测试写进真实的 backend/data，也避免测试间串状态。
import asyncio

import pytest

from app import config
from app.agent import checkpoint, runner
from app.agent.graph import reset_graph
from app.services import file_store, run_log
from app.routers import client_errors
from app.services.login_guard import guard


@pytest.fixture(autouse=True)
def isolated_case_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CHECKPOINT_DB_PATH", str(tmp_path / "ckpt.sqlite"))
    monkeypatch.setattr(file_store, "UPLOAD_DIR", str(tmp_path / "uploads"))
    # 运行日志也写临时目录，不往真实 backend/data 里追加（也不触发按天切分）
    monkeypatch.setattr(run_log, "RUN_LOG_PATH", str(tmp_path / "run_log.jsonl"))
    # 账号库同样隔离；登录默认关闭（与本地开发一致），登录相关测试自行开启
    monkeypatch.setattr(config, "AUTH_DB_PATH", str(tmp_path / "auth.sqlite"))
    monkeypatch.setattr(config, "AUTH_ENABLED", False)
    # 每日小结：写临时目录，且不随 TestClient 的 lifespan 自动运行（会发 Key 检查的外部请求）
    monkeypatch.setattr(config, "DAILY_SUMMARY_DIR", str(tmp_path / "daily_summary"))
    monkeypatch.setattr(config, "DAILY_SUMMARY_ENABLED", False)
    guard.reset()
    client_errors.limiter.reset()
    yield
    # 进行中的分析任务属于测试自己的事件循环，跨测试复用会出错，清空
    runner._inflight.clear()
    # 每个测试可能用过不同的事件循环；关闭连接放到新循环里做
    asyncio.run(reset_graph())
    assert checkpoint._saver is None
