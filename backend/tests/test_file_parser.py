# file_parser 文件类型识别单测：按文件名关键词归类
import pytest

from app.services.file_parser import _identify_type


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("KA大客户诉讼审批表.pdf", "审批表"),
        ("电梯安装合同.pdf", "合同"),
        ("验收报告.pdf", "验收报告"),
        # 实务中的验收材料是《电梯监督检验报告》，应归为验收报告
        ("电梯监督检验报告.pdf", "验收报告"),
        ("其他材料.pdf", "未知"),
    ],
)
def test_identify_type(filename, expected):
    assert _identify_type(filename) == expected


@pytest.mark.parametrize(
    ("filename", "text", "expected"),
    [
        # 审批表以流程名命名：文件名没有「审批」，正文标题才是审批表（正文里也有「合同」）
        (
            "AH0000001某某置业有限公司安装诉讼流程.pdf",
            "1/5\nAH0000001某某置业有限公司诉讼流程\n编号\nKASS2026-000001\n"
            "KA大客户收款不良合同诉讼审批表\n合同号\nAH0000001",
            "审批表",
        ),
        ("报告扫描.pdf", "第 1 页 共 8 页\n电梯监督检验报告\n报告编号：XX-2023-001", "验收报告"),
        ("材料一.pdf", "某某项目\n电梯设备\n安装合同\n委托方（甲方）：某某置业有限公司", "合同"),
        # 正文后文才提到「合同」：不据此归类
        ("说明.pdf", "情况说明" + "。" * 400 + "依据合同约定", "未知"),
        # 文件名能认出时以文件名为准
        ("电梯安装合同.pdf", "KA大客户收款不良合同诉讼审批表", "合同"),
    ],
)
def test_identify_type_by_content(filename, text, expected):
    assert _identify_type(filename, text) == expected
