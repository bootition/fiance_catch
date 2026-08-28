"""富化文本展示：优先显示 pdd_order_enrichments.product_desc，否则原始 item_desc。"""
from __future__ import annotations

import sqlite3

from ..db import connect
from .repo import get_enrichment_text


def enriched_item_desc(db_path, source_transaction_id: int, fallback: str = '') -> str:
    with connect(db_path) as conn:
        text = get_enrichment_text(conn, source_transaction_id)
    if text:
        return text
    return fallback or ''


def enrich_rows(db_path, rows: list[sqlite3.Row]) -> list[sqlite3.Row]:
    """给查询结果添加 item_desc_display 字段（不修改原 row 对象，转 dict）。"""
    source_ids = {int(r['source_transaction_id']) for r in rows if r['source_transaction_id'] is not None}
    lookup: dict[int, str] = {}
    if source_ids:
        placeholders = ','.join('?' * len(source_ids))
        with connect(db_path) as conn:
            for row in conn.execute(
                f"""SELECT source_transaction_id, product_desc FROM pdd_order_enrichments
                    WHERE status='active' AND source_transaction_id IN ({placeholders})""",
                tuple(source_ids),
            ).fetchall():
                lookup[int(row['source_transaction_id'])] = row['product_desc']
    out = []
    for r in rows:
        d = dict(r)
        sid = int(r['source_transaction_id']) if r['source_transaction_id'] is not None else None
        d['item_desc_display'] = lookup.get(sid, d.get('item_desc') or '')
        out.append(d)
    return out
