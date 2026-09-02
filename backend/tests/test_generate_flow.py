# generate 流程测试：/api/generate 确定性渲染（无 LLM），标注按字段状态贴
import asyncio

from app.routers.generate import GenerateRequest, generate


def _run(fields):
    return asyncio.run(generate(GenerateRequest(validated_json=fields)))


def test_generate_is_deterministic_no_llm():
    fields = {
        "plaintiff_name_final": {"value": "某电梯公司", "src": "《审批表》"},
        "total_amount": {"value": 894934, "src": "《审批表》"},
        "paid_amount": {"value": 638667.2, "src": "《审批表》"},
        "unpaid_amount": {"value": 256266.8, "src": "《审批表》"},
        "contract_sign_date": {"value": "2023-05-01", "src": "《审批表》"},
    }
    # 两次渲染完全一致（确定性，无 LLM 随机性）
    a = _run(fields).complaint_text
    b = _run(fields).complaint_text
    assert a == b
    assert a.startswith("民事起诉状")
    assert "贰拾伍万陆仟贰佰陆拾陆元捌角" in a
    assert "2023年05月01日" in a


def test_generate_marks_missing_and_conflict():
    fields = {
        "total_amount": {"value": 900000, "src": "《审批表》"},  # 与已付+未付不勾稽
        "paid_amount": {"value": 638667.2, "src": "《审批表》"},
        "unpaid_amount": {"value": 256266.8, "src": "《审批表》"},
        # plaintiff_name_final 缺失
    }
    text = _run(fields).complaint_text
    assert "【待补充】" in text  # 缺失字段
    assert "【高亮冲突：" in text  # 金额冲突
