# provenance 单测：逐页锚定定页 + 通道判定 + src 优先，以及 enrich_provenance 富化。
from app.services.extraction import enrich_provenance
from app.services.provenance import resolve_provenance


def _pages(*texts):
    return [{"page": i + 1, "text": t} for i, t in enumerate(texts)]


def test_exact_hit_resolves_page_in_src_file():
    files = [
        {
            "filename": "审批表.pdf",
            "is_scanned": False,
            "pages": _pages("封面", "被告：某某电梯公司"),
        },
    ]
    prov = resolve_provenance("某某电梯公司", "《审批表.pdf》被告栏", files)
    assert prov.page == 2
    assert prov.filename == "审批表.pdf"
    assert prov.channel == "text"
    assert prov.anchor_quality == "exact"


def test_prefers_src_named_file_over_other():
    # 同一个值同时出现在两个文件；src 指向合同 → 应返回合同里的页
    files = [
        {
            "filename": "审批表.pdf",
            "is_scanned": False,
            "pages": _pages("张三 138xxxx"),
        },
        {
            "filename": "合同.pdf",
            "is_scanned": False,
            "pages": _pages("甲方", "联系人 张三"),
        },
    ]
    prov = resolve_provenance("张三", "《合同.pdf》第2页", files)
    assert prov.filename == "合同.pdf"
    assert prov.page == 2


def test_scanned_file_marks_ocr_channel():
    files = [
        {
            "filename": "安装合同.pdf",
            "is_scanned": True,
            "pages": _pages("付款条款：验收后30日内支付"),
        },
    ]
    prov = resolve_provenance("验收后30日内支付", "《安装合同.pdf》付款条款", files)
    assert prov.channel == "ocr"
    assert prov.page == 1


def test_amount_equivalence_resolves_page():
    files = [
        {
            "filename": "审批表.pdf",
            "is_scanned": False,
            "pages": _pages("金额一览", "合同总价 894,934.00 元"),
        },
    ]
    prov = resolve_provenance(894934, "《审批表.pdf》金额", files)
    assert prov.page == 2
    assert prov.anchor_quality == "exact"


def test_no_hit_returns_empty_provenance():
    files = [
        {
            "filename": "审批表.pdf",
            "is_scanned": False,
            "pages": _pages("完全无关的内容"),
        },
    ]
    prov = resolve_provenance("查无此值", "《审批表.pdf》", files)
    assert prov.page is None
    assert prov.filename is None
    assert prov.anchor_quality == "none"


def test_no_pages_falls_back_to_text_page_none():
    files = [
        {
            "filename": "老客户端.pdf",
            "is_scanned": False,
            "text": "被告：某某公司",
            "pages": [],
        },
    ]
    prov = resolve_provenance("某某公司", "《老客户端.pdf》", files)
    assert prov.filename == "老客户端.pdf"
    assert prov.page is None  # 无分页信息 → 命中但页码未知
    assert prov.anchor_quality == "exact"


def test_enrich_provenance_writes_fields():
    fields = {
        "defendant_name": {"value": "某某电梯公司", "src": "《审批表.pdf》被告栏"},
        "total_amount": {"value": 894934, "src": "《审批表.pdf》金额"},
        "missing_field": {"value": None, "src": ""},
        "contacts": [
            {
                "name": {"value": "张三", "src": "《审批表.pdf》"},
                "phone": {"value": None, "src": ""},
            }
        ],
    }
    files = [
        {
            "filename": "审批表.pdf",
            "is_scanned": False,
            "pages": _pages(
                "封面", "被告：某某电梯公司  联系人 张三", "合同总价 894,934 元"
            ),
        }
    ]
    enrich_provenance(fields, files)

    assert fields["defendant_name"]["page"] == 2
    assert fields["defendant_name"]["channel"] == "text"
    assert fields["defendant_name"]["confidence"] >= 60  # text + exact → 70
    assert fields["total_amount"]["page"] == 3
    # 未命中/空值：页码 None，confidence 偏低（走「待核实」）
    assert fields["missing_field"]["page"] is None
    assert fields["missing_field"]["confidence"] < 60
    # 嵌套 contacts 也被富化
    assert fields["contacts"][0]["name"]["page"] == 2


# ── 出处文字里的页码与锚定页码一致（#120）──────────────────────────────────
_SEAL_CONTRACT = {
    "filename": "虚构安装合同.pdf",
    "is_scanned": True,
    # 乙方名称在封面（第 1 页）和盖章页（第 3 页）都出现
    "pages": _pages("乙方：虚构电梯工程有限公司", "付款方式", "乙方（盖章）：虚构电梯工程有限公司"),
}


def test_prefers_cited_page_when_value_appears_on_several_pages():
    prov = resolve_provenance(
        "虚构电梯工程有限公司", "《虚构安装合同.pdf》第3页盖章页", [_SEAL_CONTRACT]
    )
    assert prov.page == 3


def test_cited_page_without_value_falls_back_to_first_hit():
    prov = resolve_provenance(
        "虚构电梯工程有限公司", "《虚构安装合同.pdf》第2页", [_SEAL_CONTRACT]
    )
    assert prov.page == 1


def test_enrich_keeps_cited_page_in_src_and_page_consistent():
    fields = {
        "contract_party_b": {
            "value": "虚构电梯工程有限公司",
            "src": "《虚构安装合同.pdf》第3页盖章页（OCR识别，请核实）",
        }
    }
    enrich_provenance(fields, [_SEAL_CONTRACT])
    node = fields["contract_party_b"]
    assert node["page"] == 3
    assert "第3页" in node["src"]


def test_enrich_rewrites_page_in_src_when_cited_page_misses():
    # 模型写第 2 页，第 2 页没有这个值，实际命中第 1 页：出处文字改成第 1 页，与按钮一致
    fields = {
        "contract_party_b": {
            "value": "虚构电梯工程有限公司",
            "src": "《虚构安装合同.pdf》第2页（OCR识别，请核实）",
        }
    }
    enrich_provenance(fields, [_SEAL_CONTRACT])
    node = fields["contract_party_b"]
    assert node["page"] == 1
    assert "第1页" in node["src"] and "第2页" not in node["src"]
    assert node["src"].endswith("（OCR识别，请核实）")
    # 再锚定一次（OCR 补读后会重算）结果不变
    enrich_provenance(fields, [_SEAL_CONTRACT])
    assert fields["contract_party_b"]["src"] == node["src"]
