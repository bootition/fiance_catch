"""PDD 同步服务：导入原始 JSONL → 匹配支出富化 → 退款订单链接。"""
from __future__ import annotations

from pathlib import Path

from .importer import import_raw_file
from .matching import (
    apply_expense_proposals,
    apply_refund_order_links,
    build_expense_proposals,
    build_refund_order_links,
)


def sync_from_raw_file(db_path: str | Path, raw_path: str | Path, *, script_sha256: str = '') -> dict:
    imported = import_raw_file(db_path, raw_path, source='web-upload', script_sha256=script_sha256)
    expense_proposals = build_expense_proposals(db_path)
    expense_result = apply_expense_proposals(db_path, expense_proposals, auto_high=True)
    refund_proposals = build_refund_order_links(db_path, max_days=60)
    refund_result = apply_refund_order_links(db_path, refund_proposals, auto_high=True)
    return {
        'import': imported,
        'expense': {
            'total': len(expense_proposals),
            **expense_result,
            'high': sum(1 for p in expense_proposals if p['confidence'] == 'high'),
            'medium': sum(1 for p in expense_proposals if p['confidence'] == 'medium'),
            'low': sum(1 for p in expense_proposals if p['confidence'] == 'low'),
            'none': sum(1 for p in expense_proposals if p['confidence'] == 'none'),
        },
        'refund': {
            'total': len(refund_proposals),
            **refund_result,
            'high': sum(1 for p in refund_proposals if p['confidence'] == 'high'),
            'medium': sum(1 for p in refund_proposals if p['confidence'] == 'medium'),
            'none': sum(1 for p in refund_proposals if p['confidence'] == 'none'),
        },
    }
