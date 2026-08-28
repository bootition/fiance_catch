import re
import sqlite3
from datetime import date as dt_date
from datetime import datetime as dt_datetime

from .db import connect
from .decisions.constants import DIRECTION_ALLOWED_BULK_TYPES

_ISO_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _validate_occurred_at(value: str) -> str:
    """校验来源流水时间必须为 YYYY-MM-DD HH:MM:SS（含真实日历日）。"""
    if not _ISO_DATETIME_RE.fullmatch(value):
        raise ValueError(f"invalid occurred_at: {value!r}")
    try:
        dt_datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid occurred_at: {value!r}") from exc
    return value


def _validate_txn_date(value: str) -> str:
    """校验账本日期必须为 YYYY-MM-DD（含真实日历日）。"""
    if not _ISO_DATE_RE.fullmatch(value):
        raise ValueError(f"invalid txn_date: {value!r}")
    try:
        dt_date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid txn_date: {value!r}") from exc
    return value

BATCH_STATUS_ACTIVE = "active"
BATCH_STATUS_REVOKED = "revoked"

RULE_STATUS_OBSERVING = "observing"
RULE_STATUS_ACTIVE = "active"
RULE_STATUS_DISABLED = "disabled"

REVIEW_PENDING = "pending"
REVIEW_RESOLVED = "resolved"
REVIEW_DISMISSED = "dismissed"


# ── Import batches ──


def create_import_batch(
    db_path,
    *,
    file_name: str,
    platform: str,
    file_fingerprint: str,
) -> int:
    with connect(db_path) as conn:
        return _create_import_batch(
            conn,
            file_name=file_name,
            platform=platform,
            file_fingerprint=file_fingerprint,
        )


def _create_import_batch(
    conn,
    *,
    file_name: str,
    platform: str,
    file_fingerprint: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO import_batches(file_name, platform, file_fingerprint)
        VALUES (?, ?, ?)
        """,
        (file_name, platform, file_fingerprint),
    )
    return int(cur.lastrowid)


def get_import_batch(db_path, batch_id: int):
    with connect(db_path) as conn:
        return conn.execute(
            """
            SELECT * FROM import_batches WHERE id = ?
            """,
            (batch_id,),
        ).fetchone()


def update_batch_counts(
    db_path,
    batch_id: int,
    *,
    row_count: int,
    accepted_count: int,
    skipped_count: int,
    pending_count: int,
) -> bool:
    with connect(db_path) as conn:
        return _update_batch_counts(
            conn,
            batch_id,
            row_count=row_count,
            accepted_count=accepted_count,
            skipped_count=skipped_count,
            pending_count=pending_count,
        )


def _update_batch_counts(
    conn,
    batch_id: int,
    *,
    row_count: int,
    accepted_count: int,
    skipped_count: int,
    pending_count: int,
) -> bool:
    cur = conn.execute(
        """
        UPDATE import_batches
        SET
          row_count = ?,
          accepted_count = ?,
          skipped_count = ?,
          pending_count = ?
        WHERE id = ?
        """,
        (row_count, accepted_count, skipped_count, pending_count, batch_id),
    )
    return int(cur.rowcount) > 0


def revoke_batch(db_path, batch_id: int, *, note: str = "") -> bool:
    with connect(db_path) as conn:
        cur = conn.execute(
            """
            UPDATE import_batches
            SET status = ?, revoked_at = datetime('now'), revoke_note = ?
            WHERE id = ? AND status = ?
            """,
            (BATCH_STATUS_REVOKED, note, batch_id, BATCH_STATUS_ACTIVE),
        )
        return int(cur.rowcount) > 0


def list_import_batches(db_path, *, limit: int = 100):
    safe_limit = max(1, min(int(limit), 1000))
    with connect(db_path) as conn:
        cur = conn.execute(
            """
            SELECT * FROM import_batches
            ORDER BY imported_at DESC, id DESC
            LIMIT ?
            """,
            (safe_limit,),
        )
        return cur.fetchall()


# ── Source transactions ──


def insert_source_transaction(
    db_path,
    *,
    platform: str,
    source_txn_id: str,
    occurred_at: str,
    amount_cents: int,
    direction: str,
    status_text: str,
    counterparty: str = "",
    item_desc: str = "",
    raw_type: str = "",
    note: str = "",
    batch_id: int | None = None,
    normalized_hash: str,
) -> tuple[int, bool]:
    """插入来源流水；重复（同平台同单号）返回 (已有id, False)。"""
    with connect(db_path) as conn:
        return _insert_source_transaction(
            conn,
            platform=platform,
            source_txn_id=source_txn_id,
            occurred_at=occurred_at,
            amount_cents=amount_cents,
            direction=direction,
            status_text=status_text,
            counterparty=counterparty,
            item_desc=item_desc,
            raw_type=raw_type,
            note=note,
            batch_id=batch_id,
            normalized_hash=normalized_hash,
        )


def _insert_source_transaction(
    conn,
    *,
    platform: str,
    source_txn_id: str,
    occurred_at: str,
    amount_cents: int,
    direction: str,
    status_text: str,
    counterparty: str = "",
    item_desc: str = "",
    raw_type: str = "",
    note: str = "",
    batch_id: int | None = None,
    normalized_hash: str,
) -> tuple[int, bool]:
    occurred_at = _validate_occurred_at(occurred_at)
    existing = conn.execute(
        """
        SELECT id FROM source_transactions
        WHERE platform = ? AND source_txn_id = ?
        """,
        (platform, source_txn_id),
    ).fetchone()
    if existing is not None:
        return int(existing["id"]), False
    cur = conn.execute(
        """
        INSERT INTO source_transactions(
          platform,
          source_txn_id,
          occurred_at,
          amount_cents,
          direction,
          status_text,
          counterparty,
          item_desc,
          raw_type,
          note,
          batch_id,
          normalized_hash
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            platform,
            source_txn_id,
            occurred_at,
            amount_cents,
            direction,
            status_text,
            counterparty,
            item_desc,
            raw_type,
            note,
            batch_id,
            normalized_hash,
        ),
    )
    return int(cur.lastrowid), True


