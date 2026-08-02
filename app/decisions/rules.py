"""分类规则匹配：只匹配商户/交易对方与商品/商品说明（规格 §3.5）。"""

from .constants import CATEGORY_TRAVEL

RULE_STATUS_OBSERVING = "observing"
RULE_STATUS_ACTIVE = "active"


def _text_contains(value: str, pattern: str) -> bool:
    return pattern in value


def _rule_matches(rule, counterparty: str, item_desc: str) -> bool:
    if rule["match_field"] == "counterparty":
        return _text_contains(counterparty, rule["match_pattern"])
    return _text_contains(item_desc, rule["match_pattern"])


def match_rules(conn, counterparty: str, item_desc: str, target_category: str = ""):
    """按优先级返回最匹配的启用规则；无命中返回 None。

    优先级：active 优先于 observing；同状态取 id 最小（先创建）。
    """
    rows = conn.execute(
        """
        SELECT *
        FROM classification_rules
        WHERE status IN ('observing', 'active')
        ORDER BY
          CASE status WHEN 'active' THEN 0 ELSE 1 END,
          id ASC
        """
    ).fetchall()
    for rule in rows:
        if not _rule_matches(rule, counterparty, item_desc):
            continue
        if rule["target_category"] == CATEGORY_TRAVEL:
            continue  # 旅游类即使 active 也由用户确认（规格 §2.2）
        if target_category and rule["target_category"] != target_category:
            continue
        return rule
    return None
