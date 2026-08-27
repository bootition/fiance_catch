"""规则筛选清扫（用户指引 2026-08-27）。

把历史“多订单合并批量确认”产生的账本记录重新过一遍当前规则：
- 命中内置交通规则或 active 用户规则 → 保留
- 未命中 → 退回待确认，等待用户重新筛选/总结规则后再自动筛选
- 已退款关联或已人工编辑的记录阻塞保留，不静默删除
"""

from dataclasses import dataclass

from ..db import connect
from ..ledger_repo import _add_audit_event, _bump_rule_stats, _create_ledger_entry
from .constants import REASON_OTHER_NEUTRAL, TYPE_TRANSFER
from .builtin_rules import (
    _sync_batch_pending,
    apply_builtin_rules_to_pending,
    matches_builtin_transport,
)
from ..refunds.linking import link_refund_to_ledger
from ..refunds.matching import find_refund_candidates
from .reopen import _reopen_entries
from .rules import RULE_STATUS_ACTIVE, _rule_matches, match_rules


@dataclass(frozen=True)
class RuleSweepResult:
    kept: int
    reopened: int
    blocked: int


def _passes_rules(conn, source) -> bool:
    if matches_builtin_transport(source):
        return True
    rule = match_rules(
        conn,
        source["counterparty"],
        source["item_desc"],
        platform=source["platform"],
        direction=source["direction"],
        raw_type=source["raw_type"],
    )
    return rule is not None and rule["status"] == RULE_STATUS_ACTIVE


def sweep_bulk_confirm_entries(db_path) -> RuleSweepResult:
    """清扫历史 bulk_confirm 账本记录（单事务）。"""
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """
            SELECT DISTINCT
              le.id AS entry_id,
              le.manual_edited,
              le.source_transaction_id,
              st.*
            FROM entry_audit_events AS e
            JOIN ledger_entries AS le ON le.id = e.ref_ledger_id
            JOIN source_transactions AS st ON st.id = le.source_transaction_id
            WHERE e.event_type = 'bulk_confirm'
            ORDER BY le.id ASC
            """
        ).fetchall()

        keep_ids: list[int] = []
        reopen_ids: list[int] = []
        blocked = 0
        for row in rows:
            entry_id = int(row["entry_id"])
            if int(row["manual_edited"]) == 1:
                blocked += 1
                continue
            linked = conn.execute(
                "SELECT id FROM refund_links WHERE original_ledger_id = ? LIMIT 1",
                (entry_id,),
            ).fetchone()
            if linked is not None:
                blocked += 1
                continue
            source = dict(row)
            if _passes_rules(conn, source):
                keep_ids.append(entry_id)
            else:
                reopen_ids.append(entry_id)

        result = _reopen_entries(conn, reopen_ids, rule_id=None)
        blocked += result.blocked_count
        conn.commit()
        return RuleSweepResult(
            kept=len(keep_ids),
            reopened=result.reopened,
            blocked=blocked,
        )


def apply_active_rules_to_pending(db_path) -> int:
    """把当前 active 用户规则应用到既有 unmatched 待确认项。

    用于用户后续流程：用户重新筛选/总结规则 → AI 显式写入并提升 →
    调用本函数自动筛选历史待确认。观察期规则不自动入账。
    """
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        rules = conn.execute(
            """
            SELECT * FROM classification_rules
            WHERE status = 'active'
            ORDER BY id ASC
            """
        ).fetchall()
        rows = conn.execute(
            """
            SELECT
              rq.id AS review_id,
              rq.source_transaction_id,
              st.*
            FROM review_queue AS rq
            JOIN source_transactions AS st ON st.id = rq.source_transaction_id
            WHERE rq.status = 'pending'
              AND rq.reason = 'unmatched'
            ORDER BY st.occurred_at ASC, st.id ASC
            """
        ).fetchall()
        posted = 0
        for row in rows:
            source = dict(row)
            matched = None
            for rule in rules:
                if str(rule["platform"] or "") not in ("", source["platform"]):
                    continue
                if str(rule["direction"] or "") not in ("", source["direction"]):
                    continue
                if not _rule_matches(
                    rule,
                    source["counterparty"],
                    source["item_desc"],
                    source["raw_type"],
                ):
                    continue
                matched = rule
                break
            if matched is None:
                continue
            entry_id = _create_ledger_entry(
                conn,
                entry_type=matched["target_type"],
                amount_cents=source["amount_cents"],
                category=matched["target_category"],
                txn_date=source["occurred_at"][:10],
                source_transaction_id=source["id"],
                batch_id=source["batch_id"],
                note="",
            )
            conn.execute(
                """
                UPDATE review_queue
                SET status = 'resolved',
                    resolved_ledger_id = ?,
                    resolved_at = datetime('now')
                WHERE id = ? AND status = 'pending'
                """,
                (entry_id, source["review_id"]),
            )
            _bump_rule_stats(conn, matched["id"], confirmed=False)
            _add_audit_event(
                conn,
                event_type="rule_applied",
                ref_ledger_id=entry_id,
                ref_rule_id=matched["id"],
                ref_batch_id=source["batch_id"],
                detail=f"field:{matched['match_field']};pattern:{matched['match_pattern']}",
            )
            _sync_batch_pending(conn, source["batch_id"])
            posted += 1
        conn.commit()
        return posted