def get_source_transaction(db_path, source_txn_id: int):
    with connect(db_path) as conn:
        return conn.execute(
            """
            SELECT * FROM source_transactions WHERE id = ?
            """,
            (source_txn_id,),
        ).fetchone()


def list_source_transactions(
    db_path,
    *,
    platform: str | None = None,
    batch_id: int | None = None,
    limit: int = 500,
):
    clauses: list[str] = []
    params: list = []
    if platform:
        clauses.append("platform = ?")
        params.append(platform)
    if batch_id is not None:
        clauses.append("batch_id = ?")
        params.append(batch_id)
    where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    safe_limit = max(1, min(int(limit), 2000))
    with connect(db_path) as conn:
        cur = conn.execute(
            f"""
            SELECT * FROM source_transactions{where_sql}
            ORDER BY occurred_at DESC, id DESC
            LIMIT ?
            """,
            tuple(params + [safe_limit]),
        )
        return cur.fetchall()


# ── Ledger entries ──


def create_ledger_entry(
    db_path,
    *,
    entry_type: str,
    amount_cents: int,
    category: str = "",
    txn_date: str,
    source_transaction_id: int | None = None,
    batch_id: int | None = None,
    note: str = "",
) -> int:
    with connect(db_path) as conn:
        return _create_ledger_entry(
            conn,
            entry_type=entry_type,
            amount_cents=amount_cents,
            category=category,
            txn_date=txn_date,
            source_transaction_id=source_transaction_id,
            batch_id=batch_id,
            note=note,
        )


