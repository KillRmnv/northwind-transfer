"""Extended chaos audit: the two states scripts/chaos_run.py does not check.

Reads provider ledgers from the mock and the service database, then reports:
- NEEDS_REVIEW transfers older than --grace-sec (chaos_run classifies them as settled);
- FAILED transfers with a live PROCESSING payout in a provider ledger
  (chaos_run only matches COMPLETED ledger entries for failed_but_paid).

Does not modify scripts/chaos_run.py (vendor code, see D20).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlalchemy as sa


def http_get(url: str):
    with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 - mock/admin URL from CLI args
        return response.status, json.loads(response.read().decode())


def fetch_ledgers(mock: str, providers: list) -> dict:
    entries: dict = defaultdict(list)
    for provider in providers:
        status, body = http_get(f"{mock}/{provider['provider_id']}/admin/ledger")
        if status != 200:
            raise SystemExit(f"cannot read ledger of {provider['provider_id']}: {status}")
        for entry in body.get("entries", []):
            entry["provider_id"] = provider["provider_id"]
            entries[str(entry.get("payout_id", ""))].append(entry)
    return entries


async def audit_db(grace_sec: int, tag: str) -> dict:
    from app.db.engine import session_factory
    from app.models.tables import PayoutAttempt, Transfer

    now = datetime.now(timezone.utc)
    result = {"stuck_review": [], "failed_with_live_payout": [], "open_total": 0}
    async with session_factory() as session:
        query = sa.select(Transfer).where(Transfer.status.not_in(["COMPLETED", "FAILED"]))
        if tag:
            query = query.where(Transfer.recipient_account.like(f"{tag}%"))
        open_transfers = list((await session.execute(query)).scalars().all())
        result["open_total"] = len(open_transfers)
        for transfer in open_transfers:
            if transfer.status != "NEEDS_REVIEW":
                continue
            age = (now - transfer.updated_at).total_seconds()
            if age >= grace_sec:
                result["stuck_review"].append(str(transfer.transfer_id))
        failed_query = (
            sa.select(PayoutAttempt)
            .join(Transfer, Transfer.transfer_id == PayoutAttempt.transfer_id)
            .where(PayoutAttempt.payout_id.is_not(None))
            .where(PayoutAttempt.status == "PROCESSING")
            .where(Transfer.status == "FAILED")
        )
        if tag:
            failed_query = failed_query.where(Transfer.recipient_account.like(f"{tag}%"))
        live_attempts = list((await session.execute(failed_query)).scalars().all())
        result["live_attempt_rows"] = [
            (str(a.transfer_id), a.provider_id, a.payout_id) for a in live_attempts
        ]
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mock", default="http://mock-provider:9000")
    parser.add_argument("--data", default="data")
    parser.add_argument("--grace-sec", type=int, default=600)
    parser.add_argument("--tag", default="CH")
    args = parser.parse_args()

    with open(f"{args.data}/providers.json", encoding="utf-8") as handle:
        providers = json.load(handle)
    ledgers = fetch_ledgers(args.mock, providers)
    live_payouts = {
        payout_id
        for payout_id, entries in ledgers.items()
        if any(e["status"] == "PROCESSING" for e in entries)
    }
    db = asyncio.run(audit_db(args.grace_sec, args.tag))
    failed_with_live = [row for row in db.pop("live_attempt_rows") if row[2] in live_payouts]
    db["failed_with_live_payout"] = failed_with_live
    print(json.dumps(db, indent=2, sort_keys=True))
    bad = bool(db["stuck_review"] or db["failed_with_live_payout"])
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
