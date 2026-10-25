# Instructions for Codex / agents in VS Code

## Project goal
A minimal container, independent of Brother's cloud services, that reads scans saved on a NAS, sends them as attachments through SMTP, and archives the files. At least two independent profiles (`routes`) are configured in YAML.

## Behavioral contract
- Each route has its own `inbox` directory, `archive` directory, and `to` addresses.
- Send only nonempty, stable files with allowed extensions that are older than `min_file_age_seconds`.
- Archive files *only after the SMTP server has accepted the message*.
- If sending fails, keep the original file in `inbox`.
- After SMTP acceptance and before archiving, persist the state in SQLite (`/state`). On restart, do not resend a message if its acceptance has already been recorded.
- There is no exactly-once guarantee: a crash between the SMTP server's response and the SQLite write may cause a duplicate. A recorded `accepted` status does not mean delivery to the recipient's mailbox.
- Never put passwords in YAML, logs, or the repository. Read secrets from environment variables (`.env`).
- Do not add a public HTTP endpoint or port mappings; the service does not need inbound access.

## Development guidelines
- Use Python 3.12+; PyYAML is the only third-party library.
- Run tests with `python -m unittest discover -s tests -v`.
- Changes to sending or archiving semantics require a regression test.
- Configuration lives in `config/config.yaml`; passwords and DSM paths belong in `.env`.
- Do not move a file into the archive after an SMTP error, a read error, or a file change during copying.
- When in doubt, prioritize data safety over speed.
