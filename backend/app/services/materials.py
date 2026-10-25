# 材料齐全判定（确定性代码）：上传接口、agent 清点节点共用，口径与前端 lib/materials.ts 一致
#
# 规则：审批表与合同都有才算齐全；缺验收报告只提醒。判断依据是文件类型（identified_type），
# 上传页律师改过的类型以改后的为准。齐不齐全只用于提示——律师在上传页点「材料不全，仍然分析」
# 后，分析照常进行，缺的字段留【待补充】（#69、#70）。
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

# 必须有的材料：缺了上传页按钮变为「材料不全，仍然分析」
REQUIRED_TYPES = ("审批表", "合同")
# 应当有的材料：缺了都列出来（验收报告缺失只提醒）
EXPECTED_TYPES = ("审批表", "合同", "验收报告")


@dataclass(frozen=True)
class MaterialStatus:
    missing: list[str]  # 缺少的材料类型（含验收报告），按 EXPECTED_TYPES 顺序
    can_proceed: bool  # 审批表与合同都有


def material_status(doc_types: Iterable[str]) -> MaterialStatus:
    """按各文件的类型判断材料是否齐全；doc_types 为每个文件的 identified_type。"""
    types = list(doc_types)
    present = set(types)
    return MaterialStatus(
        missing=[t for t in EXPECTED_TYPES if t not in present],
        can_proceed=bool(types) and all(t in present for t in REQUIRED_TYPES),
    )


def material_note(status: MaterialStatus) -> str:
    """给律师看的一句话说明；齐全时为空串。"""
    if not status.missing:
        return ""
    names = "、".join(status.missing)
    if status.can_proceed:
        return f"未上传{names}，相关字段可能缺失，请律师补充。"
    return f"未上传{names}，律师已选择按现有材料继续分析，相关字段留待补充。"
