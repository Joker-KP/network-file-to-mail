"""SMTP delivery, durable delivery ledger, and safe archive handling."""
from __future__ import annotations

import fcntl
import hashlib
import logging
import mimetypes
import os
import shutil
import smtplib
import sqlite3
import ssl
import time
import uuid
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path

from .config import Config, Route

LOG = logging.getLogger(__name__)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_signature(st: os.stat_result) -> tuple[int, int, int, int, int]:
    return (st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_dev, st.st_ino)


def file_key(route_id: str, path: Path, signature: tuple[int, ...], sha256: str) -> str:
    # ctime can change when fixing permissions without changing file contents;
    # do not use it for the persistent delivery identity.
    stable_identity = (signature[0], signature[1], signature[3], signature[4])
    value = "\0".join((route_id, str(path), *(str(x) for x in stable_identity), sha256))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class Ledger:
    """Commit delivery *before* archive; survives NAS/container restarts."""

    def __init__(self, dbpath: Path):
        dbpath.parent.mkdir(parents=True, exist_ok=True)
        self._lock_file = (dbpath.parent / "worker.lock").open("a+b")
        try:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("Another instance is already using the same state directory") from error
        self.db = sqlite3.connect(dbpath)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS deliveries (
                key TEXT PRIMARY KEY,
                route_id TEXT NOT NULL,
                source TEXT NOT NULL,
                target TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                sent_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                archived_at TEXT
            )
        """)
        self.db.commit()

    def get(self, key: str) -> tuple[str, str, str] | None:
        row = self.db.execute(
            "SELECT target, sha256, archived_at FROM deliveries WHERE key=?", (key,)
        ).fetchone()
        return tuple(row) if row else None

    def record_sent(self, key: str, route: Route, source: Path, target: Path, sha256: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO deliveries (key, route_id, source, target, sha256) VALUES (?, ?, ?, ?, ?)",
                (key, route.id, str(source), str(target), sha256),
            )

    def mark_archived(self, key: str) -> None:
        with self.db:
            self.db.execute(
                "UPDATE deliveries SET archived_at=CURRENT_TIMESTAMP WHERE key=?", (key,)
            )

    def close(self) -> None:
        if self.db is not None:
            self.db.close()
            self.db = None
        if not self._lock_file.closed:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
            self._lock_file.close()


def build_message(config: Config, route: Route, filename: str, attachment: bytes) -> EmailMessage:
    message = EmailMessage()
    message["From"] = formataddr((config.smtp.from_name, config.smtp.from_address))
    message["To"] = ", ".join(route.to)
    message["Subject"] = (route.subject or config.subject).format(route=route.id, filename=filename)
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid()
    message.set_content(config.body.format(route=route.id, filename=filename))
    guessed, _ = mimetypes.guess_type(filename)
    if guessed and "/" in guessed:
        maintype, subtype = guessed.split("/", 1)
    else:
        maintype, subtype = "application", "octet-stream"
    message.add_attachment(attachment, maintype=maintype, subtype=subtype, filename=filename)
    return message


def send_email(config: Config, message: EmailMessage, recipients: tuple[str, ...]) -> None:
    smtp = config.smtp
    context = ssl.create_default_context()
    if smtp.security == "ssl":
        server = smtplib.SMTP_SSL(smtp.host, smtp.port, timeout=smtp.timeout_seconds, context=context)
    else:
        server = smtplib.SMTP(smtp.host, smtp.port, timeout=smtp.timeout_seconds)
    with server:
        server.ehlo()
        if smtp.security == "starttls":
            server.starttls(context=context)
            server.ehlo()
        server.login(smtp.username, smtp.password)
        rejected = server.send_message(message, from_addr=smtp.from_address, to_addrs=list(recipients))
        if rejected:
            raise smtplib.SMTPRecipientsRefused(rejected)


def choose_archive_target(route: Route, source: Path, sha256: str) -> Path:
    initial = route.archive / source.name
    if not initial.exists():
        return initial
    suffix = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + sha256[:8]
    for counter in range(1, 10001):
        postfix = suffix if counter == 1 else f"{suffix}-{counter}"
        candidate = route.archive / f"{source.stem}__{postfix}{source.suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Too many filename collisions in: {route.archive}")


def archive_file(source: Path, target: Path, expected_sha256: str) -> None:
    """Copy through .partial in target dir; verify before deleting source.

    A restart after copy but before source unlink is safe: an existing identical
    target can be recognized on the next pass and removed from inbox.
    """
    if digest(source.read_bytes()) != expected_sha256:
        raise RuntimeError(f"File changed after sending; keeping it in inbox: {source}")
    if target.exists():
        if digest(target.read_bytes()) != expected_sha256:
            raise RuntimeError(f"A different file already exists in the archive at: {target}")
        if digest(source.read_bytes()) != expected_sha256:
            raise RuntimeError(f"File changed before deletion: {source}")
        source.unlink()
        return

    temp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.partial")
    try:
        with source.open("rb") as original, temp.open("xb") as output:
            shutil.copyfileobj(original, output)
            output.flush()
            os.fsync(output.fileno())
        if digest(temp.read_bytes()) != expected_sha256:
            raise RuntimeError("File changed while copying to the archive")
        # Single worker is guaranteed by flock on the state folder.
        os.replace(temp, target)
        if digest(source.read_bytes()) != expected_sha256:
            raise RuntimeError(f"File changed before deletion: {source}")
        source.unlink()
    finally:
        temp.unlink(missing_ok=True)


class ScanMailer:
    def __init__(self, config: Config, state_dir: Path):
        self.config = config
        self.ledger = Ledger(state_dir / "deliveries.sqlite3")
        self.observed: dict[Path, tuple[tuple[int, ...], int]] = {}
        for route in config.routes:
            if not route.inbox.is_dir():
                raise RuntimeError(f"Missing inbox directory: {route.inbox} (not created automatically)")
            route.archive.mkdir(parents=True, exist_ok=True)
            if not route.archive.is_dir():
                raise RuntimeError(f"Missing archive directory: {route.archive}")
        LOG.info("Started; profiles: %s; interval: %ds", ", ".join(r.id for r in config.routes), config.polling.interval_seconds)

    def close(self) -> None:
        self.ledger.close()

    def scan_once(self) -> None:
        seen: set[Path] = set()
        for route in self.config.routes:
            try:
                entries = sorted(route.inbox.iterdir())
            except OSError:
                LOG.exception("Cannot read directory %s", route.inbox)
                continue
            for path in entries:
                if path.is_symlink() or not path.is_file() or path.suffix.lower() not in self.config.polling.extensions:
                    continue
                seen.add(path)
                try:
                    st = path.stat()
                    signature = file_signature(st)
                    prev = self.observed.get(path)
                    count = prev[1] + 1 if prev and prev[0] == signature else 1
                    self.observed[path] = (signature, count)
                    if time.time() - st.st_mtime < self.config.polling.min_file_age_seconds:
                        continue
                    if count < self.config.polling.stable_observations:
                        continue
                    if st.st_size > self.config.polling.max_attachment_bytes:
                        LOG.warning("Skipped oversized file (%d B): %s", st.st_size, path)
                        continue
                    if st.st_size == 0:
                        LOG.warning("Skipped empty file: %s", path)
                        continue
                    self.process(route, path, signature)
                except (OSError, RuntimeError, smtplib.SMTPException, ValueError):
                    LOG.exception("Error processing %s; file remains in inbox for retry", path)
                except Exception:
                    LOG.exception("Unexpected error for file %s", path)
        for path in set(self.observed) - seen:
            del self.observed[path]

    def process(self, route: Route, path: Path, original_signature: tuple[int, ...]) -> None:
        content = path.read_bytes()
        if file_signature(path.stat()) != original_signature or len(content) != original_signature[0]:
            LOG.info("File is being written; deferring: %s", path)
            return
        sha256 = digest(content)
        key = file_key(route.id, path, original_signature, sha256)
        existing = self.ledger.get(key)
        if existing is not None:
            target = Path(existing[0])
            LOG.info("Already sent %s; retrying archiving only", path.name)
        else:
            target = choose_archive_target(route, path, sha256)
            message = build_message(self.config, route, path.name, content)
            LOG.info("SMTP: sending %s (%s, %d B) -> %s", path.name, route.id, len(content), ", ".join(route.to))
            send_email(self.config, message, route.to)
            # SMTP accepted: persist BEFORE archive. In the very small window
            # between SMTP acceptance and DB commit, a crash can cause resending.
            self.ledger.record_sent(key, route, path, target, sha256)
            LOG.info("SMTP: accepted %s", path.name)
        archive_file(path, target, sha256)
        self.ledger.mark_archived(key)
        self.observed.pop(path, None)
        LOG.info("Archive: %s -> %s", path, target)
