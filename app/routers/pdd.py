"""拼多多订单同步页面（v8）。"""
from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..db import connect
from ..pdd.matching import (
    apply_expense_proposals,
    apply_refund_order_links,
    build_expense_proposals,
    build_refund_order_links,
)
from ..pdd.service import sync_from_raw_file
from ..router_support.settings_access import current_settings
from ..settings import get_settings
from ..templates_core import templates

router = APIRouter(prefix='/pdd', tags=['pdd'])


def _counts(db_path) -> dict:
    with connect(db_path) as conn:
        run = conn.execute(
            "SELECT * FROM pdd_sync_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        orders = conn.execute("SELECT COUNT(*) FROM pdd_orders").fetchone()[0]
        enrich_active = conn.execute(
            "SELECT COUNT(*) FROM pdd_order_enrichments WHERE status='active'"
        ).fetchone()[0]
        enrich_review = conn.execute(
            "SELECT COUNT(*) FROM pdd_order_enrichments WHERE status='manual_review'"
        ).fetchone()[0]
        refund_links = conn.execute("SELECT COUNT(*) FROM pdd_refund_order_links").fetchone()[0]
        pending_refund = conn.execute(
            """
            SELECT COUNT(*) FROM review_queue AS rq
            JOIN source_transactions AS s ON s.id = rq.source_transaction_id
            WHERE rq.status='pending' AND rq.reason='refund_pending'
              AND s.counterparty LIKE '%拼多多%'
            """
        ).fetchone()[0]
        pending_expense = conn.execute(
            """
            SELECT COUNT(*) FROM review_queue AS rq
            JOIN source_transactions AS s ON s.id = rq.source_transaction_id
            WHERE rq.status='pending' AND rq.reason='unmatched'
              AND s.counterparty LIKE '%拼多多%'
            """
        ).fetchone()[0]
    return {
        'latest_run': dict(run) if run else None,
        'order_count': int(orders),
        'enrich_active': int(enrich_active),
        'enrich_review': int(enrich_review),
        'refund_links': int(refund_links),
        'pending_refund': int(pending_refund),
        'pending_expense': int(pending_expense),
    }


@router.get('', response_class=HTMLResponse)
def pdd_page(request: Request):
    settings = get_settings()
    ctx = {
        'request': request,
        'active_page': 'pdd',
        'pending_count': _pending_count(settings.db_path),
        **_counts(settings.db_path),
        'flash': '',
    }
    return HTMLResponse(templates.get_template('pdd.html').render(ctx))


def _pending_count(db_path) -> int:
    with connect(db_path) as conn:
        return int(conn.execute(
            "SELECT COUNT(*) FROM review_queue WHERE status='pending'"
        ).fetchone()[0])


@router.post('/import', response_class=HTMLResponse)
def pdd_import(request: Request, file: UploadFile = File(...)):
    settings = get_settings()
    raw_dir = settings.data_dir / 'pdd' / 'raw'
    raw_dir.mkdir(parents=True, exist_ok=True)
    if not (file.filename or '').lower().endswith('.jsonl'):
        return RedirectResponse('/pdd?flash=' + quote('只接受 .jsonl 原始订单文件'), status_code=303)
    data = file.file.read()
    if len(data) > 20 * 1024 * 1024:
        return RedirectResponse('/pdd?flash=' + quote('文件超过 20MB，已拒绝'), status_code=303)
    if len(data) < 10 or data[:1] != b'{':
        return RedirectResponse('/pdd?flash=' + quote('文件不是 JSONL 订单快照'), status_code=303)
    script_sha256 = hashlib.sha256(data).hexdigest()
    target = raw_dir / f'upload-{script_sha256[:12]}.jsonl'
    target.write_bytes(data)
    try:
        result = sync_from_raw_file(settings.db_path, target, script_sha256=script_sha256)
    except Exception as exc:
        return RedirectResponse('/pdd?flash=' + quote(f'导入失败：{exc}'), status_code=303)
    flash = (
        f"导入完成：raw={result['import']['raw_count']} 订单={result['import']['order_count']}；"
        f"支出富化 high={result['expense']['high']}/manual={result['expense']['manual_review']}；"
        f"退款链接 high={result['refund']['high']}"
    )
    return RedirectResponse('/pdd?flash=' + quote(flash), status_code=303)


@router.post('/rematch', response_class=HTMLResponse)
def pdd_rematch(request: Request):
    settings = get_settings()
    expense = build_expense_proposals(settings.db_path)
    exp = apply_expense_proposals(settings.db_path, expense, auto_high=True)
    refund = build_refund_order_links(settings.db_path, max_days=60)
    ref = apply_refund_order_links(settings.db_path, refund, auto_high=True)
    flash = f"重跑匹配：支出 high={exp['applied']}/manual={exp['manual_review']}；退款链接 high={ref['applied']}"
    return RedirectResponse('/pdd?flash=' + quote(flash), status_code=303)
