# 原告信息表：Excel 解析、括号规范化、校验、变更比对、存档回退与按名称查询（数据均为虚构）
import io
from datetime import datetime, timedelta, timezone

from openpyxl import Workbook

from app.services import branch_table as bt
from app.services.branch_registry import lookup_plaintiff

CN = timezone(timedelta(hours=8))
GOOD_A = "91440000MA00000015"
GOOD_B = "91320000MA00000029"


def _xlsx(rows: list[list[object]]) -> bytes:
    book = Workbook()
    for r in rows:
        book.active.append(r)
    buf = io.BytesIO()
    book.save(buf)
    return buf.getvalue()


HEADER = ["原告名称", "统一社会信用代码", "住所地", "法定代表人\\负责人", "联系方式"]


def test_parse_xlsx_normalizes_brackets_and_skips_blank_rows():
    content = _xlsx(
        [
            ["某某公司原告信息"],  # 表头上方的标题行
            HEADER,
            ["示例电梯（中国）有限公司", GOOD_A, "广州某地址", "甲某", 12345678],
            [
                " 示例电梯(中国)有限公司某某分公司 ",
                GOOD_B.lower(),
                "南京某地址",
                "乙某",
                None,
            ],
            [None, None, None, None, None],
        ]
    )
    entries = bt.parse_table("表.xlsx", content)
    assert [e["name"] for e in entries] == [
        "示例电梯（中国）有限公司",
        "示例电梯（中国）有限公司某某分公司",
    ]
    assert entries[0]["phone"] == "12345678"
    assert entries[1]["credit_code"] == GOOD_B


def test_parse_rejects_missing_header_and_unknown_format():
    for name, content, msg in [
        ("表.xlsx", _xlsx([["甲", "乙"], ["1", "2"]]), "未找到表头"),
        ("表.csv", b"a,b", "仅支持"),
    ]:
        try:
            bt.parse_table(name, content)
        except ValueError as e:
            assert msg in str(e)
        else:
            raise AssertionError("应当报错")


def test_check_entries_errors_and_warnings():
    entries = [
        bt.normalize_entry(
            {
                "name": "甲公司",
                "credit_code": GOOD_A,
                "address": "地",
                "representative": "人",
            }
        ),
        bt.normalize_entry(
            {
                "name": "乙公司",
                "credit_code": GOOD_A[:-1] + "H",
                "address": "",
                "representative": "人",
            }
        ),
        bt.normalize_entry(
            {
                "name": "甲公司",
                "credit_code": GOOD_A,
                "address": "地",
                "representative": "人",
            }
        ),
        bt.normalize_entry({"name": "", "credit_code": GOOD_B}),
    ]
    check = bt.check_entries(entries)
    assert "「甲公司」重复出现且内容不同，请保留正确的一行" in check.errors
    assert any("缺少原告名称" in e for e in check.errors)
    assert any("乙公司" in w and "校验位不符" in w for w in check.warnings)
    assert any("乙公司" in w and "住所地" in w for w in check.warnings)
    assert bt.check_entries([]).errors


def test_diff_entries():
    old = [
        {
            "name": "甲",
            "credit_code": "1",
            "address": "a",
            "representative": "张",
            "phone": "",
        },
        {
            "name": "乙",
            "credit_code": "2",
            "address": "b",
            "representative": "李",
            "phone": "",
        },
    ]
    new = [
        {
            "name": "甲",
            "credit_code": "1",
            "address": "a",
            "representative": "王",
            "phone": "",
        },
        {
            "name": "丙",
            "credit_code": "3",
            "address": "c",
            "representative": "赵",
            "phone": "",
        },
    ]
    diff = {d["name"]: d for d in bt.diff_entries(old, new)}
    assert diff["甲"]["kind"] == "changed"
    assert diff["甲"]["changes"] == [
        {
            "field": "representative",
            "label": "法定代表人\\负责人",
            "old": "张",
            "new": "王",
        }
    ]
    assert diff["乙"]["kind"] == "removed"
    assert diff["丙"]["kind"] == "added"


def test_save_archives_previous_version_and_restore(tmp_path):
    path, hist = str(tmp_path / "t.json"), str(tmp_path / "hist")
    v1 = [{"name": "甲", "credit_code": GOOD_A, "address": "a", "representative": "张"}]
    v2 = [{"name": "甲", "credit_code": GOOD_A, "address": "a", "representative": "王"}]
    t1 = datetime(2026, 1, 1, 9, 0, tzinfo=CN)
    bt.save_table(v1, "v1.xls", path, hist, now=t1)
    assert bt.list_history(hist) == []
    bt.save_table(v2, "v2.xls", path, hist, now=t1 + timedelta(days=1))
    history = bt.list_history(hist)
    assert [h["source_file"] for h in history] == ["v1.xls"]
    assert bt.load_table(path).entries[0]["representative"] == "王"

    restored = bt.restore_table(
        history[0]["id"], path, hist, now=t1 + timedelta(days=2)
    )
    assert restored.entries[0]["representative"] == "张"
    assert "恢复自" in restored.source_file
    assert len(bt.list_history(hist)) == 2  # 回退前的 v2 也已存档

    assert bt.age_days(restored, now=t1 + timedelta(days=12)) == 10


def test_restore_rejects_bad_version(tmp_path):
    for vid in ["../x", "nope"]:
        try:
            bt.restore_table(vid, str(tmp_path / "t.json"), str(tmp_path / "h"))
        except ValueError:
            pass
        else:
            raise AssertionError("应当报错")


def test_lookup_matches_half_width_brackets_and_reads_new_version(tmp_path):
    path, hist = str(tmp_path / "t.json"), str(tmp_path / "hist")
    bt.save_table(
        [
            {
                "name": "示例电梯(中国)有限公司某某分公司",
                "credit_code": GOOD_B,
                "address": "南京某地址",
                "representative": "乙某",
            },
        ],
        "a.xls",
        path,
        hist,
        now=datetime(2026, 9, 29, 10, 0, tzinfo=CN),
    )
    info = lookup_plaintiff("示例电梯（中国）有限公司某某分公司", path=path)
    assert info is not None
    assert (info.credit_code, info.person_in_charge, info.version) == (
        GOOD_B,
        "乙某",
        "2026-09-29",
    )
    assert lookup_plaintiff("示例电梯（中国）有限公司另一分公司", path=path) is None


def test_load_legacy_format(tmp_path):
    p = tmp_path / "legacy.json"
    p.write_text(
        '{"headquarters": {"name": "总公司", "credit_code": "X", "legal_rep": "甲"},'
        ' "branches": [{"name": "江苏分公司", "person_in_charge": "乙"}]}',
        encoding="utf-8",
    )
    table = bt.load_table(str(p))
    assert [e["representative"] for e in table.entries] == ["甲", "乙"]
    assert lookup_plaintiff("总公司江苏分公司", path=str(p)).person_in_charge == "乙"


def test_to_xlsx_round_trip():
    entries = [
        bt.normalize_entry(
            {
                "name": "甲公司",
                "credit_code": GOOD_A,
                "address": "地",
                "representative": "人",
                "phone": "1",
            }
        )
    ]
    assert bt.parse_table("x.xlsx", bt.to_xlsx(entries)) == entries
