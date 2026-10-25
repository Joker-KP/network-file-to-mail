# Brother → Synology NAS → email (SMTP)

A project for **Synology DSM / Container Manager**, independent of Brother Web Connect. The Brother scanner saves a PDF (or JPG/PNG/TIFF) to the NAS; the container polls the directories, sends each file as an SMTP attachment, and moves it to `archive` **only after the server accepts the message**.

The supplied configuration includes **two independent profiles**: `konto1` and `konto2`, each with its own `inbox`, `archive`, and `to` recipient list. Both use one SMTP account, configured for Fastmail by default. The route IDs and directory names are kept as `konto1` and `konto2` so existing scanner destinations continue to match.

## 1. Project structure

```text
synology-scan-to-email/
├── compose.yaml             # Docker Compose for DSM
├── Dockerfile
├── requirements.txt
├── .env.example             # copy to .env; set secrets, UID/GID, and paths
├── .gitignore
├── .dockerignore            # excludes secrets from the build context
├── config/
│   └── config.yaml          # service settings, SMTP (no password), two profiles
├── scanmail/
│   ├── __init__.py
│   ├── __main__.py          # service entry point
│   ├── config.py            # YAML loading and validation
│   └── service.py           # polling, SMTP, SQLite, archiving
├── tests/
│   ├── test_config.py
│   └── test_service.py
├── .vscode/extensions.json
├── AGENTS.md                # project instructions for VS Code / Codex
├── REVIEW.md                # code review findings and validation
└── README.md
```

## 2. Synology directories

For example, create a shared folder at **`/volume1/ScanToMail`** with this structure:

```text
/volume1/ScanToMail/
├── konto1/
│   ├── inbox/       ← Brother saves scans for profile 1 here
│   └── archive/     ← files are moved here after SMTP acceptance
└── konto2/
    ├── inbox/       ← Brother saves scans for profile 2 here
    └── archive/     ← archive for profile 2
```

The separate state directory (for example, `/volume1/docker/scan-to-email/state`) **must not** be inside an `inbox`. It contains the SQLite ledger used to avoid resending a message after a container restart when SMTP acceptance was recorded but archiving failed. Keep this directory persistent; deleting the ledger removes that protection for files still in an inbox.

Create the directories over SSH if they do not already exist:

```bash
sudo mkdir -p /volume1/ScanToMail/konto1/inbox /volume1/ScanToMail/konto1/archive
sudo mkdir -p /volume1/ScanToMail/konto2/inbox /volume1/ScanToMail/konto2/archive
sudo mkdir -p /volume1/docker/scan-to-email/state
```

Set permissions and ACLs through DSM **Control Panel → Shared Folder** or File Station so the process running with the UID/GID from `.env` can read and delete scans and write archives and SQLite state. Creating directories with `mkdir` does not automatically grant the required permissions.

The `inbox` directories must match the destinations configured on the scanner. You can adapt `config/config.yaml` and the mounts in `compose.yaml` to your existing paths.

## 3. Prepare the configuration

Copy the entire project to Synology, for example to `/volume1/docker/scan-to-email/project`, and open the folder in VS Code.

**A.** Copy `.env.example` to `.env` (a hidden file). Set:

```dotenv
PUID=1026
PGID=100
TZ=Europe/Warsaw
NAS_SCAN_ROOT=/volume1/ScanToMail
NAS_STATE_DIR=/volume1/docker/scan-to-email/state
SMTP_USERNAME=scanner@example.com
SMTP_PASSWORD=INSERT_APP_PASSWORD_HERE
```

`PUID` and `PGID` above are **examples**, not universal Synology IDs. Run `id your_username` over SSH and use its `uid` and `gid`. The NAS folder ownership and ACLs must allow that user to read and delete files in `inbox` and write to `archive` and `state`. The Brother scanner can use a separate NAS account with write access to its inbox.

For Fastmail, use an **app password** with SMTP access instead of the main account password. Prefer a separate app password for this service. `.env` is excluded from the image build context; never commit it to Git.

**B.** Edit `config/config.yaml`:

- `smtp.host`, `smtp.port`, `smtp.security`: the supplied values are `smtp.fastmail.com:465` with `ssl`; configure the host, port, and `starttls` setting required by your provider.
- `smtp.from_address`: the actual sender address configured on the SMTP account; the server must allow it.
- `polling.interval_seconds`: how often to check the directories; the supplied value is `30` seconds.
- `polling.min_file_age_seconds`: minimum time since the file was last modified; the supplied value is `60` seconds.
- `polling.stable_observations`: consecutive observations with the same size and metadata; the supplied value is `2`.
- `polling.max_attachment_mb`: larger files stay in `inbox`; the supplied value is `18`. Despite the key name, the unit is MiB (1,048,576 bytes). MIME/base64 encoding increases the email size.
- `routes[0].inbox`, `.archive`, `.to`: the first profile; `routes[1]...`: the second. These are **paths inside the container**, starting with `/data/` when using the supplied mount.
- `message.subject`, `message.body`, or an individual `routes[].subject`: templates support `{filename}` and `{route}`.