def _create_ledger_entry(
    conn,
    *,
    entry_type: str,
    amount_cents: int,
    category: str = "",
    txn_date: str,
    source_transaction_id: int | None = None,
    batch_id: int | None = None,
    note: str = "",
) -> int:
    txn_date = _validate_txn_date(txn_date)
    cur = conn.execute(
        """
        INSERT INTO ledger_entries(
          entry_type,
          amount_cents,
          category,
          txn_date,
          source_transaction_id,
          batch_id,
          note
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entry_type,
            amount_cents,
            category,
            txn_date,
            source_transaction_id,
            batch_id,
            note,
        ),
    )
    return int(cur.lastrowid)


def get_ledger_entry(db_path, entry_id: int):
    with connect(db_path) as conn:
        return conn.execute(
            """
            SELECT * FROM ledger_entries WHERE id = ?
            """,
            (entry_id,),
        ).fetchone()


def list_ledger_entries(db_path, *, limit: int = 2000):
    safe_limit = max(1, min(int(limit), 10000))
    with connect(db_path) as conn:
        cur = conn.execute(
            """
            SELECT * FROM ledger_entries
            ORDER BY txn_date DESC, id DESC
            LIMIT ?
            """,
            (safe_limit,),
        )
        return cur.fetchall()


def update_ledger_entry(
    db_path,
    entry_id: int,
    *,
    entry_type: str,
    amount_cents: int,
    category: str,
    txn_date: str,
    note: str,
) -> bool:
    """更新账本记录（仓储层强制退款不变量，红队 P1 修复）。

    - 已关联退款的记录：entry_type 不可改变（防破坏退款关联语义）
    - 消费记录：新金额不得小于已关联退款总额（防负净额）
    """
    txn_date = _validate_txn_date(txn_date)
    with connect(db_path) as conn:
        entry = conn.execute(
            "SELECT * FROM ledger_entries WHERE id = ?", (entry_id,)
        ).fetchone()
        if entry is None:
            return False
        refunded = int(
            conn.execute(
                """
                SELECT COALESCE(SUM(refund_amount_cents), 0) AS c
                FROM refund_links WHERE original_ledger_id = ?
                """,
                (entry_id,),
            ).fetchone()["c"]
        )
        if refunded > 0 and entry_type != entry["entry_type"]:
            raise ValueError(
                "cannot change entry type of a refund-linked record"
            )
        if entry_type == "consumption" and amount_cents < refunded:
            raise ValueError(
                f"amount {amount_cents} below linked refund total {refunded}"
            )
        cur = conn.execute(
            """
            UPDATE ledger_entries
            SET
              entry_type = ?,
              amount_cents = ?,
              category = ?,
              txn_date = ?,
              note = ?,
              manual_edited = 1,
              updated_at = datetime('now')
            WHERE id = ?
            """,
            (entry_type, amount_cents, category, txn_date, note, entry_id),
        )
        if cur.rowcount > 0:
            _add_audit_event(
                conn,
                event_type="manual_edit",
                ref_ledger_id=entry_id,
                ref_batch_id=entry["batch_id"],
                detail=(
                    f"before:type:{entry['entry_type']};amount:{entry['amount_cents']};"
                    f"category:{entry['category']};date:{entry['txn_date']};"
                    f"after:type:{entry_type};amount:{amount_cents};"
                    f"category:{category};date:{txn_date}"
                ),
            )
        return int(cur.rowcount) > 0


def delete_ledger_entry(db_path, entry_id: int) -> bool:
    """删除账本记录；已关联退款的记录拒绝删除（FK RESTRICT 冲突转为业务错误）。"""
    with connect(db_path) as conn:
        linked = conn.execute(
            "SELECT id FROM refund_links WHERE original_ledger_id = ? LIMIT 1",
            (entry_id,),
        ).fetchone()
        if linked is not None:
            raise ValueError(
                f"record #{entry_id} has linked refunds, cannot delete"
            )
        cur = conn.execute(
            "DELETE FROM ledger_entries WHERE id = ?", (entry_id,)
        )
        return int(cur.rowcount) > 0


# ── Review queue ──


def enqueue_review(
    db_path,
    *,
    source_transaction_id: int,
    reason: str,
    priority: int = 1,
    suggested_category: str = "",
    suggested_type: str = "",
) -> int:
    with connect(db_path) as conn:
        return _enqueue_review(
            conn,
            source_transaction_id=source_transaction_id,
            reason=reason,
            priority=priority,
            suggested_category=suggested_category,
            suggested_type=suggested_type,
        )


def _enqueue_review(
    conn,
    *,
    source_transaction_id: int,
    reason: str,
    priority: int = 1,
    suggested_category: str = "",
    suggested_type: str = "",
) -> int:
    cur = conn.execute(
        """
        INSERT INTO review_queue(
          source_transaction_id,
          reason,
          priority,
          suggested_category,
          suggested_type
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            source_transaction_id,
            reason,
            priority,
            suggested_category,
            suggested_type,
        ),
    )
    return int(cur.lastrowid)


