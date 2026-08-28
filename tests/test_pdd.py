"""PDD v8：订单导入、支出富化、退款订单链接。"""
import json
import sqlite3
from datetime import datetime

from app.db import init_db
from app.pdd.importer import import_raw_file
from app.pdd.matching import (
    apply_expense_proposals,
    apply_refund_order_links,
    build_expense_proposals,
    build_refund_order_links,
)
from app.settings import Settings


def _settings(tmp_path):
    return Settings(data_dir=tmp_path, db_path=tmp_path / 'ledger.sqlite')


def _insert_source(db_path, *, platform, source_id, occurred_at, amount, direction, status, counterparty, item_desc, reason):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO source_transactions
               (platform, source_txn_id, occurred_at, amount_cents, direction, status_text,
                counterparty, item_desc, note, normalized_hash)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (platform, source_id, occurred_at, amount, direction, status, counterparty, item_desc, '', 'h' + source_id),
        )
        sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """INSERT INTO review_queue(source_transaction_id, reason, priority, status)
               VALUES (?,?,1,'pending')""",
            (sid, reason),
        )
        conn.commit()


def _order(order_sn, amount, order_time, status='交易成功'):
    return {
        'type': 1, 'order_sn': order_sn, 'parent_order_sn': '',
        'order_amount': amount, 'display_amount': amount, 'discount_amount': 0,
        'order_time': int(datetime.strptime(order_time, '%Y-%m-%d %H:%M:%S').timestamp()),
        'order_status_prompt': status,
        'mall': {'mall_name': '测试店铺'},
        'order_goods': [{'goods_id': 'G-1', 'sku_id': 'S-1', 'goods_name': '测试商品', 'spec': '红色', 'goods_price': amount, 'goods_number': 1}],
    }


def test_v8_tables_created(tmp_path):
    init_db(_settings(tmp_path))
    with sqlite3.connect(tmp_path / 'ledger.sqlite') as conn:
        version = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0]
        assert version == '8'
        for table in ['pdd_sync_runs','pdd_orders','pdd_order_enrichments','pdd_order_enrichment_items','pdd_refund_order_links']:
            assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0] == 1


def test_import_and_expense_enrichment(tmp_path):
    settings = _settings(tmp_path)
    init_db(settings)
    _insert_source(
        settings.db_path, platform='alipay', source_id='P1', occurred_at='2026-08-01 10:00:05',
        amount=1000, direction='expense', status='交易成功', counterparty='拼多多平台商户',
        item_desc='商户单号XP1', reason='unmatched',
    )
    raw = tmp_path / 'pdd.jsonl'
    raw.write_text(json.dumps(_order('260801-1', 1000, '2026-08-01 10:00:00'), ensure_ascii=False) + '\n', encoding='utf-8')
    imported = import_raw_file(settings.db_path, raw)
    assert imported['added'] == 1
    proposals = build_expense_proposals(settings.db_path)
    assert len(proposals) == 1
    assert proposals[0]['confidence'] == 'high' and proposals[0]['method'] == 'exact_unique'
    result = apply_expense_proposals(settings.db_path, proposals)
    assert result['applied'] == 1
    with sqlite3.connect(settings.db_path) as conn:
        row = conn.execute("SELECT product_desc FROM pdd_order_enrichments").fetchone()
        assert '测试商品' in row[0]
        order = conn.execute("SELECT goods_json FROM pdd_orders WHERE order_sn='260801-1'").fetchone()
        goods = json.loads(order[0])
        assert goods[0]['goods_id'] == 'G-1' and goods[0]['sku_id'] == 'S-1'


def test_refund_order_link(tmp_path):
    settings = _settings(tmp_path)
    init_db(settings)
    _insert_source(
        settings.db_path, platform='alipay', source_id='R1', occurred_at='2026-08-03 09:00:00',
        amount=1000, direction='neutral', status='退款成功', counterparty='拼多多平台商户',
        item_desc='退款-商户单号XP1', reason='refund_pending',
    )
    raw = tmp_path / 'pdd.jsonl'
    raw.write_text(json.dumps(_order('260801-1', 1000, '2026-08-01 10:00:00', status='退款成功'), ensure_ascii=False) + '\n', encoding='utf-8')
    import_raw_file(settings.db_path, raw)
    proposals = build_refund_order_links(settings.db_path)
    assert len(proposals) == 1 and proposals[0]['confidence'] == 'high'
    result = apply_refund_order_links(settings.db_path, proposals)
    assert result['applied'] == 1
    with sqlite3.connect(settings.db_path) as conn:
        row = conn.execute("SELECT order_sn FROM pdd_refund_order_links WHERE refund_source_transaction_id=1").fetchone()
        assert row[0] == '260801-1'
