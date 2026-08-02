"""安全批次撤销（规格 §3.6/§6）。

撤销时删除未编辑、未被退款关联的批次交易；已编辑或关联过的记录
作为阻塞项明确列出，不能静默删除。整个撤销在单事务内完成。
"""

from dataclasses import dataclass, field

from .db import connect


@dataclass(frozen=True)
class BlockedItem:
    kind: str  # ledger | source
    ref_id: int
    reason: str


@dataclass(frozen=True)
class RevokeResult:
    batch_id: int
    deleted_sources: int
    deleted_ledger: int
    deleted_reviews: int
    blocked: list[BlockedItem] = field(default_factory=list)

    @property
    def blocked_count(self) -> int:
        return len(self.blocked)


def _sync_batch_pending(conn, batch_id: int) -> None:
    row = conn.execute(
        """
        SELECT COUNT(*) AS c FROM review_queue
        WHERE status = 'pending'
          AND source_transaction_id IN (
            SELECT id FROM source_transactions WHERE batch_id = ?
          )
        """,
        (batch_id,),
    ).fetchone()
    conn.execute(
        "UPDATE import_batches SET pending_count = ? WHERE id = ?",
        (int(row["c"]), batch_id),
    )


def revoke_batch(db_path, batch_id: int, *, note: str = "") -> RevokeResult:
    """撤销一个导入批次（单事务，失败完整回滚）。

    可删除：未人工编辑且未被退款关联的账本记录、待确认项、来源流水。
    阻塞项（明确列出，不删除）：
    - 人工编辑过的账本记录（manual_edited = 1）
    - 作为原消费被退款关联的账本记录
    - 已参与退款关联的退款来源流水
    """
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        batch = conn.execute(
            "SELECT * FROM import_batches WHERE id = ?", (batch_id,)
        ).fetchone()
        if batch is None:
            raise ValueError(f"batch not found: {batch_id}")
        if batch["status"] == "revoked":
            raise ValueError(f"batch already revoked: {batch_id}")

        source_ids = [
            int(row["id"])
            for row in conn.execute(
                "SELECT id FROM source_transactions WHERE batch_id = ?", (batch_id,)
            ).fetchall()
        ]
        if not source_ids:
            conn.execute(
                "UPDATE import_batches SET status = 'revoked', revoked_at = datetime('now'), revoke_note = ? WHERE id = ?",
                (note, batch_id),
            )
            conn.commit()
            return RevokeResult(batch_id=batch_id, deleted_sources=0, deleted_ledger=0, deleted_reviews=0)

        placeholders = ",".join("?" * len(source_ids))

        # ── 阻塞项收集 ──
        blocked: list[BlockedItem] = []

        ledger_rows = conn.execute(
            f"""
            SELECT le.id, le.manual_edited
            FROM ledger_entries AS le
            WHERE le.batch_id = ? OR le.source_transaction_id IN ({placeholders})
            """,
            (batch_id, *source_ids),
        ).fetchall()
        ledger_ids = [int(r["id"]) for r in ledger_rows]

        linked_ledger_ids = {
            int(r["original_ledger_id"])
            for r in conn.execute(
                f"SELECT original_ledger_id FROM refund_links WHERE original_ledger_id IN ({','.join('?' * len(ledger_ids))})"
                if ledger_ids
                else "SELECT original_ledger_id FROM refund_links WHERE 0",
                tuple(ledger_ids),
            ).fetchall()
        }
        linked_source_ids = {
            int(r["refund_source_id"])
            for r in conn.execute(
                f"SELECT refund_source_id FROM refund_links WHERE refund_source_id IN ({placeholders})",
                tuple(source_ids),
            ).fetchall()
        }

        for row in ledger_rows:
            ledger_id = int(row["id"])
            if ledger_id in linked_ledger_ids:
                blocked.append(BlockedItem("ledger", ledger_id, "refund_linked"))
            elif int(row["manual_edited"]) == 1:
                blocked.append(BlockedItem("ledger", ledger_id, "manual_edited"))

        blocked_source_ids = {int(sid) for sid in linked_source_ids}
        # 被保留（阻塞）账本引用的来源流水同样不能删（FK RESTRICT）
        kept_ledger_ids = {b.ref_id for b in blocked if b.kind == "ledger"}
        if kept_ledger_ids:
            for row in conn.execute(
                f"""
                SELECT source_transaction_id FROM ledger_entries
                WHERE id IN ({','.join('?' * len(kept_ledger_ids))})
                  AND source_transaction_id IS NOT NULL
                """,
                tuple(kept_ledger_ids),
            ).fetchall():
                if row["source_transaction_id"] is not None:
                    blocked_source_ids.add(int(row["source_transaction_id"]))
        for sid in sorted(blocked_source_ids):
            reason = (
                "refund_linked"
                if sid in linked_source_ids
                else "ledger_referenced"
            )
            blocked.append(BlockedItem("source", sid, reason))

        # ── 删除：待确认项（仅 pending 工作项；resolved 历史随来源流水级联清理） ──
        deleted_reviews = int(
            conn.execute(
                f"""
                DELETE FROM review_queue
                WHERE status = 'pending'
                  AND source_transaction_id IN ({placeholders})
                """,
                tuple(source_ids),
            ).rowcount
        )

        # ── 删除：可删账本记录（排除全部阻塞项） ──
        blocked_ledger_ids = {b.ref_id for b in blocked if b.kind == "ledger"}
        deletable_ledger = [i for i in ledger_ids if i not in blocked_ledger_ids]
        deleted_ledger = 0
        if deletable_ledger:
            deleted_ledger = int(
                conn.execute(
                    f"DELETE FROM ledger_entries WHERE id IN ({','.join('?' * len(deletable_ledger))})",
                    tuple(deletable_ledger),
                ).rowcount
            )

        # ── 删除：来源流水（排除被退款关联的） ──
        deletable_sources = [sid for sid in source_ids if sid not in blocked_source_ids]
        deleted_sources = 0
        if deletable_sources:
            deleted_sources = int(
                conn.execute(
                    f"DELETE FROM source_transactions WHERE id IN ({','.join('?' * len(deletable_sources))})",
                    tuple(deletable_sources),
                ).rowcount
            )

        conn.execute(
            "UPDATE import_batches SET status = 'revoked', revoked_at = datetime('now'), revoke_note = ? WHERE id = ?",
            (note, batch_id),
        )
        _sync_batch_pending(conn, batch_id)
        conn.commit()
        return RevokeResult(
            batch_id=batch_id,
            deleted_sources=deleted_sources,
            deleted_ledger=deleted_ledger,
            deleted_reviews=deleted_reviews,
            blocked=blocked,
        )