def list_review_queue(db_path, *, status: str = REVIEW_PENDING, limit: int = 500):
    safe_limit = max(1, min(int(limit), 2000))
    with connect(db_path) as conn:
        cur = conn.execute(
            """
            SELECT * FROM review_queue
            WHERE status = ?
            ORDER BY priority DESC, id ASC
            LIMIT ?
            """,
            (status, safe_limit),
        )
        return cur.fetchall()


def resolve_review(
    db_path,
    review_id: int,
    *,
    resolved_ledger_id: int | None,
    status: str = REVIEW_RESOLVED,
) -> bool:
    with connect(db_path) as conn:
        cur = conn.execute(
            """
            UPDATE review_queue
            SET status = ?, resolved_ledger_id = ?, resolved_at = datetime('now')
            WHERE id = ? AND status = ?
            """,
            (status, resolved_ledger_id, review_id, REVIEW_PENDING),
        )
        return int(cur.rowcount) > 0


# ── Refund links ──


def _link_refund(
    conn,
    *,
    refund_source_id: int,
    original_ledger_id: int,
    refund_amount_cents: int,
) -> int:
    """私有连接级写入：仅供受约束退款服务（link_refund_to_ledger）在事务内调用。

    不对外公开：所有业务不变量（退款来源真实性、待办、超额、一对一）由
    app/refunds/linking.py 统一校验。
    """
    cur = conn.execute(
        """
        INSERT INTO refund_links(refund_source_id, original_ledger_id, refund_amount_cents)
        VALUES (?, ?, ?)
        """,
        (refund_source_id, original_ledger_id, refund_amount_cents),
    )
    return int(cur.lastrowid)


def list_refund_links(db_path, *, original_ledger_id: int | None = None):
    with connect(db_path) as conn:
        if original_ledger_id is None:
            return conn.execute(
                """
                SELECT * FROM refund_links
                ORDER BY linked_at DESC, id DESC
                """
            ).fetchall()
        return conn.execute(
            """
            SELECT * FROM refund_links
            WHERE original_ledger_id = ?
            ORDER BY linked_at DESC, id DESC
            """,
            (original_ledger_id,),
        ).fetchall()


def list_consumption_with_refunds(db_path, *, start: str, end: str):
    """消费账本（含已关联退款总额与净成本）。

    跨期退款按原消费周期回写：退款金额归入原消费的 txn_date 周期。
    """
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT
              le.id,
              le.amount_cents,
              le.category,
              le.txn_date,
              le.note,
              le.manual_edited,
              le.source_transaction_id,
              le.batch_id,
              COALESCE(SUM(rl.refund_amount_cents), 0) AS refunded_cents
            FROM ledger_entries AS le
            LEFT JOIN refund_links AS rl ON rl.original_ledger_id = le.id
            WHERE le.entry_type = 'consumption'
              AND le.txn_date >= ? AND le.txn_date <= ?
            GROUP BY le.id
            ORDER BY le.txn_date DESC, le.id DESC
            """,
            (start, end),
        ).fetchall()
        return [
            {
                **dict(row),
                "refunded_cents": int(row["refunded_cents"]),
                "net_cost_cents": int(row["amount_cents"]) - int(row["refunded_cents"]),
            }
            for row in rows
        ]


# ── Classification rules ──


def create_classification_rule(
    db_path,
    *,
    match_field: str,
    match_pattern: str,
    target_type: str,
    target_category: str = "",
    platform: str = "",
    direction: str = "",
) -> int:
    with connect(db_path) as conn:
        return _create_classification_rule(
            conn,
            match_field=match_field,
            match_pattern=match_pattern,
            target_type=target_type,
            target_category=target_category,
            platform=platform,
            direction=direction,
        )


def _create_classification_rule(
    conn,
    *,
    match_field: str,
    match_pattern: str,
    target_type: str,
    target_category: str = "",
    platform: str = "",
    direction: str = "",
) -> int:
    pattern = (match_pattern or "").strip()
    if not pattern:
        raise ValueError("match_pattern required")
    if match_field not in ("counterparty", "item_desc", "raw_type"):
        raise ValueError("invalid match_field")
    if platform not in ("", "alipay", "wechat"):
        raise ValueError("invalid rule platform")
    if direction not in ("", "expense", "income", "neutral"):
        raise ValueError("invalid rule direction")
    if match_field == "counterparty" and "*" in pattern:
        raise ValueError("商户名已脱敏，不能作为规则条件；请改用商品/商品说明")
    if direction:
        allowed_types = DIRECTION_ALLOWED_BULK_TYPES.get(direction, frozenset())
        if target_type not in allowed_types:
            raise ValueError(
                f"规则方向与目标类型不一致：{direction} 不能写入 {target_type}"
            )
    cur = conn.execute(
        """
        INSERT INTO classification_rules(
          match_field,
          match_pattern,
          platform,
          direction,
          target_type,
          target_category
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (match_field, pattern, platform, direction, target_type, target_category),
    )
    return int(cur.lastrowid)


