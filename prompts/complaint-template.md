# 起诉状模板常量

本文件供 prompt_loader.py 作为常量注入，定义起诉状的固定格式段落。

## 诉讼请求标准表述（安装合同货款纠纷）

一、判令被告立即向原告支付拖欠的安装工程款{{unpaid_amount}}元（{{amount_currency}}），
    并自{{acceptance_latest_date}}起至实际清偿之日止，按{{breach_interest_rate_text}}支付逾期付款利息；

二、本案诉讼费用由被告承担。

## 事实与理由标准段落

原、被告双方于{{contract_sign_date}}签订《{{contract_title}}》（合同编号：{{contract_no}}），
约定由原告为被告位于{{project_site}}的项目安装电梯共{{elevator_qty}}台，
合同总价款为{{total_amount}}元（{{amount_currency}}）。

合同签订后，原告按约履行了全部安装义务，并于{{acceptance_latest_date}}前完成验收。
被告应依约支付合同款项，但截至起诉之日，被告仅支付{{paid_amount}}元，
尚欠{{unpaid_amount}}元拒不支付，严重违反合同约定。

依据《中华人民共和国民法典》第五百七十七条、第五百八十条等相关规定，
原告特向贵院提起诉讼，恳请支持原告诉讼请求。
