"""正式分类与交易类型常量（规格 §2.1/§2.2）及平台特征识别关键词。"""

# 正式分类
CATEGORY_DAILY_MEALS = "日常三餐"
CATEGORY_TRANSPORT = "出行交通"
CATEGORY_LEARNING = "书籍学习"
CATEGORY_ENTERTAINMENT = "日常娱乐"
CATEGORY_TRAVEL = "旅游"
CATEGORY_DAILY_EXPENSES = "日常缴费"
CATEGORY_MEDICAL = "医疗健康"
CATEGORY_CLOTHING = "衣物用品"
CATEGORY_IQ_TAX = "被坑智商税"
CATEGORY_UNEXPECTED = "突发消费"
CATEGORY_EXERCISE = "锻炼花费"
CATEGORY_SIDE_COST = "副业成本"
CATEGORY_SIDE_INCOME = "副业收入"
CATEGORY_OTHER_INCOME = "其他收入"
CATEGORY_LIVING_ALLOWANCE = "生活费"

# 调拨专用分类（用户指引 2026-08-27）：与消费/收入分类分离
CATEGORY_DIVIDEND_STOCKS = "攒股收息"
CATEGORY_HARRY_BROWN = "哈利布朗"
CATEGORY_CRYPTO = "虚拟货币"
CATEGORY_CS_SKINS = "CS饰品"
TRANSFER_CATEGORIES = (
    CATEGORY_DIVIDEND_STOCKS,
    CATEGORY_HARRY_BROWN,
    CATEGORY_CRYPTO,
    CATEGORY_CS_SKINS,
)

# 正式分类全集（PRD §2.2），UI 分类建议与兜底选项以此为准
FORMAL_CATEGORIES = (
    CATEGORY_DAILY_MEALS,
    CATEGORY_TRANSPORT,
    CATEGORY_LEARNING,
    CATEGORY_ENTERTAINMENT,
    CATEGORY_TRAVEL,
    CATEGORY_DAILY_EXPENSES,
    CATEGORY_MEDICAL,
    CATEGORY_CLOTHING,
    CATEGORY_IQ_TAX,
    CATEGORY_UNEXPECTED,
    CATEGORY_EXERCISE,
    CATEGORY_SIDE_COST,
    CATEGORY_SIDE_INCOME,
    CATEGORY_OTHER_INCOME,
    CATEGORY_LIVING_ALLOWANCE,
    CATEGORY_DIVIDEND_STOCKS,
    CATEGORY_HARRY_BROWN,
    CATEGORY_CRYPTO,
    CATEGORY_CS_SKINS,
)

# 正式交易类型（ledger_entries.entry_type）
TYPE_CONSUMPTION = "consumption"
TYPE_INCOME = "income"
TYPE_TRANSFER = "transfer"
TYPE_REFUND = "refund"

# 批量指定/规则目标类型（不含退款：退款走受约束关联流程）
BULK_TYPES = (TYPE_CONSUMPTION, TYPE_INCOME, TYPE_TRANSFER)

# 账单方向已知：分类区允许的类型必须与方向一致（用户反馈 2026-08-27）
# income 只能是收入；expense 可以是消费或调拨；neutral 只能是调拨
DIRECTION_ALLOWED_BULK_TYPES = {
    "income": frozenset({TYPE_INCOME}),
    "expense": frozenset({TYPE_CONSUMPTION, TYPE_TRANSFER}),
    "neutral": frozenset({TYPE_TRANSFER}),
}

# 交易类型 → 可选正式分类（PRD §2.2/§3.3）
CATEGORY_OPTIONS_BY_TYPE = {
    TYPE_CONSUMPTION: (
        CATEGORY_DAILY_MEALS,
        CATEGORY_TRANSPORT,
        CATEGORY_LEARNING,
        CATEGORY_ENTERTAINMENT,
        CATEGORY_TRAVEL,
        CATEGORY_DAILY_EXPENSES,
        CATEGORY_MEDICAL,
        CATEGORY_CLOTHING,
        CATEGORY_IQ_TAX,
        CATEGORY_UNEXPECTED,
        CATEGORY_EXERCISE,
        CATEGORY_SIDE_COST,
    ),
    TYPE_INCOME: (
        CATEGORY_SIDE_INCOME,
        CATEGORY_OTHER_INCOME,
        CATEGORY_LIVING_ALLOWANCE,
    ),
    TYPE_TRANSFER: TRANSFER_CATEGORIES,
}

# 待确认原因
REASON_REFUND_PENDING = "refund_pending"  # 退款待办（阶段 4 匹配原消费）
REASON_WITHDRAWAL = "withdrawal"  # 提现到银行卡：必须逐笔选用途
REASON_PERSON_TRANSFER = "person_transfer"  # 人际转账/红包/收款
REASON_OTHER_NEUTRAL = "other_neutral"  # 其他不计收支资金流
REASON_UNMATCHED = "unmatched"  # 未命中规则
REASON_OBSERVING_RULE = "observing_rule"  # 观察期规则预填
REASON_TRAVEL = "travel"  # 旅游类交易需用户确认

# 提现识别（提现到银行卡必须逐笔选用途，绝不自动归为投资/消费）
WITHDRAWAL_KEYWORDS = ("提现", "取现")

# 调拨识别：平台余额/理财账户间资金移动，不计消费或收入。
# 仅作用于“不计收支/中性”方向分支，正常消费的充值（如话费）不受影响。
TRANSFER_KEYWORDS = ("余额宝", "零钱通", "理财通", "充值")

# 人际转账/红包/收款：一律停在待确认，不根据平台收/支字段自动定性
ALIPAY_PERSON_TYPES = {"转账红包", "亲友代付", "红包"}
WECHAT_PERSON_TYPES = {"转账", "微信红包"}
PERSON_KEYWORDS = ("转账", "红包", "收款")

# 副业收入关键词（闲鱼虚拟资料经营收入，仅作预填建议，仍须用户确认）
SIDE_INCOME_KEYWORDS = ("闲鱼", "虚拟资料")
