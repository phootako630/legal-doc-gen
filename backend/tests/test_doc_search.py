# doc_search 单测：关键词逐页检索 + 三类条款定位。
from app.services.doc_search import (
    is_toc_page,
    locate_clause_bodies,
    locate_clauses,
    search_in_docs,
)


def _pages(*texts):
    return [{"page": i + 1, "text": t} for i, t in enumerate(texts)]


def test_search_returns_page_and_snippet():
    pages = _pages("封面无关内容", "第五条 付款：验收合格后30日内支付货款")
    hits = search_in_docs(pages, ["付款"])
    assert len(hits) == 1
    assert hits[0].page == 2
    assert "付款" in hits[0].snippet


def test_search_no_hit():
    pages = _pages("完全无关")
    assert search_in_docs(pages, ["付款", "支付"]) == []


def test_locate_clauses_finds_all_three():
    pages = _pages(
        "第五条 付款方式：验收后支付",
        "第六条 违约责任：逾期按每日万分之五计算利率",
        "第七条 争议解决：提交仲裁委员会仲裁",
    )
    assert locate_clauses(pages) == {"payment", "breach", "dispute"}


def test_locate_clauses_partial():
    pages = _pages("第五条 付款：验收后支付货款")
    assert locate_clauses(pages) == {"payment"}


# ── 条款正文判定（按需 OCR 的停止条件）──────────────────────────────────────
def test_toc_page_detected():
    assert is_toc_page("目 录 第一章 词语定义 第二章 合同文件")
    headings = " ".join(f"第{n}章 标题" for n in "二三四五六七八九十")
    assert is_toc_page("第二部分 合同条款 " + headings)  # 无「目录」字样的目录续页
    assert not is_toc_page(
        "第十九章. 工程保修 第二十章. 争议、违约及索赔 1.1 向法院提起诉讼"
    )


def test_clause_bodies_ignore_toc():
    toc = "目录 第十三章 工程款的核实与支付 第二十章 争议、违约及索赔"
    assert locate_clause_bodies(_pages(toc)) == set()


def test_clause_bodies_found_in_real_wording():
    pages = _pages(
        "第二十章. 争议 1.1 双方向工程所在地的当地法院提起诉讼。",
        "第二十八章. 合同价款支付 1. 进度款：安装完成后，30 个工作日内支付合同总价的 60%；",
    )
    assert locate_clause_bodies(pages) == {"payment", "dispute"}


def test_payment_pattern_excludes_penalty_and_fee():
    pages = _pages(
        "乙方还应向甲方支付合同价款 10% 的违约金。",
        "向总包单位支付本合同价（不含设备费）的2%作为乙方的配合及管理费",
    )
    assert "payment" not in locate_clause_bodies(pages)
