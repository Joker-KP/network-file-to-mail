# Brother → Synology NAS → email (SMTP)

A standalone Docker service for Synology NAS, independent of Brother Web Connect. The Brother scanner saves a PDF, JPG, PNG, or TIFF to the NAS. The container polls the scan directories, sends each file as an SMTP attachment, records SMTP acceptance in SQLite, and then archives the file.

The supplied YAML includes two independent routes, `account1` and `account2`, each with its own `inbox`, `archive`, and recipients. All SMTP settings, including the username and password, belong in the runtime YAML file.

Deploy through [Docker Hub](docs/docker-hub.md) using `macjoker/scan-to-mail:latest`, or build on your development machine and transfer **the image archive and a private YAML configuration** to the NAS. Synology does not need the source checkout, a Compose project, or a `.env` file. A Dockerfile is a build recipe; its local build context still needs the application source and `requirements.txt`.

## 1. Build and transfer the image

### Publish and pull through Docker Hub

```bash
docker login --username macjoker
./scripts/publish-dockerhub.sh
```

The script runs `docker build -t macjoker/scan-to-mail:latest --push .` from the repository root. On the NAS, pull `macjoker/scan-to-mail:latest` and use it in place of `scan-to-email:local` in the settings and commands below. See the [Docker Hub deployment guide](docs/docker-hub.md) for authentication, architecture options, version tags, and updates.

### Transfer an image archive instead

From the repository root on a machine with Docker:

```bash
docker build -t scan-to-email:local .
docker save -o /tmp/scan-to-email-image.tar scan-to-email:local
```

Build for the NAS CPU architecture. If the build machine differs, select the appropriate platform with Docker's `--platform` option, for example `linux/amd64` or `linux/arm64`; cross-platform builds require a suitable builder.

Transfer `/tmp/scan-to-email-image.tar` to the NAS. Import it as an image in Container Manager, or load it over SSH:

```bash
docker load -i /path/to/scan-to-email-image.tar
```

The image contains the application and its dependencies. The `config/` directory is excluded from the build context, so SMTP credentials are not baked into the image. Rebuild and import a new image after changing application code.

## 2. Prepare NAS directories and YAML

Example NAS layout:

```text
/volume1/ScanToMail/
├── account1/
│   ├── inbox/       ← Brother saves scans for account1 here
│   └── archive/     ← files are archived after recorded SMTP acceptance
└── account2/
    ├── inbox/
    └── archive/

/volume1/docker/scan-to-email/
├── config/
│   └── config.yaml  ← private runtime YAML, including SMTP credentials
└── state/          ← persistent SQLite ledger and worker lock
```

Create the directories through DSM or SSH:

```bash
sudo mkdir -p /volume1/ScanToMail/account1/inbox /volume1/ScanToMail/account1/archive
sudo mkdir -p /volume1/ScanToMail/account2/inbox /volume1/ScanToMail/account2/archive
sudo mkdir -p /volume1/docker/scan-to-email/config /volume1/docker/scan-to-email/state
```

Copy the repository's [config/config.yaml](config/config.yaml) template to `/volume1/docker/scan-to-email/config/config.yaml` on the NAS. Edit that private copy:

```yaml
smtp:
  host: smtp.fastmail.com
  port: 465
  security: ssl
  username: scanner@example.com
  password: "REPLACE_WITH_SMTP_APP_PASSWORD"
  from_address: scanner@example.com
  from_name: "Brother Scanner"
  timeout_seconds: 30
```

This is only the SMTP section: keep `version`, `polling`, `message`, and both `routes` from the template as well. Use an app password with SMTP access for Fastmail. Quote the password as a YAML string; in single-quoted YAML strings, escape an apostrophe by doubling it. The sender address must be allowed by your SMTP account. For another provider, use its host, port, and `ssl` or `starttls` settings.

Update each route's `to` recipients. The supplied paths are container paths:

```yaml
routes:
  - id: account1
    inbox: /data/account1/inbox
    archive: /data/account1/archive
    to:
      - recipient1@example.com
  - id: account2
    inbox: /data/account2/inbox
    archive: /data/account2/archive
    to:
      - recipient2@example.com
```

Run `id your_username` over SSH to find the NAS user's numeric UID and GID. Set filesystem ownership and DSM ACLs so this identity can read the private YAML, read and delete inbox files, and write archives and SQLite state. `1026:100` below is an example, not a universal Synology identity. The scanner can use a separate NAS account with write access to the inboxes.

