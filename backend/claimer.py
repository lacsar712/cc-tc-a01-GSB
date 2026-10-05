"""进程内认领：同一 Flask 进程后台线程抢 pending，不另起容器。

只认已钉桩（带坐标快照）的待认领单；退回单（status=returned）不动。
"""
import threading
import time
from datetime import datetime, timezone

from sqlalchemy import not_

from models import ConvergenceLog, SessionLocal
from rules import judge

_stop = threading.Event()


def claim_once() -> bool:
    db = SessionLocal()
    try:
        row = (
            db.query(ConvergenceLog)
            .filter(ConvergenceLog.status == "pending")
            .filter(not_(ConvergenceLog.coordinate_snapshot.is_(None)))
            .order_by(ConvergenceLog.id)
            .with_for_update(skip_locked=True)
            .first()
        )
        if row is None:
            db.commit()
            return False
        verdict, reason = judge(float(row.delta_mm))
        row.status = "done"
        row.verdict = verdict
        row.reason = reason
        row.processed_at = datetime.now(timezone.utc)
        db.commit()
        return True
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def loop():
    while not _stop.is_set():
        try:
            if claim_once():
                time.sleep(0.4)
            else:
                time.sleep(1.0)
        except Exception as exc:
            print(f"claimer error: {exc}", flush=True)
            time.sleep(1.0)


def start():
    t = threading.Thread(target=loop, name="convergence-claimer", daemon=True)
    t.start()
