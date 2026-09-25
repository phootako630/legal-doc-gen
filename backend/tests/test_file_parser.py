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
