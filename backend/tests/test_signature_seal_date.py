# 签约日期被印章遮挡（确认单（七）第 7 题选 B）：签约日期出自扫描签署页时另做签署栏专用识别，
# 日期被遮挡 / 看不清时取我方盖章处日期并标待核实；双方日期都清楚时不改。OCR 一律打桩，虚构数据。
import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from app.agent import nodes as nodes_mod
from app.services import call_keys, file_store
from app.services.prompt_loader import load_prompt

_FILE = "合同.pdf"


def _state(sign_src=f"《{_FILE}》第2页签署栏（OCR识别，请核实）", used=0):
    return {
        "extracted_fields": {
            "contract_sign_date": {
                "value": "2021年10月15日", "src": sign_src, "page": 2, "channel": "ocr", "source_file": _FILE,
            }
        },
        "files": [{"filename": _FILE, "identified_type": "合同", "is_scanned": True,
                   "file_id": file_store.put(b"%PDF-fake-contract")}],
        "ocr_pages_used": used,
    }


def _sig(shared="2021年10月18日", shared_legible=True, ours="2021-12-15", ours_legible=True, notes=""):
    return json.dumps({
        "parties": [
            {"role": "购货方", "name": "虚构置业有限公司", "date": None, "date_legible": False},
            {"role": "供货方", "name": "某电梯有限公司", "date": ours, "date_legible": ours_legible},
        ],
        "shared_date": shared, "shared_date_legible": shared_legible, "notes": notes,
    }, ensure_ascii=False)


def _run(monkeypatch, text, state=None):
    ocr = AsyncMock(return_value=f"```json\n{text}\n```")
    monkeypatch.setattr(nodes_mod, "ocr_signature_page", ocr)
    out = asyncio.run(nodes_mod.signature_ocr_node(state or _state()))
    return out, ocr


@pytest.mark.parametrize(
    "kw",
    [
        {"notes": "左侧签约日期被红色印章覆盖，但字迹清晰可辨"},  # 说明里写了日期被印章覆盖（即使模型自评可读）
        {"shared_legible": False},  # 公共日期看不清
    ],
)
def test_occluded_takes_our_seal_date(monkeypatch, kw):
    out, ocr = _run(monkeypatch, _sig(**kw))
    node = out["extracted_fields"]["contract_sign_date"]
    assert node["value"] == "2021年12月15日"
    assert "我方" in node["src"] and "待核实" in node["src"] and "2021年10月15日" in node["src"]
    assert node["page"] == 2 and out["ocr_pages_used"] == 1
    assert ocr.await_args.args[1] == 2  # 读的是签约日期所在的那一页


@pytest.mark.parametrize(
    "kw",
    [
        {},  # 双方日期都清楚：仍按确认单（六）第 4 题，不改
        {"notes": "被印章覆盖", "ours_legible": False},  # 我方日期也看不清：不猜
        {"notes": "被印章覆盖", "ours": "2021-1?-15"},
        {"notes": "被印章覆盖", "ours": "2021-10-15"},  # 与现值相同
        # 双方日期都清楚、只是没有公共日期（模型把 legible 填 false）：按（六）第 4 题取最晚日期，不改
        {"shared": None, "shared_legible": False},
        {"notes": "甲方名称位于红色印章下方"},  # 说的不是日期
    ],
)
def test_no_override(monkeypatch, kw):
    out, _ = _run(monkeypatch, _sig(**kw))
    assert "extracted_fields" not in out and out["ocr_pages_used"] == 1


def test_skipped_when_not_from_scanned_page(monkeypatch):
    for state in (_state(sign_src="《审批表.pdf》签约时间"), _state(sign_src="律师填写")):
        state["extracted_fields"]["contract_sign_date"]["channel"] = "text"
        out, ocr = _run(monkeypatch, _sig(notes="覆盖"), state)
        assert out == {} and ocr.await_count == 0
    out, ocr = _run(monkeypatch, _sig(notes="覆盖"), _state(used=10_000))  # 全案 OCR 页数已用完
    assert out == {} and ocr.await_count == 0


def test_ocr_failure_or_bad_json_keeps_value(monkeypatch):
    out, _ = _run(monkeypatch, "识别不出")
    assert "extracted_fields" not in out
    monkeypatch.setattr(nodes_mod, "ocr_signature_page", AsyncMock(side_effect=RuntimeError("x")))
    out = asyncio.run(nodes_mod.signature_ocr_node(_state()))
    assert "extracted_fields" not in out and out["ocr_pages_used"] == 1


def test_signature_ocr_key_separate_from_page_ocr():
    # 同一页图片：签署栏专用识别与普通识别分开缓存；普通识别的键不变（已录缓存仍有效）
    prompt = load_prompt("ocr-signature.md")
    assert call_keys.ocr_key("QUJD", prompt) != call_keys.ocr_key("QUJD")
    assert call_keys.ocr_key("QUJD", None) == call_keys.ocr_key("QUJD")


def test_replay_cache_keeps_signature_ocr_apart(tmp_path):
    from scripts.case_eval.cache import ReplayCache

    cache = ReplayCache(str(tmp_path), mode="replay")  # 未命中时真实调用并录下
    calls = []

    def real(img, prompt=None):
        calls.append(prompt)
        return "签署栏" if prompt else "全文"

    ocr = cache.wrap_ocr(real)
    assert ocr("QUJD") == "全文" and ocr("QUJD", "签署栏提示词") == "签署栏"
    assert ocr("QUJD") == "全文" and ocr("QUJD", "签署栏提示词") == "签署栏"  # 第二轮都从缓存取
    assert calls == [None, "签署栏提示词"]
