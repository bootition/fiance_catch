"""PDD 订单与账本流水匹配：支出富化 + 退款订单链接。"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

from ..db import connect
from . import repo

EXACT_HIGH_WINDOW = 2 * 3600
EXACT_FALLBACK_WINDOW = 72 * 3600
SUBSET_WINDOW = 6 * 3600
MAX_SUBSET_ITEMS = 8


def _dt(value):
    if not value:
        return None
    try:
        return datetime.strptime(str(value), '%Y-%m-%d %H:%M:%S')
    except ValueError:
        try:
            return datetime.strptime(str(value)[:19], '%Y-%m-%d %H:%M:%S')
        except ValueError:
            return None


def _best_diff(order, t):
    diffs = []
    for value in (order.get('order_time'), order.get('pay_time')):
        d = _dt(value)
        if d is not None:
            diffs.append(abs((d - t).total_seconds()))
    return min(diffs) if diffs else 10**12


def _goods_desc(goods_json):
    try:
        goods = json.loads(goods_json or '[]')
    except Exception:
        return ''
    parts = []
    for g in goods:
        name = (g.get('goods_name') or '').strip()
        spec = (g.get('spec') or '').strip()
        qty = g.get('goods_number') or 1
        seg = name
        if spec:
            seg += f'/{spec}'
        seg += f' ×{qty}'
        parts.append(seg)
    return ' + '.join(parts)


def _order_dict(row):
    return {
        'order_sn': row['order_sn'],
        'order_time': row['order_time'],
        'pay_time': row['pay_time'],
        'display_amount_cents': int(row['display_amount_cents']),
        'status_text': row['status_text'] or '',
        'mall_name': row['mall_name'] or '',
        'goods_json': row['goods_json'] or '[]',
    }


def _find_subsets(target, candidates):
    seen = set(); uniq = []
    for c in candidates:
        if c['order_sn'] not in seen and 0 < c['display_amount_cents'] <= target:
            seen.add(c['order_sn']); uniq.append(c)
    n = len(uniq)
    if n == 0 or n > 20:
        return []
    solutions = []
    for mask in range(1, 1 << n):
        if mask.bit_count() > MAX_SUBSET_ITEMS:
            continue
        s = sum(uniq[i]['display_amount_cents'] for i in range(n) if (mask >> i) & 1)
        if s == target:
            solutions.append([uniq[i] for i in range(n) if (mask >> i) & 1])
            if len(solutions) >= 10:
                break
    return solutions


def _subset_score(sol, t):
    if not sol:
        return 10**12
    times = [_dt(o['order_time']) or _dt(o['pay_time']) for o in sol]
    times = [x for x in times if x]
    if not times:
        return 10**12
    max_diff = max(abs((x - t).total_seconds()) for x in times)
    spread = (max(times) - min(times)).total_seconds()
    return max_diff + 2 * spread + 1000 * len(sol)


def build_expense_proposals(db_path) -> list[dict]:
    proposals = []
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT s.id, s.platform, s.occurred_at, s.amount_cents, s.item_desc,
                   rq.status AS queue_status, rq.reason
            FROM source_transactions AS s
            JOIN review_queue AS rq ON rq.source_transaction_id = s.id
            WHERE s.counterparty LIKE '%拼多多%'
              AND s.direction = 'expense'
              AND rq.reason = 'unmatched'
              AND rq.status = 'pending'
            ORDER BY s.occurred_at
            """
        ).fetchall()
        order_rows = conn.execute(
            "SELECT * FROM pdd_orders WHERE status_text <> '交易已取消' ORDER BY order_time"
        ).fetchall()
    orders = [_order_dict(r) for r in order_rows]
    for r in rows:
        amt = int(r['amount_cents']); t = _dt(r['occurred_at'])
        exact = [o for o in orders if o['display_amount_cents'] == amt and _best_diff(o, t) <= EXACT_FALLBACK_WINDOW]
        exact.sort(key=lambda o: (_best_diff(o, t), o['order_sn']))
        prop = {
            'source_transaction_id': int(r['id']),
            'occurred_at': r['occurred_at'],
            'amount_cents': amt,
            'original_item_desc': r['item_desc'] or '',
            'method': '', 'confidence': '', 'order_sns': [], 'amounts': [],
            'product_desc': '', 'candidates': [],
        }
        sol = []
        if exact:
            best = exact[0]; bd = _best_diff(best, t)
            if len(exact) == 1 and bd <= EXACT_HIGH_WINDOW:
                sol = [best]; prop.update(method='exact_unique', confidence='high')
            elif len(exact) == 1:
                sol = [best]; prop.update(method='exact_unique_far', confidence='medium')
            else:
                second_bd = _best_diff(exact[1], t)
                if bd <= 300 and second_bd > 3600:
                    sol = [best]; prop.update(method='exact_best', confidence='high')
                elif bd <= 300:
                    sol = [best]; prop.update(method='exact_ambiguous_near', confidence='medium')
                else:
                    sol = [best]; prop.update(method='exact_best_far', confidence='low')
            prop['candidates'] = [o['order_sn'] for o in exact]
        else:
            near = [o for o in orders if _best_diff(o, t) <= SUBSET_WINDOW]
            sols = _find_subsets(amt, near)
            if sols:
                sols.sort(key=lambda s: (_subset_score(s, t), len(s)))
                if len(sols) == 1:
                    sol = sols[0]; prop.update(method='subset_unique', confidence='high')
                elif _subset_score(sols[0], t) <= 3000 and _subset_score(sols[1], t) - _subset_score(sols[0], t) >= 1200:
                    sol = sols[0]; prop.update(method='subset_best', confidence='high')
                else:
                    sol = sols[0]; prop.update(method='subset_ambiguous', confidence='medium')
                prop['candidates'] = [[o['order_sn'] for o in s] for s in sols]
            else:
                prop.update(method='no_match', confidence='none')
        if sol:
            prop['order_sns'] = [o['order_sn'] for o in sol]
            prop['amounts'] = [o['display_amount_cents'] for o in sol]
            prop['product_desc'] = ' + '.join(_goods_desc(o['goods_json']) for o in sol)
        proposals.append(prop)
    return proposals


