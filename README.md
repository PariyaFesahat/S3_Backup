# S3 Backup Watcher

A Python filesystem watcher that automatically synchronizes files from one or more local directories to one or more S3-compatible storage destinations, such as MinIO, AWS S3, or Wasabi.

## Features

- Watch multiple directories recursively.
- Route each local path to its own S3 destination (bucket, region, credentials) via an explicit path -> target mapping.
- Detect new, modified, deleted, and moved files.
- Automatically synchronize changes to S3-compatible storage.
- Supports MinIO, AWS S3, and other S3-compatible services.
- Preserve original filenames.
- Organize backups by server name, backup date, and backup directory.
- Multiple backup directories created on the same date share the same date directory.
- Ignore temporary/editor files such as `.swp`, `.swo`, `.swn`, `*~`, and `.#*`.
- Configurable filesystem-event debounce.
- Multipart uploads for large files.
- Configurable S3 retry attempts and upload concurrency.
- A failure uploading one path does not stop the others; a summary is logged at the end of each run.
- Logs are written to stdout for Docker.
- Docker CPU, memory, PID, and log limits can be configured.
- Configurable timezone.

## Project Structure

```text
S3_Backup/
├── config/
│   └── config.yaml
├── src/
│   ├── backup.py
│   ├── config.py
│   ├── logging_config.py
│   ├── main.py
│   ├── manager.py
│   ├── s3.py
│   └── watcher.py
├── tests/
│   ├── test_config.py
│   └── test_main.py
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── requirements-dev.txt
├── pytest.ini
├── .dockerignore
├── .gitignore
├── TODO.md
└── README.md
```

## Configuration

Example `config/config.yaml`:

```yaml
server:
  name: "db-server-01"

# Named S3 destinations. Each target is a distinct bucket, and may use
# a different account/region/endpoint/credentials.
targets:
  - name: "primary-minio"
    enabled: true
    bucket: "my-postgres-backups"
    region: "us-east-1"
    endpoint_url: "http://192.168.1.50:9000"
    access_key_id: "minioadmin"
    secret_access_key: "minioadmin123"
    prefix: "postgres/"

  - name: "offsite-wasabi"
    enabled: true
    bucket: "offsite-backups"
    region: "us-east-2"
    endpoint_url: "https://s3.wasabisys.com"
    access_key_id: "${WASABI_ACCESS_KEY}"      # resolved from the environment
    secret_access_key: "${WASABI_SECRET_KEY}"

  - name: "archive-glacier"
    enabled: false                             # target off; any mapping using it is skipped
    bucket: "archive-bucket"
    region: "eu-central-1"
    profile: "archive-role"                    # AWS named profile / IAM role instead of static keys

# Each local path is explicitly routed to one or more targets above.
mappings:
  - path: "/opt/test"
    target_name: "primary-minio"
    enabled: true

  - path: "/db_dump"                           # same path, several targets
    target_names: ["primary-minio", "offsite-wasabi"]
    enabled: true

  - path: "/dump"
    target_name: "offsite-wasabi"
    destination_prefix: "dump-backups/"        # overrides the target's own `prefix` for this path
    enabled: true

retention:
  days: 10

watcher:
  debounce_seconds: 5

logging:
  level: INFO
```

## S3 Targets

`targets` is a list of named S3 destinations. Each entry supports:

| field | required | notes |
|---|---|---|
| `name` | yes | unique identifier, referenced by `mappings[].target_name` / `mappings[].target_names` |
| `bucket` | yes | destination bucket |
| `region` | no | default `us-east-1` |
| `endpoint_url` | no | for MinIO/Wasabi/other S3-compatible services |
| `access_key_id` / `secret_access_key` | no | must both be set, or both omitted |
| `profile` | no | AWS named profile/IAM role; mutually exclusive with the key pair |
| `prefix` | no | default S3 key prefix for this target |
| `enabled` | no | default `true`; set `false` to disable the target without deleting its config |

