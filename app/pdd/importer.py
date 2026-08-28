"""把抓取到的 PDD 原始 JSONL 标准化并导入 pdd_orders。"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from ..db import connect
from . import repo


def _ts(seconds) -> str:
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return ''
    if not seconds:
        return ''
    return datetime.fromtimestamp(seconds).strftime('%Y-%m-%d %H:%M:%S')


def _flatten_goods(obj: dict) -> list[dict]:
    items: list[dict] = []
    if obj.get('type') == 2:
        for sub in obj.get('orders') or []:
            for g in sub.get('order_goods') or []:
                items.append({
                    'goods_id': str(g.get('goods_id') or ''),
                    'sku_id': str(g.get('sku_id') or ''),
                    'goods_name': g.get('goods_name') or '',
                    'spec': g.get('spec') or '',
                    'goods_price': int(g.get('goods_price') or 0),
                    'goods_number': int(g.get('goods_number') or 0),
                    'sub_order_sn': sub.get('order_sn') or '',
                })
        return items
    for g in obj.get('order_goods') or []:
        items.append({
            'goods_id': str(g.get('goods_id') or ''),
            'sku_id': str(g.get('sku_id') or ''),
            'goods_name': g.get('goods_name') or '',
            'spec': g.get('spec') or '',
            'goods_price': int(g.get('goods_price') or 0),
            'goods_number': int(g.get('goods_number') or 0),
            'sub_order_sn': '',
        })
    return items


def normalize_order(obj: dict, *, raw_path: str = '') -> dict:
    go = obj.get('group_order') or {}
    display = obj.get('display_amount')
    if display is None:
        display = obj.get('order_amount') or 0
    return {
        'order_sn': obj.get('order_sn') or '',
        'parent_order_sn': obj.get('parent_order_sn') or '',
        'order_type': obj.get('type'),
        'order_time': _ts(obj.get('order_time')),
        'pay_time': _ts(go.get('success_time')) or _ts(go.get('create_at')),
        'display_amount_cents': int(display),
        'order_amount_cents': obj.get('order_amount'),
        'discount_amount_cents': int(obj.get('discount_amount') or 0),
        'status_text': obj.get('order_status_prompt') or '',
        'mall_name': (obj.get('mall') or {}).get('mall_name') or '',
        'goods': _flatten_goods(obj),
        'raw_path': raw_path,
    }


def import_raw_file(db_path: str | Path, raw_path: str | Path, *, source: str = 'manual-upload', script_sha256: str = '') -> dict:
    """导入原始 JSONL；重复 order_sn 做 upsert。返回运行统计。"""
    raw_path = Path(raw_path)
    raw_count = 0
    added = 0
    updated = 0
    with connect(db_path) as conn:
        conn.execute('BEGIN IMMEDIATE')
        run_id = repo.create_sync_run(
            conn, source=source, script_sha256=script_sha256, security_ok=True
        )
        try:
            with raw_path.open('r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    obj = json.loads(line)
                    if not obj.get('order_sn'):
                        continue
                    raw_count += 1
                    row = normalize_order(obj, raw_path=str(raw_path))
                    if repo.upsert_order(conn, row, fetched_run_id=run_id):
                        added += 1
                    else:
                        updated += 1
            repo.finish_sync_run(
                conn, run_id, raw_count=raw_count, order_count=added + updated,
                status='ok', report_path=str(raw_path),
            )
        except Exception:
            repo.finish_sync_run(
                conn, run_id, raw_count=raw_count, order_count=added + updated,
                status='failed', report_path=str(raw_path),
            )
            raise
        conn.commit()
    return {'run_id': run_id, 'raw_count': raw_count, 'added': added, 'updated': updated}
