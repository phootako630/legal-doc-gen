# 测试隔离：checkpoint 库与上传目录指向临时目录，并在每个测试后重置图缓存，
# 避免测试写进真实的 backend/data，也避免测试间串状态。
import asyncio

import pytest

from app import config
from app.agent import checkpoint, runner
from app.agent.graph import reset_graph
from app.services import file_store


@pytest.fixture(autouse=True)
def isolated_case_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CHECKPOINT_DB_PATH", str(tmp_path / "ckpt.sqlite"))
    monkeypatch.setattr(file_store, "UPLOAD_DIR", str(tmp_path / "uploads"))
    yield
    # 进行中的分析任务属于测试自己的事件循环，跨测试复用会出错，清空
    runner._inflight.clear()
    # 每个测试可能用过不同的事件循环；关闭连接放到新循环里做
    asyncio.run(reset_graph())
    assert checkpoint._saver is None
