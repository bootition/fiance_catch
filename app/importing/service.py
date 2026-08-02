import hashlib
from dataclasses import dataclass
from pathlib import Path

from ..db import connect
from ..ledger_repo import (
    _create_import_batch,
    _insert_source_transaction,
    _update_batch_counts,
)
from .alipay import parse_alipay_csv_bytes
from .model import Platform, RowStatus
from .wechat import parse_wechat_xlsx


@dataclass(frozen=True)
class ImportResult:
    batch_id: int
    file_name: str
    file_fingerprint: str
    total: int
    added: int
    duplicates: int
    skipped: int
    invalid: int
    refunds: int


@dataclass
class _Counters:
    total: int = 0
    added: int = 0
    duplicates: int = 0
    skipped: int = 0
    invalid: int = 0
    refunds: int = 0


def _file_fingerprint(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_bytes(path: str | Path) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def import_file(db_path, file_path: str | Path, platform: str) -> ImportResult:
    """解析并导入一个账单文件（原子）。

    创建批次、写入全部来源流水、更新批次计数在单个 BEGIN IMMEDIATE
    事务内完成：任何一步失败都完整回滚，批次与流水均无残留，可安全重传。

    - 成功/退款行入库（同平台来源单号已存在则计重复跳过）
    - 关闭/失败/未知状态行计跳过，不入库
    - 成功/退款行缺少来源交易单号计 invalid，不入库（避免 (platform,'')
      合并去重）；零金额成功行是平台事实，允许保留
    - 不保存原始文件内容
    """
    if platform not in (Platform.ALIPAY.value, Platform.WECHAT.value):
        raise ValueError(f"unsupported platform: {platform}")
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"file not found: {path}")

    fingerprint = _file_fingerprint(path)
    if platform == Platform.ALIPAY.value:
        rows = parse_alipay_csv_bytes(_read_bytes(path))
    else:
        rows = parse_wechat_xlsx(path)

    counters = _Counters(total=len(rows))
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        batch_id = _create_import_batch(
            conn,
            file_name=path.name,
            platform=platform,
            file_fingerprint=fingerprint,
        )
        for row in rows:
            if row.status == RowStatus.SKIPPED:
                counters.skipped += 1
                continue
            if not row.source_txn_id:
                counters.invalid += 1
                continue
            _, created = _insert_source_transaction(
                conn,
                platform=row.platform,
                source_txn_id=row.source_txn_id,
                occurred_at=row.occurred_at,
                amount_cents=row.amount_cents,
                direction=row.direction,
                status_text=row.status_text,
                counterparty=row.counterparty,
                item_desc=row.item_desc,
                raw_type=row.raw_type,
                note=row.note,
                batch_id=batch_id,
                normalized_hash=row.normalized_hash,
            )
            if created:
                counters.added += 1
                if row.status == RowStatus.REFUND:
                    counters.refunds += 1
            else:
                counters.duplicates += 1
        _update_batch_counts(
            conn,
            batch_id,
            row_count=counters.total,
            accepted_count=counters.added,
            skipped_count=counters.skipped,
            pending_count=0,
        )
        conn.commit()

    return ImportResult(
        batch_id=batch_id,
        file_name=path.name,
        file_fingerprint=fingerprint,
        total=counters.total,
        added=counters.added,
        duplicates=counters.duplicates,
        skipped=counters.skipped,
        invalid=counters.invalid,
        refunds=counters.refunds,
    )