If neither a key pair nor `profile` is set, boto3's default credential chain is used (environment variables, `~/.aws/credentials`, or an IAM role).

Values may reference environment variables with `${VAR_NAME}` syntax (e.g. for secrets you don't want committed to Git); the app fails fast with a clear error if the referenced variable isn't set.

## Path -> Target Mappings

`mappings` is a list that assigns each local backup path to **one or more** S3 targets. A path mapped to several targets is uploaded to each of them independently:

```text
Source path
    ├── target: parspack
    ├── target: hetzner
    └── target: another-s3
```

| field | required | notes |
|---|---|---|
| `path` | yes | local directory to watch/back up; may appear in several mappings, once per target |
| `target_name` | one of the two | a single target; must match a `targets[].name` |
| `target_names` | one of the two | a list of targets for this path; each must match a `targets[].name` |
| `destination_prefix` | no | if set, replaces (does not append to) the target's own `prefix` for this path |
| `enabled` | no | default `true`; disables this whole mapping entry |

Fanning one path out to several targets can be written either way — as one entry with `target_names`, or as one entry per target with `target_name`:

```yaml
mappings:
  - path: "/db_dump"
    target_names: ["parspack", "hetzner"]

# ...is equivalent to:
mappings:
  - path: "/db_dump"
    target_name: "parspack"
  - path: "/db_dump"
    target_name: "hetzner"
```

Config loading fails fast with a clear error if:

- a mapping references a target that doesn't exist in `targets`
- a mapping sets neither `target_name` nor `target_names`, or sets both
- the same path is mapped to the *same* target more than once (that would upload the same content to the same place twice)
- an `access_key_id`/`secret_access_key` pair is only half-set, or combined with `profile`
- no path -> target links remain active after applying `enabled` flags on both mappings and targets

A mapping pointed at a *disabled* target is not a config error — it's simply skipped at runtime and reported in the summary, so toggling a target off pauses everything routed to it without editing `mappings`.

Per target, failures are isolated: if one target is unreachable or an upload to it fails, the other targets for that path still complete, and nothing already uploaded is rolled back. Retention is applied separately per target, and each source path is watched by exactly one filesystem watcher no matter how many targets it feeds.

### Legacy single-bucket format

If `config.yaml` still uses the old format (`backup.source_dirs` + a single `s3:` block, no `targets`/`mappings`), it's auto-migrated at load time: one target is generated from the `s3:` block, and one mapping per `source_dirs` entry is generated pointing at it. A deprecation warning is logged. Update the file to the new format above when convenient — the migration shim may be removed in a future version.

### Fan-out shorthand (`source_dirs` + `targets`, no `mappings`)

If `backup.source_dirs` is given alongside `targets` but `mappings` is omitted, every source dir is mapped to **every** target:

```yaml
backup:
  source_dirs:
    - /db_dump

targets:
  - name: parspack
    ...
  - name: hetzner
    ...
```

is expanded to one mapping per source dir with `target_names: [parspack, hetzner]`. Use explicit `mappings` when different paths need different targets.

## Server Name

The server name identifies the host that produced the backup:

```yaml
server:
  name: "db-server-01"
```

Use a unique name on each server:

```text
db-server-01
db-server-02
db-server-03
```

The configured name is used instead of the Docker container hostname.

## Backup Directories

Backup paths are declared as `mappings[].path` entries — there is no separate directory list; a path is only watched if it appears in `mappings`:

```yaml
mappings:
  - path: "/opt/test"
    target_name: "primary-minio"

  - path: "/dump"
    target_name: "offsite-wasabi"

  - path: "/db_dump"
    target_name: "primary-minio"
```

Every path in `mappings` is watched recursively (once per path, even when it feeds several targets) and synchronized to each target assigned to it.

All file extensions are supported.

Example:

```text
/dump/
├── backup001/
│   ├── database.dump
│   └── metadata.txt
├── backup002/
│   └── database.sql
└── backup003/
    └── backup.tar
```

## S3 / MinIO

The application uses `boto3` and communicates with an S3-compatible API.

For MinIO running directly on the Ubuntu host:

```yaml
endpoint_url: "http://localhost:9000"
```

If MinIO is another container on the same Docker network:

```yaml
endpoint_url: "http://minio:9000"
```

If MinIO is running on another host:

```yaml
endpoint_url: "http://192.168.1.50:9000"
```

## S3 Backup Layout

Backups are stored using:

```text
<target-or-mapping-prefix>/<server-name>/<backup-date>/<backup-directory>/
```

The prefix is the mapping's `destination_prefix` if set, otherwise the target's own `prefix`.

Example:

```text
postgres/
└── db-server-01/
    ├── 2026-08-19/
    │   ├── backup001/
    │   │   ├── database.dump
    │   │   └── metadata.txt
    │   └── backup002/
    │       └── database.dump
    └── 2026-08-20/
        └── backup003/
            └── database.dump
```

This keeps backups from different servers separated.

Multiple backup directories created on the same date are stored under the same date directory.

## Backup Date

The backup date is based on the filesystem timestamp of the top-level backup directory.

For example:

```text
/dump/backup001
```

The directory timestamp corresponds to the timestamp displayed by commands such as:

```bash
ls -alh /dump
```

The resulting date is used in the S3 path:

```text
YYYY-MM-DD
```

## Synchronization

When the application starts, existing backup directories are synchronized.

After startup, the filesystem watcher continuously monitors the configured directories.

The workflow is:

```text
Filesystem change
       |
       v
Watchdog event
       |
       v
Debounce
       |
       v
BackupManager
       |
       v
S3 synchronization
```

New or modified files are uploaded.

Deleted files are removed from the corresponding S3 backup during synchronization.

## Temporary Files

The watcher ignores temporary/editor files:

```text
*.swp
*.swo
*.swn
*~
.#*
```

Examples:

```text
.test.txt.swp
test.txt~
.#test.txt
```

These files are not uploaded.

## Debounce

The watcher uses a debounce period to prevent a large number of filesystem events from triggering many immediate synchronization operations.

Example:

```yaml
watcher:
  debounce_seconds: 5
```

If several changes happen within the debounce period, they are grouped into one synchronization.

## Large File Uploads

Large files use multipart uploads. These values currently apply uniformly to every target and are fixed in `src/s3.py` (not read from `config.yaml`):

- Retry attempts: `max_attempts: 10`, `mode: adaptive` — retry budget for transient S3/MinIO failures.
- Upload concurrency: `max_concurrency: 2` — limits simultaneous multipart upload operations; useful when MinIO returns `429 Too Many Requests`.
- Multipart threshold: `64 MB` — files larger than this use multipart uploads.
- Multipart chunk size: `64 MB` — size of each multipart part.

## Docker

Example Docker Compose configuration:

```yaml
services:
  s3-backup:
    build: .

    container_name: s3-backup

    restart: unless-stopped

    volumes:
      - /dump:/dump:rw
      - /opt/test:/opt/test:rw
      - ./config/config.yaml:/app/config/config.yaml:ro

    environment:
      TZ: Asia/Tehran

    mem_limit: 512m
    mem_reservation: 128m
    cpus: 0.50
    pids_limit: 100

    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "5"
```

### Resource limits

```yaml
mem_limit: 512m
```

Maximum memory available to the container.

```yaml
mem_reservation: 128m
```

Soft memory reservation.

```yaml
cpus: 0.50
```

Limits the container to approximately half of one CPU core.

```yaml
pids_limit: 100
```

Limits the number of processes/threads inside the container.

## Docker Logging

The application writes logs to stdout.

View logs:

```bash
docker compose logs -f s3-backup
```

Or:

```bash
docker logs -f s3-backup
```

Docker log rotation:

```yaml
logging:
  driver: json-file
  options:
    max-size: "10m"
    max-file: "5"
```

This keeps up to five log files, each limited to approximately 10 MB.

## Timezone

The Docker container can use the Tehran timezone:

```yaml
environment:
  TZ: Asia/Tehran
```

Check the container time:

```bash
docker exec s3-backup date
```

## Installation

Create a virtual environment:

```bash
python3 -m venv .venv
```

Activate it:

```bash
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Run locally from the repository root:

```bash
python -m src.main
```

To run the test suite (uses `pytest` and `moto` to mock S3):

```bash
pip install -r requirements-dev.txt
pytest
```

## Docker Commands

Build:

```bash
docker compose build
```

Start:

```bash
docker compose up -d
```

View logs:

```bash
docker compose logs -f s3-backup
```

Stop:

```bash
docker compose down
```

Restart:

```bash
docker compose restart s3-backup
```

Rebuild after code changes:

```bash
docker compose down
docker compose build
docker compose up -d
```

Check status:

```bash
docker ps
```

Check resource usage:

```bash
docker stats s3-backup
```

## MinIO Connectivity

If MinIO exposes port 9000:

```bash
docker ps
```

You should see something similar to:

```text
0.0.0.0:9000->9000/tcp
```

Test the S3 API:

```bash
curl http://127.0.0.1:9000
```

An `AccessDenied` XML response is expected when accessing the S3 API without authentication. It confirms that the endpoint is reachable.

## Troubleshooting

### YAML error

For:

```text
yaml.scanner.ScannerError:
sequence entries are not allowed here
```

check the configuration:

```bash
nl -ba config/config.yaml
```

Validate it:

```bash
python -c "import yaml; print(yaml.safe_load(open('config/config.yaml')))"
```

### S3 credentials error

Make sure each target in `targets:` contains a matched pair:

```yaml
targets:
  - name: "primary-minio"
    access_key_id: "..."
    secret_access_key: "..."
```

`access_key_id` and `secret_access_key` must both be set, or both omitted (to fall back to a `profile` or the default AWS credential chain). Setting only one raises a config error at startup naming the offending target.

### Unknown target / duplicate path -> target error

```text
Mapping for path '/opt/test' references unknown target 'primary-mino'. Known targets: primary-minio, offsite-wasabi
```

Check for typos in `mappings[].target_name`, and confirm the target is spelled identically in `targets[].name`.

```text
Path '/opt/test' is mapped to target 'primary-minio' more than once; a path may map to many targets, but only once to each
```

A `path` may map to as many targets as you like, but only once to each. Remove the redundant entry (or point it at a different target).

### MinIO connection error

Check the endpoint:

```bash
curl http://<minio-host>:9000
```

Inside a Docker container, `localhost` refers to that container itself, not the Ubuntu host or another container.

### Container continuously restarting

Check:

```bash
docker compose logs --tail=200 s3-backup
```

Then:

```bash
docker inspect s3-backup --format='OOMKilled={{.State.OOMKilled}} ExitCode={{.State.ExitCode}}'
```

Exit code `137` commonly indicates the process was killed with `SIGKILL`, often because the container exceeded its memory limit.

### HTTP 429 Too Many Requests

If a large multipart upload fails with:

```text
429 Too Many Requests
```

reduce upload concurrency:

```yaml
max_concurrency: 2
```

and increase retry attempts:

```yaml
max_attempts: 10
```

## Retention

The configuration contains:

```yaml
retention:
  days: 10
```

The intended policy is to retain backups for 10 days.

Retention cleanup should be implemented/enabled in the application before relying on this setting for automatic deletion.

## Security

S3 credentials can be stored directly in YAML per target:

```yaml
targets:
  - name: "primary-minio"
    access_key_id: "..."
    secret_access_key: "..."
```

Do not commit production credentials to Git.

For production, prefer `${ENV_VAR}` interpolation (resolved from the environment at load time), an AWS `profile`, Docker secrets, or an IAM role — omit `access_key_id`/`secret_access_key` entirely to use the default AWS credential chain.

