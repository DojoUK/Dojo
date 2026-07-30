#!/bin/sh
set -e

python - <<'PYEOF'
import os
import socket
import sys
import time

host = os.environ.get('DB_HOST', 'db')
port = int(os.environ.get('DB_PORT', 3306))

for _ in range(60):
    try:
        with socket.create_connection((host, port), timeout=2):
            break
    except OSError:
        time.sleep(1)
else:
    sys.exit(f"Timed out waiting for database at {host}:{port}")
PYEOF

python manage.py migrate --noinput

exec "$@"
