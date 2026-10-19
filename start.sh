#!/usr/bin/env bash
# Production entrypoint (Render). Free instances have no pre-deploy hook or shell,
# so migrations, catalog refresh and admin bootstrap run on every boot — all idempotent.
set -euo pipefail

python manage.py migrate --noinput
python manage.py seed_real_data
if [[ -n "${ADMIN_IDENTIFIER:-}" ]]; then
  python manage.py create_admin --identifier "$ADMIN_IDENTIFIER" ${ADMIN_PASSWORD:+--password "$ADMIN_PASSWORD"}
fi

exec gunicorn swiftpass.wsgi:application --bind "0.0.0.0:${PORT:-8000}" --workers 2 --timeout 60
