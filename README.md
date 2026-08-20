# S3 Backup Watcher

A Python filesystem watcher that automatically synchronizes files from one or more local directories to an S3-compatible storage service such as MinIO.

## Features

- Watch multiple directories recursively.
- Detect new, modified, deleted, and moved files.
- Automatically synchronize changes to S3-compatible storage.
- Supports MinIO and other S3-compatible services.
- Preserve original filenames.
- Organize backups by server name, backup date, and backup directory.
- Multiple backup directories created on the same date share the same date directory.
- Ignore temporary/editor files such as `.swp`, `.swo`, `.swn`, `*~`, and `.#*`.
- Configurable filesystem-event debounce.
- Multipart uploads for large files.
- Configurable S3 retry attempts and upload concurrency.
- Logs are written to stdout for Docker.
- Docker CPU, memory, PID, and log limits can be configured.
- Configurable timezone.

## Project Structure

```text
S3_Backup/
├── config/
│   └── config.yaml
├── src/
│   ├── __init__.py
│   ├── backup.py
│   ├── config.py
│   ├── logging_config.py
│   ├── main.py
│   ├── manager.py
│   ├── s3.py
│   └── watcher.py
├── tests/
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
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

backup:
  source_dirs:
    - "/opt/test"
    - "/dump"

s3:
  endpoint_url: "http://192.168.1.50:9000"
  access_key: "minioadmin"
  secret_key: "minioadmin123"
  bucket: "my-postgres-backups"
  prefix: "postgres/"
  region: "us-east-1"

  upload:
    max_attempts: 10
    max_concurrency: 2
    multipart_threshold_mb: 64
    multipart_chunksize_mb: 64

retention:
  days: 10

watcher:
  debounce_seconds: 5

logging:
  level: INFO
```

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

Multiple directories can be configured:

```yaml
backup:
  source_dirs:
    - "/opt/test"
    - "/dump"
    - "/db_dump"
```

Every configured directory is watched recursively.

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
<prefix>/<server-name>/<backup-date>/<backup-directory>/
```

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

Large files use multipart uploads.

Example:

```yaml
s3:
  upload:
    max_attempts: 10
    max_concurrency: 2
    multipart_threshold_mb: 64
    multipart_chunksize_mb: 64
```

### Retry attempts

```yaml
max_attempts: 10
```

Increases the retry budget for transient S3/MinIO failures.

### Upload concurrency

```yaml
max_concurrency: 2
```

Limits simultaneous multipart upload operations.

This is useful when MinIO returns:

```text
429 Too Many Requests
```

### Multipart threshold

```yaml
multipart_threshold_mb: 64
```

Files larger than 64 MB use multipart uploads.

### Multipart chunk size

```yaml
multipart_chunksize_mb: 64
```

Each multipart part is 64 MB.

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

Make sure the YAML contains:

```yaml
s3:
  access_key: "..."
  secret_key: "..."
```

The key must be named `secret_key`.

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

The current configuration stores S3 credentials in YAML:

```yaml
access_key: "..."
secret_key: "..."
```

Do not commit production credentials to Git.

For production, consider environment variables or Docker secrets.

