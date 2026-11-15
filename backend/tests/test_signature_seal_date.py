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


_MISSING = object()


def _sig(shared="2021年10月18日", shared_legible=True, ours="2021-12-15", ours_legible=True, notes=""):
    our_party = {"role": "供货方", "name": "某电梯有限公司", "date": ours}
    if ours_legible is not _MISSING:
        our_party["date_legible"] = ours_legible
    data = {
        "parties": [{"role": "购货方", "name": "虚构置业有限公司", "date": None, "date_legible": False}, our_party],
        "shared_date": shared, "notes": notes,
    }
    if shared_legible is not _MISSING:
        data["shared_date_legible"] = shared_legible
    return json.dumps(data, ensure_ascii=False)


def _run(monkeypatch, text, state=None):
    ocr = AsyncMock(return_value=f"```json\n{text}\n```")
    monkeypatch.setattr(nodes_mod, "ocr_signature_page", ocr)
    out = asyncio.run(nodes_mod.signature_ocr_node(state or _state()))
    return out, ocr


@pytest.mark.parametrize(
    "kw",
    [
        {"shared_legible": False},  # 公共日期明确标看不清
        {"shared_legible": _MISSING, "notes": "左侧签约日期被红色印章覆盖"},  # 没标可读性、说明写了日期被覆盖
    ],
)
def test_occluded_takes_our_seal_date(monkeypatch, kw):
    out, ocr = _run(monkeypatch, _sig(**kw))
    node = out["extracted_fields"]["contract_sign_date"]
    assert node["value"] == "2021年12月15日"
    assert "我方" in node["src"] and "遮挡" in node["src"] and "待核实" in node["src"]
    assert "2021年10月15日" in node["src"]  # 原识别值
    assert node["page"] == 2 and out["ocr_pages_used"] == 1
    assert ocr.await_args.args[1] == 2  # 读的是签约日期所在的那一页


def test_all_legible_takes_latest_even_if_notes_mention_seal(monkeypatch):
    # 说明提到「被红章覆盖，但字迹清晰可辨」、各日期都明确标清楚：以清楚为准，按（六）第 4 题取最晚日期（#172 评审）
    state = _state()
    state["extracted_fields"]["contract_sign_date"]["value"] = "2022年1月1日"
    out, _ = _run(monkeypatch, _sig(shared="2022-01-01", ours="2021-12-15", notes="签约日期被红章覆盖，但字迹清晰可辨"), state)
    assert "extracted_fields" not in out  # 现值已是最晚的 2022-01-01，不改成我方较早日期
    # 现值是普通 OCR 错读的日期：取签署栏里清楚的最晚日期
    out, _ = _run(monkeypatch, _sig(shared="2021-10-18", ours="2021-12-15"))
    node = out["extracted_fields"]["contract_sign_date"]
    assert node["value"] == "2021年12月15日" and "最晚日期" in node["src"] and "待核实" in node["src"]


@pytest.mark.parametrize(
    "kw",
    [
        {"shared_legible": False, "ours_legible": False},  # 我方日期也看不清：不猜
        {"shared_legible": False, "ours_legible": _MISSING},  # 我方没标可读性：不当作清楚（#172 评审）
        {"shared_legible": False, "ours": "2021-1?-15"},
        {"shared_legible": False, "ours": "2021-10-15"},  # 与现值相同
        {"shared_legible": _MISSING},  # 可读性说不清、说明也没提遮挡：不改
        {"shared": None, "shared_legible": False, "ours": "2021-10-15"},  # 没有公共日期，不算遮挡
        {"shared_legible": _MISSING, "notes": "甲方名称位于红色印章下方"},  # 说的不是日期
    ],
)
def test_no_override(monkeypatch, kw):
    out, _ = _run(monkeypatch, _sig(**kw))
    assert "extracted_fields" not in out and out["ocr_pages_used"] == 1


def test_skipped_when_not_from_scanned_page(monkeypatch):
    for state in (_state(sign_src="《合同.docx》第5条签署处"), _state(sign_src="律师填写")):
        state["extracted_fields"]["contract_sign_date"]["channel"] = "text"
        state["extracted_fields"]["contract_sign_date"].pop("source_file")
        out, ocr = _run(monkeypatch, _sig(shared_legible=False), state)
        assert out == {} and ocr.await_count == 0


def test_missing_or_approval_date_reads_located_signature_page(monkeypatch):
    # 普通 OCR 因遮挡没读出签约日期，或暂取审批表日期：从已识别的扫描签章页复核（#172 评审）
    for sign in ({"value": None, "src": ""}, {"value": "2021-12-01", "src": "《审批表.pdf》签约时间"}):
        state = _state()
        state["extracted_fields"]["contract_sign_date"] = sign
        state["files"][0]["pages"] = [
            {"page": 1, "text": "第一条 工程概况"},
            {"page": 3, "text": "签约方于 ____ 在 盖章前确认以上条款：\n甲方（盖章）：虚构置业有限公司"},
        ]
        out, ocr = _run(monkeypatch, _sig(shared_legible=False), state)
        assert ocr.await_args.args[1] == 3
        node = out["extracted_fields"]["contract_sign_date"]
        assert node["value"] == "2021年12月15日" and node["page"] == 3


@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        ("budget", "扫描识别页数已达上限"),
        ("raise", "识别失败"),
        ("bad_json", "识别结果无法解析"),
    ],
)
def test_unreviewed_is_visible_in_src(monkeypatch, setup, reason):
    # 签署栏没复核成：保留原值，出处写明未复核及原因（#172 评审）
    state = _state(used=10_000) if setup == "budget" else _state()
    if setup == "raise":
        monkeypatch.setattr(nodes_mod, "ocr_signature_page", AsyncMock(side_effect=RuntimeError("x")))
        out = asyncio.run(nodes_mod.signature_ocr_node(state))
    else:
        out, _ = _run(monkeypatch, "识别不出" if setup == "bad_json" else _sig(), state)
    node = out["extracted_fields"]["contract_sign_date"]
    assert node["value"] == "2021年10月15日"
    assert "签署栏未复核" in node["src"] and reason in node["src"] and "待核实" in node["src"]


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
