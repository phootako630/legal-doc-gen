# 企业信息联网查询（预留）：按统一社会信用代码补全法人全称，替代 LLM 猜名称。
#
# 现状（对应 CLAUDE.md「联网工具」第 7 步）：
#   查询 API（企查查 / 天眼查 / 国家企业信用信息公示系统）暂未接入——本模块只
#   定义好接口与数据结构，lookup_company 恒返回 None；agent 据此走 HITL（human-in-
#   the-loop）：暂停问律师，由律师自行查询后键入法人全称。
#   将来接入时，只需在 lookup_company 内实现 HTTP 调用并解析为 CompanyInfo，
#   agent 会自动改为“有结果则填、无结果才问律师”，无需改编排层。
from __future__ import annotations

from dataclasses import dataclass

from app.config import COMPANY_LOOKUP_API_KEY


@dataclass(frozen=True)
class CompanyInfo:
    """企业工商信息（查询结果）。"""

    name: str  # 法人全称
    credit_code: str  # 统一社会信用代码
    legal_rep: str | None = None  # 法定代表人
    address: str | None = None  # 注册地址
    source: str = ""  # 数据来源（如“企查查”），用于 src 标注


async def lookup_company(credit_code: str | None) -> CompanyInfo | None:
    """
    按统一社会信用代码查询企业法人全称。

    预留实现：查询 API 尚未接入，恒返回 None（→ agent 转 HITL 让律师人工查询键入）。
    接入步骤（示例）：
      1. 在 .env 配置 COMPANY_LOOKUP_API_KEY，选定服务商（企查查/天眼查/公示系统）；
      2. 在此用 httpx/requests 发起查询，按信用代码取法人全称/法定代表人/地址；
      3. 解析为 CompanyInfo 返回；查询失败或无结果返回 None（保持 HITL 兜底）。
    """
    if not credit_code or not COMPANY_LOOKUP_API_KEY:
        return None
    # TODO(step7): 接入企查查/天眼查/公示系统 API。当前预留，未实现联网查询。
    return None