def list_classification_rules(db_path, *, status: str | None = None):
    with connect(db_path) as conn:
        if status is None:
            return conn.execute(
                """
                SELECT * FROM classification_rules
                ORDER BY id ASC
                """
            ).fetchall()
        return conn.execute(
            """
            SELECT * FROM classification_rules
            WHERE status = ?
            ORDER BY id ASC
            """,
            (status,),
        ).fetchall()


def update_rule_status(db_path, rule_id: int, status: str) -> bool:
    """规则状态机（服务层强制，红队 P1 修复）。

    - observing → active：必须经 promote_rule()（观察期语义，不可直跳）
    - observing/active → disabled：允许停用
    - disabled → active：允许重新启用
    """
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT status, target_category FROM classification_rules WHERE id = ?",
            (rule_id,),
        ).fetchone()
        if row is None:
            return False
        current = row["status"]
        if status == "active":
            if current not in ("disabled",):
                return False
            if row["target_category"] == "旅游":
                return False  # 旅游类规则禁止自动入账（规格 §2.2，红队修复 2026-08-14）
        elif status == "disabled":
            if current not in ("observing", "active"):
                return False
        else:
            return False
        cur = conn.execute(
            """
            UPDATE classification_rules
            SET status = ?, updated_at = datetime('now')
            WHERE id = ?
            """,
            (status, rule_id),
        )
        return int(cur.rowcount) > 0


def bump_rule_stats(db_path, rule_id: int, *, confirmed: bool) -> bool:
    with connect(db_path) as conn:
        return _bump_rule_stats(conn, rule_id, confirmed=confirmed)


def _bump_rule_stats(conn, rule_id: int, *, confirmed: bool) -> bool:
    column = "confirm_count" if confirmed else "hit_count"
    cur = conn.execute(
        f"""
        UPDATE classification_rules
        SET {column} = {column} + 1, updated_at = datetime('now')
        WHERE id = ?
        """,
        (rule_id,),
    )
    return int(cur.rowcount) > 0


# ── Audit events ──


def add_audit_event(
    db_path,
    *,
    event_type: str,
    ref_ledger_id: int | None = None,
    ref_rule_id: int | None = None,
    ref_batch_id: int | None = None,
    detail: str = "",
) -> int:
    with connect(db_path) as conn:
        return _add_audit_event(
            conn,
            event_type=event_type,
            ref_ledger_id=ref_ledger_id,
            ref_rule_id=ref_rule_id,
            ref_batch_id=ref_batch_id,
            detail=detail,
        )


def _add_audit_event(
    conn,
    *,
    event_type: str,
    ref_ledger_id: int | None = None,
    ref_rule_id: int | None = None,
    ref_batch_id: int | None = None,
    detail: str = "",
) -> int:
    cur = conn.execute(
        """
        INSERT INTO entry_audit_events(
          event_type,
          ref_ledger_id,
          ref_rule_id,
          ref_batch_id,
          detail
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (event_type, ref_ledger_id, ref_rule_id, ref_batch_id, detail),
    )
    return int(cur.lastrowid)


def list_audit_events(db_path, *, limit: int = 200):
    safe_limit = max(1, min(int(limit), 1000))
    with connect(db_path) as conn:
        cur = conn.execute(
            """
            SELECT * FROM entry_audit_events
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (safe_limit,),
        )
        return cur.fetchall()
