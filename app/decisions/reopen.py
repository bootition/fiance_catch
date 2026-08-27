"""误操作退回待确认（用户反馈 2026-08-27）。

批量确认/高风险定性创建账本记录后，如果用户发现选错类型或分类，
可以按“单笔”或“规则组”把账本记录退回 review_queue 重新处理。
安全约束：已关联退款的记录、已人工编辑的记录不删除，明确阻塞。
"""

from dataclasses import dataclass

from ..db import connect
from ..ledger_repo import _add_audit_event


@dataclass(frozen=True)
class BlockedItem:
    entry_id: int
    reason: str


@dataclass(frozen=True)
class ReopenResult:
    reopened: int
    blocked: list[BlockedItem]

    @property
    def blocked_count(self) -> int:
        return len(self.blocked)


def _sync_batch_pending(conn, batch_id: int | None) -> None:
    if batch_id is None:
        return
    count = int(
        conn.execute(
            """
            SELECT COUNT(*) AS c FROM review_queue
            WHERE status = 'pending'
              AND source_transaction_id IN (
                SELECT id FROM source_transactions WHERE batch_id = ?
              )
            """,
            (batch_id,),
        ).fetchone()["c"]
    )
    conn.execute(
        "UPDATE import_batches SET pending_count = ? WHERE id = ?",
        (count, batch_id),
    )


def _rule_id_for_entry(conn, entry_id: int) -> int | None:
    row = conn.execute(
        """
        SELECT ref_rule_id FROM entry_audit_events
        WHERE ref_ledger_id = ? AND event_type = 'bulk_confirm' AND ref_rule_id IS NOT NULL
        ORDER BY id DESC LIMIT 1
        """,
        (entry_id,),
    ).fetchone()
    return None if row is None else int(row["ref_rule_id"])


def _reopen_entries(
    conn,
    entry_ids: list[int],
    *,
    rule_id: int | None,
) -> ReopenResult:
    reopened = 0
    blocked: list[BlockedItem] = []
    for entry_id in entry_ids:
        entry = conn.execute(
            "SELECT * FROM ledger_entries WHERE id = ?", (entry_id,)
        ).fetchone()
        if entry is None:
            continue
        linked = conn.execute(
            "SELECT id FROM refund_links WHERE original_ledger_id = ? LIMIT 1",
            (entry_id,),
        ).fetchone()
        if linked is not None:
            blocked.append(BlockedItem(entry_id, "refund_linked"))
            continue
        if int(entry["manual_edited"]) == 1:
            blocked.append(BlockedItem(entry_id, "manual_edited"))
            continue

        reviews = conn.execute(
            """
            SELECT id FROM review_queue
            WHERE resolved_ledger_id = ? AND status = 'resolved'
            """,
            (entry_id,),
        ).fetchall()
        review_ids = [int(r["id"]) for r in reviews]
        source_id = entry["source_transaction_id"]
        if not review_ids and source_id is not None:
            existing_pending = conn.execute(
                "SELECT id FROM review_queue WHERE source_transaction_id = ? AND status = 'pending' LIMIT 1",
                (source_id,),
            ).fetchone()
            if existing_pending is not None:
                blocked.append(BlockedItem(entry_id, "review_conflict"))
                continue
            cur = conn.execute(
                """
                INSERT INTO review_queue(source_transaction_id, reason, priority)
                VALUES (?, 'unmatched', 1)
                """,
                (source_id,),
            )
            review_ids = [int(cur.lastrowid)]

        conn.execute("DELETE FROM ledger_entries WHERE id = ?", (entry_id,))
        for review_id in review_ids:
            conn.execute(
                """
                UPDATE review_queue
                SET status = 'pending',
                    resolved_ledger_id = NULL,
                    resolved_at = NULL
                WHERE id = ?
                """,
                (review_id,),
            )
        effective_rule_id = rule_id if rule_id is not None else _rule_id_for_entry(conn, entry_id)
        _add_audit_event(
            conn,
            event_type="bulk_reopen",
            ref_ledger_id=entry_id,
            ref_rule_id=effective_rule_id,
            ref_batch_id=entry["batch_id"],
            detail=f"entry:{entry_id};back_to_inbox",
        )
        if effective_rule_id is not None:
            conn.execute(
                """
                UPDATE classification_rules
                SET confirm_count = MAX(0, confirm_count - 1),
                    updated_at = datetime('now')
                WHERE id = ?
                """,
                (effective_rule_id,),
            )
        _sync_batch_pending(conn, entry["batch_id"])
        reopened += 1
    return ReopenResult(reopened=reopened, blocked=blocked)


def reopen_ledger_entry(db_path, entry_id: int) -> ReopenResult:
    """把单条账本记录退回待确认（误操作纠正）。"""
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        result = _reopen_entries(conn, [entry_id], rule_id=None)
        conn.commit()
        return result


def reopen_rule_confirmations(db_path, rule_id: int) -> ReopenResult:
    """把某条规则名下批量确认产生的账本记录全部退回待确认。"""
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """
            SELECT DISTINCT le.id
            FROM entry_audit_events AS e
            JOIN ledger_entries AS le ON le.id = e.ref_ledger_id
            WHERE e.ref_rule_id = ? AND e.event_type = 'bulk_confirm'
            ORDER BY le.id ASC
            """,
            (rule_id,),
        ).fetchall()
        entry_ids = [int(r["id"]) for r in rows]
        if not entry_ids:
            raise ValueError(f"rule #{rule_id} has no confirmed ledger entries")
        result = _reopen_entries(conn, entry_ids, rule_id=rule_id)
        conn.commit()
        return result