Keep the YAML private and mount it read-only. Do not commit real credentials; `config/local.yaml` is ignored for local development. The checked-in template must contain placeholders only. The state directory must be persistent and outside all inboxes; deleting it removes duplicate-submission protection for scans awaiting archiving.

## 3. Container Manager settings

Create a container from the pulled `macjoker/scan-to-mail:latest` image or the imported `scan-to-email:local` image. Keep its default entrypoint and leave the command empty. Enable automatic restart and configure the environment and volume mappings below. No port mappings are needed.

### Environment variables

These defaults are declared in the Dockerfile and can be changed in the container's environment settings:

| Variable | Image default | Purpose |
|---|---|---|
| `PUID` | `1026` | NAS user's numeric UID; set this to the account that has access to the mounted files. Must be greater than zero. |
| `PGID` | `100` | NAS group's numeric GID; set this to the corresponding group. |
| `TZ` | `Europe/Warsaw` | Time zone for log timestamps and message dates. |
| `SCANMAIL_CONFIG` | `/config/config.yaml` | Configuration file path **inside the container**. Keep the default when using the mount below. |
| `SCANMAIL_STATE_DIR` | `/state` | Persistent ledger directory **inside the container**. Keep the default when using the mount below. |
| `PYTHONUNBUFFERED` | `1` | Emit logs immediately. Keep the default. |
| `PYTHONDONTWRITEBYTECODE` | `1` | Avoid writing Python bytecode to the image filesystem. Keep the default. |

No SMTP environment variables are used. The entrypoint starts as root, clears supplementary groups, switches to `PGID` and `PUID`, then starts the worker as that user. It does not change NAS ownership or ACLs. If you explicitly set Docker's container user, that user's UID/GID must match `PUID`/`PGID`.

NAS host paths are volume settings, not environment variables: setting an environment variable cannot mount a NAS directory. The previous host-path variables are replaced by the mappings below.

### Volume mappings

| NAS host path (example) | Container path | Access |
|---|---|---|
| `/volume1/ScanToMail` | `/data` | Read/write |
| `/volume1/docker/scan-to-email/state` | `/state` | Read/write |
| `/volume1/docker/scan-to-email/config/config.yaml` | `/config/config.yaml` | Read-only |

If mapping a directory is more convenient, mount `/volume1/docker/scan-to-email/config` at `/config` read-only instead of mapping the individual YAML file. Match the actual file location to `SCANMAIL_CONFIG`.

Start the container and inspect its logs. Existing inbox directories must be present; the service does not create them automatically. It creates missing archive directories if permissions allow.

## 4. Equivalent SSH commands, without Compose

First validate the mounted YAML. Replace the example UID/GID with your NAS values:

```bash
docker run --rm \
  -e PUID=1026 -e PGID=100 \
  --mount type=bind,src=/volume1/docker/scan-to-email/config,dst=/config,readonly \
  scan-to-email:local --check-config
```

This does not connect to SMTP, send mail, or require the scan and state mounts. It checks YAML and required settings without printing the password. It does not verify the password with your mail provider.

Start the worker:

```bash
docker run -d \
  --name scan-to-email \
  --restart unless-stopped \
  --init \
  --read-only \
  --tmpfs /tmp:rw,nosuid,nodev,size=16m \
  --security-opt no-new-privileges:true \
  --log-opt max-size=10m --log-opt max-file=3 \
  -e PUID=1026 -e PGID=100 -e TZ=Europe/Warsaw \
  --mount type=bind,src=/volume1/ScanToMail,dst=/data \
  --mount type=bind,src=/volume1/docker/scan-to-email/state,dst=/state \
  --mount type=bind,src=/volume1/docker/scan-to-email/config,dst=/config,readonly \
  scan-to-email:local

docker logs -f --tail=100 scan-to-email
```

Use `sudo` for Docker commands if your SSH user needs it. Both workflows use the same image, runtime YAML, environment values, and mounts.

### Updates and migration

After editing the mounted YAML, restart the container to reload it. After changing environment variables, mounts, or the image, recreate the container with those settings. Preserve the same state mount across updates. Replacing an individually mounted file may require recreating the container; mounting its parent directory avoids that bind-mount issue.

