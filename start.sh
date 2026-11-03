#!/usr/bin/env bash
# Production entrypoint (Render). Free instances have no pre-deploy hook or shell,
# so migrations, catalog refresh and admin bootstrap run on every boot — all idempotent.
set -euo pipefail

python manage.py migrate --noinput
python manage.py seed_real_data
if [[ -n "${OPENFGA_API_URL:-}" ]]; then
  python manage.py openfga_setup && python manage.py openfga_sync || echo "OpenFGA setup failed: staff and admin checks will be denied until it succeeds."
else
  echo "OPENFGA_API_URL is not set: every admin and staff check will be denied."
fi
if [[ -n "${ADMIN_IDENTIFIER:-}" ]]; then
  # Non-fatal: a new admin needs ADMIN_PASSWORD; without it this only promotes an existing account.
  python manage.py create_admin --identifier "$ADMIN_IDENTIFIER" ${ADMIN_PASSWORD:+--password "$ADMIN_PASSWORD"}     || echo "Admin bootstrap skipped (set ADMIN_PASSWORD to create $ADMIN_IDENTIFIER)."
fi

exec gunicorn swiftpass.wsgi:application --bind "0.0.0.0:${PORT:-8000}" --worker-class gthread --workers 2 --threads 4 --timeout 60
