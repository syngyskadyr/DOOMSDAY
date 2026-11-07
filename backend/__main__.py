"""Local single-process launcher. No model call or network probe at startup."""
import argparse
import logging
from pathlib import Path

import uvicorn

from .app import DEFAULT_CHECKLIST, DEFAULT_DEV, create_app


def main():
    parser = argparse.ArgumentParser(description="DOOMSDAY CQA backend (FastAPI + SQLite)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", type=Path, default=None, help="SQLite path; otherwise DOOMSDAY_DB_PATH or backend/data/doomsday.sqlite3")
    parser.add_argument("--dev", type=Path, default=DEFAULT_DEV, help="JSON file to import atomically at startup")
    parser.add_argument("--no-dev", action="store_true", help="Disable startup import; use HTTP import")
    parser.add_argument("--checklist", type=Path, default=DEFAULT_CHECKLIST)
    parser.add_argument("--frontend", type=Path, default=None, help="Public frontend directory (default frontend/public if present)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    app = create_app(db_path=args.db, dev_path=None if args.no_dev else args.dev,
                     checklist_path=args.checklist, frontend_path=args.frontend)
    uvicorn.run(app, host=args.host, port=args.port, workers=1, access_log=False)


if __name__ == "__main__":
    main()