def apply_expense_proposals(db_path, proposals: list[dict], *, auto_high: bool = True) -> dict:
    """把匹配结果写入 pdd_order_enrichments；high 自动 active，其余 manual_review。"""
    applied = 0; review = 0; skipped = 0
    with connect(db_path) as conn:
        conn.execute('BEGIN IMMEDIATE')
        for p in proposals:
            if not p.get('order_sns'):
                skipped += 1
                continue
            status = 'active' if (p['confidence'] == 'high' and auto_high) else 'manual_review'
            display_desc = p['product_desc']
            original = (p.get('original_item_desc') or '').strip()
            if original and original not in display_desc and '商户单号' not in display_desc:
                display_desc = f"{display_desc} | {original}"
            repo.upsert_enrichment(
                conn,
                source_transaction_id=p['source_transaction_id'],
                product_desc=display_desc,
                method=p['method'],
                confidence=p['confidence'],
                status=status,
            )
            repo.clear_enrichment_items(conn, p['source_transaction_id'])
            for sn, amount in zip(p['order_sns'], p['amounts']):
                repo.add_enrichment_item(
                    conn, source_transaction_id=p['source_transaction_id'],
                    order_sn=sn, amount_cents=amount,
                )
            if status == 'active':
                conn.execute(
                    """
                    INSERT INTO entry_audit_events(event_type, ref_ledger_id, ref_batch_id, detail)
                    VALUES ('pdd_enrich_applied', NULL, NULL, ?)
                    """,
                    (f"source:{p['source_transaction_id']};method:{p['method']};orders:{','.join(p['order_sns'])}",),
                )
                applied += 1
            else:
                review += 1
        conn.commit()
    return {'applied': applied, 'manual_review': review, 'skipped': skipped}


def build_refund_order_links(db_path, *, max_days: int = 60) -> list[dict]:
    """退款流水 ↔ 退款成功订单匹配提案（只出唯一候选为 high）。"""
    proposals = []
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT s.id, s.platform, s.direction, s.occurred_at, s.amount_cents, s.item_desc
            FROM source_transactions AS s
            JOIN review_queue AS rq ON rq.source_transaction_id = s.id
            WHERE s.counterparty LIKE '%拼多多%'
              AND rq.reason = 'refund_pending'
            ORDER BY s.occurred_at
            """
        ).fetchall()
        orders = [_order_dict(r) for r in conn.execute(
            "SELECT * FROM pdd_orders WHERE status_text = '退款成功'"
        ).fetchall()]
    for r in rows:
        amt = int(r['amount_cents']); t = _dt(r['occurred_at'])
        candidates = []
        for o in orders:
            if o['display_amount_cents'] != amt:
                continue
            ot = _dt(o['order_time'])
            if ot is None:
                continue
            diff = abs((ot - t).total_seconds())
            if diff <= max_days * 86400:
                candidates.append((diff, o))
        candidates.sort(key=lambda x: x[0])
        prop = {
            'refund_source_transaction_id': int(r['id']),
            'occurred_at': r['occurred_at'], 'amount_cents': amt,
            'order_sn': '', 'match_method': '', 'confidence': '', 'candidates': [],
            'time_diff_seconds': None,
        }
        if len(candidates) == 1:
            prop.update(order_sn=candidates[0][1]['order_sn'], match_method='refund_amount_unique',
                        confidence='high', time_diff_seconds=int(candidates[0][0]),
                        candidates=[candidates[0][1]['order_sn']])
        elif len(candidates) > 1:
            prop.update(order_sn=candidates[0][1]['order_sn'], match_method='refund_amount_ambiguous',
                        confidence='medium', time_diff_seconds=int(candidates[0][0]),
                        candidates=[c[1]['order_sn'] for c in candidates])
        else:
            prop.update(match_method='no_match', confidence='none')
        proposals.append(prop)
    return proposals
def apply_refund_order_links(db_path, proposals: list[dict], *, auto_high: bool = True) -> dict:
    applied = 0; review = 0
    with connect(db_path) as conn:
        conn.execute('BEGIN IMMEDIATE')
        for p in proposals:
            if not p.get('order_sn'):
                continue
            conf = p['confidence']
            if conf == 'high' and auto_high:
                repo.upsert_refund_order_link(
                    conn,
                    refund_source_transaction_id=p['refund_source_transaction_id'],
                    order_sn=p['order_sn'], match_method=p['match_method'],
                    confidence=conf, amount_cents=p['amount_cents'],
                    time_diff_seconds=p.get('time_diff_seconds'),
                )
                applied += 1
            else:
                review += 1
        conn.commit()
    return {'applied': applied, 'manual_review': review}
