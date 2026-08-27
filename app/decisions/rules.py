"""分类规则匹配：只匹配商户/交易对方与商品/商品说明（规格 §3.5）。

旅游类规则是否自动入账由引擎决定（§2.2：旅游必须用户确认），
匹配层不做过滤，保证旅游规则命中也能计入规则证据（红队修复 2026-08-14）。
"""

RULE_STATUS_OBSERVING = "observing"
RULE_STATUS_ACTIVE = "active"


def _text_contains(value: str, pattern: str) -> bool:
    pattern = pattern.strip()
    if not pattern:
        return False  # 防御：空模式不得匹配任何交易
    return pattern in value


def _rule_matches(rule, counterparty: str, item_desc: str) -> bool:
    if rule["match_field"] == "counterparty":
        return _text_contains(counterparty, rule["match_pattern"])
    return _text_contains(item_desc, rule["match_pattern"])


def match_rules(
    conn,
    counterparty: str,
    item_desc: str,
    target_category: str = "",
    platform: str = "",
    direction: str = "",
):
    """按优先级返回最匹配的启用规则；无命中返回 None。

    规则条件：match_field（商户/商品）+ platform（空为任意平台）+
    direction（空为任意方向）；优先级：active 优先于 observing，
    同状态取 id 最小（先创建）。
    """
    rows = conn.execute(
        """
        SELECT *
        FROM classification_rules
        WHERE status IN ('observing', 'active')
          AND (platform = '' OR platform = ?)
          AND (direction = '' OR direction = ?)
        ORDER BY
          CASE status WHEN 'active' THEN 0 ELSE 1 END,
          id ASC
        """,
        (platform, direction),
    ).fetchall()
    for rule in rows:
        if not _rule_matches(rule, counterparty, item_desc):
            continue
        if target_category and rule["target_category"] != target_category:
            continue
        return rule
    return None
