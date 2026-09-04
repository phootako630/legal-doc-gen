# doc_search 单测：关键词逐页检索 + 三类条款定位。
from app.services.doc_search import locate_clauses, search_in_docs


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
