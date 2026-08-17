# S3 Backup

Python application for uploading backup directories from a Linux server to an S3-compatible object storage service such as MinIO.

## Overview

The application scans a configured local backup directory and uploads each backup directory to S3.

For example:

```text
/dump/
├── back090087/
├── back090088/
└── back090089/
```

The backups are stored using the server hostname and the directory date:

```text
postgres/
└── server01/
    └── 2026-08-17/
        ├── back090087/
        ├── back090088/
        └── back090089/
```

The date is determined from the directory modification time (`mtime`), which is the timestamp normally displayed by `ls -l`.

The application does not overwrite an existing backup directory in S3.

## Features

- Upload backup directories from a Linux server.
- Support for S3-compatible storage such as MinIO.
- Automatically uses the server hostname in the S3 path.
- Uses the local backup directory's `mtime` as the backup date.
- Preserves original backup directory and file names.
- Groups multiple backup directories with the same date under one date directory.
- Skips a backup directory if it already exists in S3.
- Configuration is stored in YAML.
- S3 credentials are currently stored in the YAML configuration.

## Requirements

- Python 3.9+
- Linux/Ubuntu server
- S3-compatible storage
- MinIO or another S3-compatible server
- Network access from the backup server to the S3 endpoint

## Project Structure

```text
S3_Backup/
├── config/
│   └── config.yaml
├── src/
│   ├── __init__.py
│   ├── backup.py
│   ├── config.py
│   ├── main.py
│   └── s3.py
├── tests/
│   └── __init__.py
├── requirements.txt
├── .gitignore
└── README.md
```

## Installation

Clone or copy the project to the server:

```bash
cd /home/<user>/
git clone <repository-url> S3_Backup
cd S3_Backup
```

Create a Python virtual environment:

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

## Configuration

Create:

```text
config/config.yaml
```

Example:

```yaml
backup:
  source_dir: /dump

s3:
  endpoint_url: "http://localhost:9000"
  access_key: "your-access-key"
  secret_key: "your-secret-key"
  bucket: "my-postgres-backups"
  prefix: "postgres"
  region: "us-east-1"

retention:
  days: 10

logging:
  level: INFO
```

### Configuration Options

#### Backup

| Option | Description |
|---|---|
| `source_dir` | Directory containing backup directories |

The application searches for directories directly under `source_dir`.

#### S3

| Option | Description |
|---|---|
| `endpoint_url` | S3-compatible storage endpoint |
| `access_key` | S3/MinIO access key |
| `secret_key` | S3/MinIO secret key |
| `bucket` | Destination bucket |
| `prefix` | Root prefix for uploaded backups |
| `region` | S3 region |

For MinIO, the S3 API normally uses port `9000`:

```yaml
s3:
  endpoint_url: "http://192.168.1.100:9000"
```

The MinIO web console port, commonly `9001`, should not be used as the S3 endpoint.

#### Retention

The configuration contains:

```yaml
retention:
  days: 10
```

The retention setting is reserved for the backup cleanup functionality.

## Running the Application

Run the application from the project root:

```bash
cd /home/<user>/S3_Backup
source .venv/bin/activate
python -m src.main
```

Example output:

```text
Found 3 backup directory(s):
  /dump/back090087
  /dump/back090088
  /dump/back090089

Checking S3 bucket: my-postgres-backups
S3 bucket is accessible.

Processing: back090087 (date: 2026-08-17)
Uploading: /dump/back090087/backup.dump
       to: s3://my-postgres-backups/postgres/server01/2026-08-17/back090087/backup.dump
Backup uploaded: back090087 -> 2026-08-17
```

## S3 Directory Structure

The application creates:

```text
<prefix>/
└── <server-hostname>/
    └── <backup-date>/
        └── <backup-directory>/
            └── backup-files
```

Example:

```text
postgres/
└── db-server-01/
    ├── 2026-08-15/
    │   └── back090080/
    │       └── backup.dump
    ├── 2026-08-16/
    │   ├── back090081/
    │   │   └── backup.dump
    │   └── back090082/
    │       └── backup.dump
    └── 2026-08-17/
        ├── back090083/
        │   └── backup.dump
        └── back090084/
            └── backup.dump
```

Multiple backup directories with the same date are stored under the same date directory.

## Duplicate Handling

The application does not overwrite an existing backup directory.

If this already exists:

```text
postgres/
└── db-server-01/
    └── 2026-08-17/
        └── back090087/
            └── backup.dump
```

and `/dump/back090087` is processed again, the application skips it.

The existing S3 backup is not modified.

A new directory such as:

```text
/dump/back090088/
```

will still be uploaded to:

```text
postgres/db-server-01/2026-08-17/back090088/
```

## Directory Date

The backup date is obtained from the directory modification timestamp:

```python
backup_dir.stat().st_mtime
```

This corresponds to the timestamp normally shown by:

```bash
ls -l /dump
```

For example:

```text
drwxr-xr-x 2 postgres postgres 4096 Aug 17 09:30 back090087
```

will result in the S3 date directory:

```text
2026-08-17
```

## Testing

Create a test directory:

```bash
mkdir -p /opt/test/back090087
```

Create a test file:

```bash
echo "test backup" > /opt/test/back090087/test.dump
```

Update the configuration:

```yaml
backup:
  source_dir: /opt/test
```

Run:

```bash
python -m src.main
```

Verify that the file appears in MinIO.

## Security

The current configuration stores S3 credentials in:

```text
config/config.yaml
```

Do not commit this file to a public Git repository.

Add it to `.gitignore`:

```gitignore
config/config.yaml
.venv/
__pycache__/
*.pyc
```

Keep a template configuration such as:

```text
config/
├── config.yaml
└── config.example.yaml
```

Example:

```yaml
s3:
  endpoint_url: "http://localhost:9000"
  access_key: "YOUR_ACCESS_KEY"
  secret_key: "YOUR_SECRET_KEY"
  bucket: "my-postgres-backups"
```

In a future version, credentials should preferably be moved to environment variables or another secret-management mechanism.

## Troubleshooting

### Cannot access S3 bucket

Check the S3 endpoint:

```yaml
s3:
  endpoint_url: "http://localhost:9000"
```

Check that MinIO is running:

```bash
systemctl status minio
```

or, if running with Docker:

```bash
docker ps
```

Check connectivity:

```bash
curl http://localhost:9000
```

### No backup directories found

Check the configured source:

```bash
ls -alh /dump
```

The application only processes directories directly under the configured `source_dir`.

Expected structure:

```text
/dump/
├── back090087/
├── back090088/
└── back090089/
```

### Backup is skipped

If the application reports:

```text
Skipping: back090087 already exists in S3
```

the backup directory has already been uploaded to the corresponding S3 date directory.

This is intentional and prevents an existing backup from being overwritten.

## Dependencies

```text
boto3
PyYAML
```

Install them with:

```bash
pip install -r requirements.txt
```

## Future Improvements

- Implement 10-day backup retention.
- Move S3 credentials to environment variables.
- Add structured logging.
- Add retry handling for failed uploads.
- Add upload progress reporting.
- Add systemd service/timer for automatic execution.
- Add automated tests.
- Add checksum verification after upload.
- Add notifications when an upload fails.