def apply_transfer_rules_to_neutral_pending(db_path) -> int:
    """把 active 调拨规则应用到高风险区 other_neutral 待确认（如基金买入）。"""
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        rules = conn.execute(
            """
            SELECT * FROM classification_rules
            WHERE status = 'active' AND target_type = 'transfer'
            ORDER BY id ASC
            """
        ).fetchall()
        rows = conn.execute(
            """
            SELECT
              rq.id AS review_id,
              rq.source_transaction_id,
              st.*
            FROM review_queue AS rq
            JOIN source_transactions AS st ON st.id = rq.source_transaction_id
            WHERE rq.status = 'pending'
              AND rq.reason = ?
            ORDER BY st.occurred_at ASC, st.id ASC
            """,
            (REASON_OTHER_NEUTRAL,),
        ).fetchall()
        posted = 0
        for row in rows:
            source = dict(row)
            matched = None
            for rule in rules:
                if str(rule["platform"] or "") not in ("", source["platform"]):
                    continue
                if str(rule["direction"] or "") not in ("", source["direction"]):
                    continue
                if not _rule_matches(
                    rule,
                    source["counterparty"],
                    source["item_desc"],
                    source["raw_type"],
                ):
                    continue
                matched = rule
                break
            if matched is None:
                continue
            entry_id = _create_ledger_entry(
                conn,
                entry_type=TYPE_TRANSFER,
                amount_cents=source["amount_cents"],
                category=matched["target_category"],
                txn_date=source["occurred_at"][:10],
                source_transaction_id=source["id"],
                batch_id=source["batch_id"],
                note="",
            )
            conn.execute(
                """
                UPDATE review_queue
                SET status = 'resolved',
                    resolved_ledger_id = ?,
                    resolved_at = datetime('now')
                WHERE id = ? AND status = 'pending'
                """,
                (entry_id, source["review_id"]),
            )
            _bump_rule_stats(conn, matched["id"], confirmed=False)
            _add_audit_event(
                conn,
                event_type="rule_applied",
                ref_ledger_id=entry_id,
                ref_rule_id=matched["id"],
                ref_batch_id=source["batch_id"],
                detail=f"field:{matched['match_field']};pattern:{matched['match_pattern']}",
            )
            _sync_batch_pending(conn, source["batch_id"])
            posted += 1
        conn.commit()
        return posted


def apply_person_transfer_rules(db_path) -> int:
    """把 active 消费/收入规则应用到高风险区 person_transfer（如单挑啊转账→日常缴费）。"""
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        rules = conn.execute(
            """
            SELECT * FROM classification_rules
            WHERE status = 'active' AND target_type IN ('consumption', 'income')
            ORDER BY id ASC
            """
        ).fetchall()
        rows = conn.execute(
            """
            SELECT
              rq.id AS review_id,
              rq.source_transaction_id,
              st.*
            FROM review_queue AS rq
            JOIN source_transactions AS st ON st.id = rq.source_transaction_id
            WHERE rq.status = 'pending'
              AND rq.reason = 'person_transfer'
            ORDER BY st.occurred_at ASC, st.id ASC
            """
        ).fetchall()
        posted = 0
        for row in rows:
            source = dict(row)
            matched = None
            for rule in rules:
                if str(rule["platform"] or "") not in ("", source["platform"]):
                    continue
                if str(rule["direction"] or "") not in ("", source["direction"]):
                    continue
                if not _rule_matches(
                    rule,
                    source["counterparty"],
                    source["item_desc"],
                    source["raw_type"],
                ):
                    continue
                matched = rule
                break
            if matched is None:
                continue
            entry_id = _create_ledger_entry(
                conn,
                entry_type=matched["target_type"],
                amount_cents=source["amount_cents"],
                category=matched["target_category"],
                txn_date=source["occurred_at"][:10],
                source_transaction_id=source["id"],
                batch_id=source["batch_id"],
                note="",
            )
            conn.execute(
                """
                UPDATE review_queue
                SET status = 'resolved',
                    resolved_ledger_id = ?,
                    resolved_at = datetime('now')
                WHERE id = ? AND status = 'pending'
                """,
                (entry_id, source["review_id"]),
            )
            _bump_rule_stats(conn, matched["id"], confirmed=False)
            _add_audit_event(
                conn,
                event_type="rule_applied",
                ref_ledger_id=entry_id,
                ref_rule_id=matched["id"],
                ref_batch_id=source["batch_id"],
                detail=f"field:{matched['match_field']};pattern:{matched['match_pattern']}",
            )
            _sync_batch_pending(conn, source["batch_id"])
            posted += 1
        conn.commit()
        return posted


def auto_link_unambiguous_refunds(db_path) -> int:
    """把只剩唯一候选的退款自动冲销原消费（用户指引 2026-08-28）。

    只有候选唯一时才自动关联；多个候选仍需人工选择。
    """
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT rq.id AS review_id, rq.source_transaction_id
            FROM review_queue AS rq
            WHERE rq.status = 'pending' AND rq.reason = 'refund_pending'
            ORDER BY rq.id ASC
            """
        ).fetchall()
    linked = 0
    for row in rows:
        candidates = find_refund_candidates(db_path, int(row["source_transaction_id"]))
        if len(candidates) != 1:
            continue
        try:
            link_refund_to_ledger(
                db_path,
                int(row["source_transaction_id"]),
                int(candidates[0].ledger_id),
            )
            linked += 1
        except ValueError:
            continue
    return linked


def apply_all_rules_to_pending(db_path) -> int:
    """内置规则 + active 用户规则，全部应用到 unmatched 待确认。"""
    total = apply_builtin_rules_to_pending(db_path).posted
    total += apply_active_rules_to_pending(db_path)
    total += apply_transfer_rules_to_neutral_pending(db_path)
    total += apply_person_transfer_rules(db_path)
    total += auto_link_unambiguous_refunds(db_path)
    return total
