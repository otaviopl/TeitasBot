#!/bin/sh
set -e

python scripts/seed_user.py

exec "$@"
