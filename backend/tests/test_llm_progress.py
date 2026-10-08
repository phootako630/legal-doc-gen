# 进度模块单测：按 progress_id 隔离、生命周期，以及 contextvar 能传进 LangGraph 节点
#
# UAT 阶段开发者与律师会同时使用：两人的上传 / 分析进度必须互不串。
import asyncio

from app.agent import nodes as nodes_mod
from app.agent.runner import run_analyze_once
from app.services import llm_progress, upload_progress


def test_progress_lifecycle():
    with llm_progress.track("t-life", total_stages=3):
        snap = llm_progress.snapshot("t-life")
        assert snap["active"] is True
        assert snap["total_stages"] == 3
        assert snap["stage_index"] == 0
        assert snap["stage_elapsed_s"] >= 0  # 活跃时应返回非负耗时

        llm_progress.start_stage("字段抽取", 2)
        snap = llm_progress.snapshot("t-life")
        assert snap["stage"] == "字段抽取"
        assert snap["stage_index"] == 2

    snap = llm_progress.snapshot("t-life")
    assert snap["active"] is False
    assert snap["stage"] == ""
    assert snap["stage_index"] == 0
    assert snap["stage_elapsed_s"] == 0  # 非活跃时耗时归零


def test_tasks_are_isolated():
    # 两个任务交替推进，各自的阶段互不覆盖
    with llm_progress.track("t-a", total_stages=3):
        llm_progress.start_stage("材料清点", 1)
        with llm_progress.track("t-b", total_stages=3):
            llm_progress.start_stage("校验高亮", 3)
            assert llm_progress.snapshot("t-b")["stage"] == "校验高亮"
        # 离开内层后 contextvar 回到 t-a
        llm_progress.start_stage("字段抽取", 2)
        assert llm_progress.snapshot("t-a")["stage"] == "字段抽取"
    assert llm_progress.snapshot("t-b")["active"] is False


def test_unknown_or_missing_id_is_inactive_and_noop():
    assert llm_progress.snapshot("never-started")["active"] is False
    assert llm_progress.snapshot(None)["active"] is False
    # 未绑定任务时 start_stage 不报错、不记录（单测直调节点的场景）
    llm_progress.start_stage("字段抽取", 2)
    with llm_progress.track(None, total_stages=3):
        llm_progress.start_stage("字段抽取", 2)


def test_snapshot_is_a_copy():
    # snapshot 返回副本，外部修改不应污染内部状态
    with llm_progress.track("t-copy", total_stages=2):
        snap = llm_progress.snapshot("t-copy")
        snap["stage"] = "被外部篡改"
        assert llm_progress.snapshot("t-copy")["stage"] != "被外部篡改"


def test_upload_progress_isolated():
    with upload_progress.track("u-1", total_files=2):
        upload_progress.start_file("审批表.pdf", 1)
        with upload_progress.track("u-2", total_files=1):
            upload_progress.start_file("合同.pdf", 1)
        snap = upload_progress.snapshot("u-1")
        assert snap["active"] is True
        assert snap["filename"] == "审批表.pdf"
        assert snap["total_files"] == 2
    assert upload_progress.snapshot("u-1")["active"] is False
    assert upload_progress.snapshot("u-2")["filename"] is None


def test_concurrent_analyses_report_their_own_stages(monkeypatch):
    """两个分析并发跑真实 LangGraph 图：节点里的 start_stage 必须记到各自的请求编号下。"""
    seen: list[tuple[str | None, str]] = []

    def fake_load_prompt(name, variables=None):
        return name

    async def fake_chat(messages, json_mode=False, **_):
        prompt = messages[0]["content"]
        tid = llm_progress._table.current()  # noqa: SLF001 —— 断言 contextvar 已传入节点
        seen.append((tid, llm_progress.snapshot(tid)["stage"]))
        await asyncio.sleep(0)  # 让两个分析交错执行
        if prompt == "prompt-a-checklist.md":
            return {"can_proceed": True, "missing": [], "notes": ""}
        if prompt == "prompt-a-extract.md":
            return {"defendant_name": {"value": "某公司", "src": "《审批表.pdf》"}}
        return {} if json_mode else "说明\n---HIGHLIGHT---\n"

    monkeypatch.setattr(nodes_mod, "load_prompt", fake_load_prompt)
    monkeypatch.setattr(nodes_mod, "chat", fake_chat)
    files = [{"filename": "审批表.pdf", "identified_type": "审批表", "text": "某公司"}]

    async def both():
        return await asyncio.gather(
            run_analyze_once("key-lawyer-0001", files, True),
            run_analyze_once("key-developer-02", files, True),
        )

    asyncio.run(both())

    by_task: dict[str | None, list[str]] = {}
    for tid, stage in seen:
        by_task.setdefault(tid, []).append(stage)
    expected = ["材料清点", "字段抽取", "校验高亮"]
    # 每个分析都完整看到自己的三阶段，且没有落到“未绑定”(None) 名下
    assert by_task["key-lawyer-0001"] == expected
    assert by_task["key-developer-02"] == expected
    assert None not in by_task
    # 结束后两个任务都复位为非活跃
    assert llm_progress.snapshot("key-lawyer-0001")["active"] is False
    assert llm_progress.snapshot("key-developer-02")["active"] is False
