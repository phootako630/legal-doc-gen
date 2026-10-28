# 付款条款写法统计脚本（#116）：只输出计数，不带原文与案件目录名；写法只列通用付款术语。虚构数据。
from scripts.payment_phrasing_stats import main, render, scan_case


def _case(root, name, pages):
    d = root / name / "cache" / "ocr"
    d.mkdir(parents=True)
    for i, text in enumerate(pages):
        (d / f"{i:02d}.txt").write_text(text, encoding="utf-8")
    return root / name


def test_counts_current_candidate_and_fullwidth(tmp_path):
    c = scan_case(_case(tmp_path, "虚构梧桐案", [
        "3.1 甲方支付合同总价的30%。",  # 现行已认
        "3.2 甲方支付合同价款的50%。",  # 现行认不出，候选能认
        "3.3 验收后付至本合同总价款的95％。",  # 全角百分号
        "逾期付款按合同价款的5%支付违约金。",  # 违约金，不算付款
        "逾期付款违约金按合同价款的5%计算。",  # 违约金写在比例前面，同样不算（#123 评审）
        "目录\n第一章 总则\n第二章 付款 支付合同价款的10%",  # 目录页不计
    ]))
    assert (c.pages, c.toc_pages, c.current_hits, c.ratio_pages) == (6, 1, 1, 3)
    assert c.fullwidth_pages == 1
    assert c.candidate_hits == 2  # 合同价款、本合同总价款（全角「％」现行也认不出）
    assert c.bases["合同价款"] == 1 and "合同价款" in c.bases


def test_output_hides_case_names_and_unknown_phrasing(tmp_path, capsys):
    # 「星海虚构公司款项」两案都有：跨案件重复也不能列出（#123 评审），只计入「其他写法」
    _case(tmp_path, "虚构甲公司欠款案", ["甲方支付合同价款的50%。", "甲方支付星海虚构公司款项的20%。"])
    _case(tmp_path, "虚构乙公司欠款案", ["甲方支付合同价款的40%。", "甲方支付星海虚构公司款项的30%。"])
    assert main([str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "虚构甲公司" not in out and "虚构乙公司" not in out
    assert "| 合同价款 | 2 | 2 | ★ |" in out
    assert "星海虚构" not in out  # 不在通用术语表里：只计数，不列名字
    assert "其他写法：1 种，共 2 页" in out
    assert "50%" not in out and "甲方" not in out  # 不带原文


def test_render_handles_no_ratio_pages(tmp_path):
    out = render([scan_case(_case(tmp_path, "虚构空案", ["电梯安装合同"]))])
    assert "案件 1 个" in out


def test_missing_root_or_cache_returns_error(tmp_path):
    assert main([str(tmp_path / "nope")]) == 2
    assert main([str(tmp_path)]) == 2
