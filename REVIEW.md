# Project review

Reviewed on 2026-10-25. Scope: configuration validation, SMTP submission, the SQLite ledger, archive handling, container configuration, documentation, and the existing tests. This review records implementation findings; the translation does not change sending or archiving logic.

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

## Documentation changes

- Translated the README, agent instructions, configuration comments, environment example, Compose diagnostics, CLI help, validation errors, logs, sample messages, and test fixture text into English.
- Preserved route IDs, directory paths, configuration keys, and environment variable names.
- Changed the documented Compose validation command to `docker compose config --quiet` so it does not print expanded environment credentials.
- Clarified persistent state, partial recipient acceptance, the attachment limit's MiB units, and local environment loading.
- Replaced native Windows development instructions with Linux/WSL guidance because the worker imports `fcntl`.

## Validation

- All 12 existing unit tests passed before and after translation, using Python 3.14.4 (`python3 -m unittest discover -s tests -v`).
- Four temporary probes confirmed the implementation findings above; they characterize current flaws rather than fixes.
- Validated the supplied YAML and CLI configuration check using dummy SMTP credentials, without a network connection.
- Validated Docker Compose using an isolated copy of `.env.example`; no real credentials were read or printed.
- Compared Python syntax trees with string literals normalized to confirm that only text changed in the Python files.
- No live SMTP submission, container deployment, NAS test, or power-loss test was performed. The container's Python 3.12 runtime was not exercised.
