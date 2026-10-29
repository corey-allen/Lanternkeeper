"""Background ingestion worker: ``python -m lk_api.worker [--once]``.

Uses PostgreSQL as the queue (SELECT ... FOR UPDATE SKIP LOCKED); see ADR-0003.
"""

from __future__ import annotations

import argparse
import logging
import signal
import time

from .config import Settings, get_settings
from .db import Database
from .ingestion.ocr import OcrEngine, build_ocr
from .ingestion.pipeline import claim_next_job, run_job
from .logging_setup import configure_logging
from .storage import FileStorage

log = logging.getLogger("lk_api.worker")


def process_available(db: Database, storage: FileStorage, settings: Settings, ocr: OcrEngine,
                      limit: int = 100) -> int:
    processed = 0
    while processed < limit:
        job_id = claim_next_job(db)
        if job_id is None:
            break
        run_job(db, storage, job_id, ocr=ocr, max_pages=settings.max_pages, ocr_min_chars=settings.ocr_min_chars)
        processed += 1
    return processed


def main() -> None:
    parser = argparse.ArgumentParser(description="Lanternkeeper ingestion worker")
    parser.add_argument("--once", action="store_true", help="process queued jobs then exit")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    args = parser.parse_args()
    settings = get_settings()
    configure_logging(settings.log_level, include_document_text=settings.log_document_text)
    db, storage, ocr = Database(settings.database_url), FileStorage(settings.storage_dir), build_ocr(settings.ocr_engine)
    log.info("worker started", extra={"ocr_engine": ocr.name, "ocr_available": ocr.available()})
    stop = {"flag": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(flag=True))
    try:
        while not stop["flag"]:
            n = process_available(db, storage, settings, ocr)
            if args.once:
                break
            if n == 0:
                time.sleep(args.poll_seconds)
    finally:
        db.dispose()


if __name__ == "__main__":
    main()
