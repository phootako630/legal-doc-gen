# llm_progress 内存态进度模块单测：begin → start_stage → snapshot → finish 生命周期
from app.services import llm_progress


def test_progress_lifecycle():
    llm_progress.begin(total_stages=3)
    snap = llm_progress.snapshot()
    assert snap["active"] is True
    assert snap["total_stages"] == 3
    assert snap["stage_index"] == 0
    assert snap["stage_elapsed_s"] >= 0  # 活跃时应返回非负耗时

    llm_progress.start_stage("字段抽取", 2)
    snap = llm_progress.snapshot()
    assert snap["stage"] == "字段抽取"
    assert snap["stage_index"] == 2

    llm_progress.finish()
    snap = llm_progress.snapshot()
    assert snap["active"] is False
    assert snap["stage"] == ""
    assert snap["stage_index"] == 0
    assert snap["stage_elapsed_s"] == 0  # 非活跃时耗时归零


def test_snapshot_is_a_copy():
    # snapshot 返回副本，外部修改不应污染内部状态
    llm_progress.begin(total_stages=2)
    snap = llm_progress.snapshot()
    snap["stage"] = "被外部篡改"
    assert llm_progress.snapshot()["stage"] != "被外部篡改"
    llm_progress.finish()
