"""Entrypoint: python -m scanmail --config /app/config/config.yaml."""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
from pathlib import Path

from .config import load_config
from .service import ScanMailer


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan to email (Synology/Docker)")
    parser.add_argument("--config", type=Path, default=Path("config/config.yaml"))
    parser.add_argument("--state-dir", type=Path, default=Path("/state"))
    parser.add_argument("--check-config", action="store_true", help="Validate YAML and SMTP environment variables without starting the service")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    try:
        config = load_config(args.config)
        if args.check_config:
            print("OK: YAML, SMTP credential presence, and profiles are valid. SMTP connectivity was not tested.")
            for route in config.routes:
                print(f"  {route.id}: {route.inbox} -> {', '.join(route.to)} -> {route.archive}")
            return 0
        stop = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        worker = ScanMailer(config, args.state_dir)
        try:
            while not stop.is_set():
                worker.scan_once()
                stop.wait(config.polling.interval_seconds)
        finally:
            worker.close()
        return 0
    except (ValueError, RuntimeError, OSError) as error:
        logging.error("Cannot start: %s", error)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