When migrating from the previous route names or moving existing inboxes, first finish processing and any pending archive retries using the old configuration. Stop the old container before renaming directories, updating the scanner destinations, and switching to `account1`/`account2`. Route IDs and source paths are part of the ledger identity; changing them while accepted scans remain in an inbox can cause resending. Keep the state directory and an archive backup. This update does not automatically rename NAS directories or migrate existing ledger records.

## 5. Processing settings and end-to-end test

The supplied configuration checks inboxes every 30 seconds, waits until a file is at least 60 seconds old, and requires two consecutive observations with unchanged size and metadata. Processing is not immediate.

- `polling.interval_seconds`: time between directory scans.
- `polling.min_file_age_seconds`: minimum time since the file's last modification.
- `polling.stable_observations`: required consecutive unchanged observations.
- `polling.max_attachment_mb`: maximum attachment size, supplied as `18`. Despite the key name, the unit is MiB (1,048,576 bytes). Encoding makes the final email larger.
- `polling.extensions`: allowed extensions, compared without regard to case.
- `message.subject`, `message.body`, and optional `routes[].subject`: templates supporting `{filename}` and `{route}`.

Each additional `to` entry adds a recipient to the same email. Additional routes can have their own inbox, archive, recipients, and optional subject.

1. Configure Brother to save a test PDF in `account1/inbox`.
2. Check the logs for `SMTP: sending ...`, `SMTP: accepted ...`, then `Archive: ...`.
3. Confirm recipient 1 received the attachment and the file moved from `account1/inbox` to `account1/archive`.
4. Repeat for `account2` and its recipient.
5. Simulate an unavailable SMTP server; the file should stay in `inbox` and be retried on the next polling cycle.

## 6. Error handling and limitations

| Situation | Behavior |
|---|---|
| SMTP unavailable or authentication fails | The file stays in `inbox`; sending is retried on a later scan. |
| A file is still being written | Processing is deferred; changes in size, mtime, ctime, device, or inode reset stability observations. |
| A file is too large, empty, or has an unsupported extension | It stays in `inbox`; oversized and empty files produce warnings. |
| SMTP acceptance is recorded, but archiving fails | Subsequent attempts retry archiving only, provided the file's delivery identity is unchanged. |
| The archive already contains the filename | A unique suffix is chosen; concurrent external archive writes remain a known risk. |
| Another worker uses the same state directory | The `worker.lock` file lock prevents concurrent processing. |

SMTP acceptance does not prove delivery to the recipient's mailbox. There is no exactly-once guarantee: a crash after SMTP acceptance and before the SQLite commit may cause a duplicate. Partial recipient acceptance or a session cleanup error after acceptance can also cause repeated submissions. Only files directly inside an inbox are scanned; subdirectories are ignored. The provider may reject an email below the attachment limit because MIME/base64 encoding increases message size.

Files are copied through a temporary archive file and checked before deleting the source. Remaining risks involving concurrent source replacement, archive collisions, and power-loss durability are documented in [REVIEW.md](REVIEW.md). Polling stability does not lock files against later writes.

Keep a separate backup of the archives. Logs contain filenames and recipient addresses; treat them as sensitive data. The service has no HTTP endpoint or inbound network listener.

## 7. Local development

Use Python 3.12+ on Linux, including WSL on Windows. The worker uses the Unix-only `fcntl` module. PyYAML is the only third-party dependency.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m unittest discover -s tests -v

cp config/config.yaml config/local.yaml
# Edit config/local.yaml with your SMTP settings and local route paths.
python -m scanmail --config config/local.yaml --check-config
```

For local execution, `--config` defaults to `config/config.yaml` and `--state-dir` to `/state`. `SCANMAIL_CONFIG` and `SCANMAIL_STATE_DIR` override those defaults, and explicit CLI arguments take precedence. Set a writable local state directory when running the worker outside Docker:

```bash
python -m scanmail --config config/local.yaml --state-dir ./state
```

The Docker entrypoint handles `PUID`/`PGID`; direct `python -m scanmail` runs as your current user. All SMTP settings come from YAML in both cases. No `.env` loading is required.

Project instructions are in [AGENTS.md](AGENTS.md). The Dockerfile builds the image from `scanmail/` and `requirements.txt`; configuration and NAS data are supplied at runtime.
