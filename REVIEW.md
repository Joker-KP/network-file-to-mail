# Project review

Reviewed on 2026-10-25; deployment documentation updated on 2026-10-26. Scope: configuration validation, SMTP submission, the SQLite ledger, archive handling, container configuration, documentation, and the existing tests. The open findings below concern sending and archiving logic, which the configuration and deployment changes do not alter.

## Open findings

### 1. [P1] A replacement scan can be deleted without being sent or archived

Location: [`scanmail/service.py:152`](scanmail/service.py#L152) and [`scanmail/service.py:167`](scanmail/service.py#L167).

`archive_file` hashes the source pathname, then calls `source.unlink()` separately. A scanner or another process can replace that pathname between the final hash check and the unlink. The worker then deletes the new scan while the archive contains only the previous scan. The state-directory lock only coordinates workers; it does not prevent scanner writes.

Confirmed with a temporary probe that atomically replaced the source with different bytes immediately before the actual unlink. The new source disappeared and the archive contained only the old bytes. Both the existing-target and new-target branches have this gap.

Recommended follow-up: establish exclusive ownership of the source before processing, using a recoverable staging protocol that preserves delivery identity across restarts. Define how a scanner still holding an open file is handled. Add regression coverage for source replacement and concurrent writes before changing deletion semantics.

### 2. [P1] The archive rename is not made durable before deleting the source

Location: [`scanmail/service.py:162`](scanmail/service.py#L162).

The temporary file is flushed and `fsync`ed, then renamed into the archive. The containing archive directory is not synced before the source is deleted. File-content syncing alone does not establish durability of the final directory entry. During a NAS power loss, depending on filesystem behavior, the source deletion could persist without a recoverable archive name.

A temporary probe confirmed that the successful copy path calls `fsync` only on the regular temporary file, never on the archive directory. Actual power-loss behavior was not tested on Synology hardware.

Recommended follow-up: sync the archive directory after publishing the destination and before deleting the source, including recovery through the existing-target branch. Preserve the source if durability confirmation fails, and add an ordering/failure regression test. Consider source-directory syncing for durable completion as well.

### 3. [P2] A concurrent archive writer can have its file overwritten

Location: [`scanmail/service.py:149`](scanmail/service.py#L149) and [`scanmail/service.py:166`](scanmail/service.py#L166).

The destination existence check and `os.replace(temp, target)` are separate operations. If another process creates that destination in between, `os.replace` silently overwrites it. The unique-suffix logic covers files present during target selection, but the lock does not coordinate external writers or workers using different state directories.

Confirmed with a temporary probe that created a different destination immediately before `os.replace`; that document was overwritten.

Recommended follow-up: publish the completed temporary file with a no-overwrite operation supported by the target filesystem. On a collision, preserve both files and keep the ledger's destination consistent with any replacement name. Add a regression test for a destination created after the initial existence check.

### 4. [P2] SMTP cleanup failure after acceptance causes duplicate submissions

Location: [`scanmail/service.py:117`](scanmail/service.py#L117) and [`scanmail/service.py:241`](scanmail/service.py#L241).

`send_email` exits its SMTP context manager before `process` records acceptance in SQLite. If the server accepts the message but its `QUIT` response raises an exception, `send_email` raises, no acceptance is recorded, and the next scan submits the message again. This can happen during normal retries without a process crash.

Confirmed with a mock SMTP session where `send_message` returned an empty rejection dictionary and context-manager cleanup raised `SMTPResponseException`. The source remained in the inbox, the ledger stayed empty, and the next scan submitted the same message again.

Recommended follow-up: distinguish successful message acceptance from session cleanup errors so a failed `QUIT` does not discard known acceptance. Add a regression test. The documented crash window and partial-recipient acceptance limitations still apply.

## Configuration and deployment update (2026-10-26)

- Renamed the supplied route IDs and directories to `account1` and `account2`.
- Moved SMTP username and password into the private runtime YAML alongside all other SMTP settings. Environment-based credentials are no longer supported.
- Excluded configuration from the Docker build context, hid passwords from configuration representations, and suppressed YAML parser excerpts that could expose credentials.
- Added a standalone image entrypoint that drops privileges according to `PUID`/`PGID`, with Dockerfile defaults for identity, timezone, and runtime paths.
- Removed the Compose and `.env` templates. The README now describes image build/export, Container Manager environment and volume settings, and equivalent standalone Docker commands.
- Documented migration precautions because route IDs and source paths participate in delivery identity. Existing ledger records and NAS directories are not migrated automatically.

## Validation

- All 24 unit tests pass using Python 3.14.4 and inside the built Python 3.12 image, including YAML-only credentials, secret handling, CLI/environment path precedence, privilege dropping, entrypoint execution, and the existing sending/archiving regression tests.
- Built `scan-to-email:codex-check` and verified image defaults, the default entrypoint, no baked-in YAML, no SMTP environment settings, and no exposed ports.
- Smoke-tested configuration validation and worker startup with networking disabled and disposable mounts. Custom `PUID`/`PGID` produced correctly owned SQLite state; SIGTERM stopped the worker cleanly. The image also supplied the configured timezone.
- Four temporary probes on 2026-10-25 confirmed the open implementation findings above; they characterize current flaws rather than fixes.
- No live SMTP submission, NAS deployment, or power-loss test was performed.