For example, change the recipient for the second profile:

```yaml
  - id: konto2
    inbox: /data/konto2/inbox
    archive: /data/konto2/archive
    to:
      - new.address@example.com
```

Add more recipients as additional `to` list entries; this still sends one email to multiple recipients. Add another entry to the `routes` list for a third profile.

## 4. Start on DSM (SSH + Docker Compose)

From the project directory, after creating the NAS directories:

```bash
cd /volume1/docker/scan-to-email/project
cp -n .env.example .env      # first-time setup; preserves an existing .env
# EDIT .env and config/config.yaml BEFORE starting!

docker compose config --quiet  # validate without printing expanded secrets
docker compose build
docker compose run --rm scan-to-email python -m scanmail --config /app/config/config.yaml --check-config
docker compose up -d
docker compose logs -f --tail=100 scan-to-email
```

If your SSH user cannot access Docker, run the Docker commands with `sudo`. You can also create a **Project** in DSM Container Manager using `compose.yaml`; select the folder containing both `compose.yaml` and `.env`. Compose builds the image locally. The service **exposes no ports** and does not need a reverse proxy.

### Updates

After changing `config/config.yaml` or `.env`, recreate the container:

```bash
docker compose up -d --force-recreate
```

After changing the code, rebuild the image:

```bash
docker compose up -d --build
```

## 5. End-to-end test

1. Check the directory permissions. Point the Brother scanner at `konto1/inbox` and save a test PDF.
2. Check that `docker compose logs -f scan-to-email` shows `SMTP: sending ...`, followed by `SMTP: accepted ...`, then `Archive: ...`.
3. Confirm that recipient 1 received the attachment, the file disappeared from `konto1/inbox`, and it appeared in `konto1/archive`.
4. Repeat for `konto2` and its recipient.
5. Simulate an unavailable SMTP server. After the sending error, the file should remain in `inbox` and be retried on the next polling cycle.

With the supplied settings, processing starts once the file is stable and old enough; it is not immediate. `--check-config` validates configuration only and does not send a message.

## 6. Error handling and limitations

| Situation | Behavior |
|---|---|
| SMTP server unavailable or authentication fails | The file stays in `inbox`; sending is retried on the next scan. |
| A file is still being written | Processing is deferred; size, mtime, ctime, device, or inode changes reset the stability count. |
| A file is too large, empty, or has an unsupported extension | It stays in `inbox`; oversized and empty files produce warnings. |
| SMTP acceptance is recorded, but archiving fails | The record in `/state/deliveries.sqlite3` makes subsequent attempts retry **archiving only**, without resending, as long as the file identity is unchanged. |
| The archive already contains the filename | A unique suffix is chosen for the new file. Concurrent external writes to the archive are not protected; see `REVIEW.md`. |
| A second container uses the same `/state` | A `worker.lock` file lock prevents concurrent processing. |

**SMTP limitations:** Acceptance by the sending server does not prove delivery to the recipient's mailbox. A crash between SMTP acceptance and the SQLite commit may cause a duplicate. Exactly-once delivery is not guaranteed. If the server accepts some recipients and rejects others, the file stays in `inbox`; retries can duplicate mail for recipients who already accepted it. A session cleanup error after acceptance can also cause a retry before acceptance is recorded. A provider's message size limit can reject an attachment below the configured file limit because encoding makes the message larger. Only files directly inside each `inbox` are scanned; subdirectories are ignored.

**Archive limitations:** Files are copied through a temporary archive file and checked before source deletion. The review identified remaining risks involving concurrent file changes and filesystem durability during power loss; see [REVIEW.md](REVIEW.md). Polling stability does not lock a file against later scanner writes.

**Security:** Keep scan directories and the SMTP password private, and use a service account with minimal permissions. The container does not listen for network connections. Logs include filenames and recipients; treat them as sensitive data. The service deletes the original after archiving, so keep a separate backup of the NAS archives.

## 7. Local development in VS Code

Use Python 3.12+ on Linux, including WSL on Windows. The worker uses the Unix-only `fcntl` module, so native Windows Python cannot run it. PyYAML is the only third-party dependency.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m unittest discover -s tests -v
```

To validate YAML locally, set `SMTP_USERNAME` and `SMTP_PASSWORD` in your shell environment, then run:

```bash
python -m scanmail --config config/config.yaml --check-config
```

This checks YAML, SMTP credential presence, and route configuration without connecting to SMTP or requiring the NAS directories to exist. The Python entry point does not load `.env` automatically; Docker Compose supplies those environment variables in the container. Project instructions for Codex are in [AGENTS.md](AGENTS.md), and review findings are in [REVIEW.md](REVIEW.md).
