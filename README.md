# Swiftpass Backend

Django 6 JSON API for Swiftpass: catalog search, seat maps, holds and carts, bookings and payments, tickets, gate scanning, and admin stats. All routes live under `/v1/`.

## Stack

- Python 3.13, Django 6.1, gunicorn (gthread), WhiteNoise
- SQLite locally, Postgres via `DATABASE_URL`
- JWT auth (PyJWT), role checks through OpenFGA
- Payment gateways: eSewa, Khalti, Fonepay, Stripe (test mode)
- OSRM for bus routes (optional own server via `OSRM_BASE_URL`)

## Apps

| App | Owns |
| --- | --- |
| `accounts` | Users, JWT, rate limiting, OpenFGA authz, staff and admin commands |
| `catalog` | Places, providers, services, search, seat maps, live tracking, routing |
| `carts` | Seat holds, carts, cart items |
| `bookings` | Bookings, passengers, tickets, gateways, payment reconcile |
| `scanning` | Gate scan, redeem, scan history |
| `adminapi` | Admin stats, users, occurrences, bookings |
| `swiftpass` | Settings, root URLs, WSGI/ASGI |

## Endpoints

| Area | Routes |
| --- | --- |
| Auth | `POST auth/register`, `POST auth/login`, `POST auth/logout`, `GET auth/me`, `preferences` |
| Catalog | `GET places`, `featured`, `search`, `occurrences/<id>/tracking`, `occurrences/<id>/seatmap` |
| Carts | `POST holds`, `POST carts`, `GET carts/<id>`, `POST carts/<id>/items` |
| Bookings | `POST bookings`, `GET bookings/mine`, `bookings/lookup`, `payments/verify`, `payments/stripe/webhook` |
| Scanning | `scan/redeem`, `scan/occurrences`, `scan/stats`, `scan/history` |
| Admin | `admin/stats`, `admin/users`, `admin/occurrences`, `admin/bookings`, `POST admin/bookings/<id>/refund` |

Django admin: `/admin/`.

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # keep DJANGO_DEBUG=1 for throwaway dev secrets
python manage.py migrate
python manage.py seed_real_data
python manage.py seed_stays
python manage.py runserver  # http://localhost:8000
```

The frontend dev server proxies `/v1` to `localhost:8000`.

### OpenFGA (roles)

Admin and staff checks need OpenFGA. Without `OPENFGA_API_URL`, every admin and staff check is denied.

```bash
python manage.py openfga_setup                      # write the authz model
python manage.py create_admin --identifier <id> --password <pw>
python manage.py grant_staff ...                    # give staff rights
python manage.py revoke_access ...                  # remove rights
```

`openfga_sync` is a one-time import of the old `User.role` column. Run it by hand, never on boot.

## Management commands

| Command | Does |
| --- | --- |
| `seed_real_data`, `seed_stays` | Load or refresh catalog. Idempotent. |
| `create_admin`, `grant_staff`, `revoke_access`, `set_password` | User and role admin |
| `openfga_setup`, `openfga_sync` | OpenFGA model and legacy import |
| `reconcile_payments --limit N` | Settle payments whose browser return never arrived |
| `release_stale_pending` | Release stale pending bookings |

## Environment

See `.env.example` for the full list.

| Variable | Purpose |
| --- | --- |
| `DJANGO_DEBUG` | `1` enables fallback dev secrets |
| `DJANGO_SECRET_KEY`, `JWT_SECRET` | Required in production |
| `DJANGO_ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS` | Host and CORS allowlists |
| `DATABASE_URL` | Postgres. Unset means local `db.sqlite3`. |
| `OPENFGA_API_URL`, `OPENFGA_API_TOKEN` | Authz service |
| `TRUSTED_PROXY_HOPS` | Proxies in front of gunicorn. Rate limits use the real client IP. |
| `DJANGO_SSL_REDIRECT` | `1` forces HTTPS |
| `FRONTEND_BASE_URL` | Where the browser returns after paying |
| `ESEWA_*`, `KHALTI_SECRET_KEY`, `FONEPAY_*` | Gateway credentials (sandbox by default) |
| `ADMIN_IDENTIFIER`, `ADMIN_PASSWORD` | Bootstrap first admin on boot |
| `OSRM_BASE_URL` | Own OSRM server for bus routes |
| `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_CURRENCY` | Stripe test mode (see below). No key + `DJANGO_DEBUG=1` runs the local simulator. |
| `DEMO_INSTANT_PAYMENT` | Local preview only: instant-confirm bookings, fake Khalti. Never in production. |

## Stripe (test mode)

Hosted Stripe Checkout, same redirect flow as the other gateways. A booking waits as `pending_payment`, then `payments/verify` re-fetches the session from Stripe before confirming. The signed webhook settles payments whose browser never came back.

**Simulator (no key).** With `DJANGO_DEBUG=1` and no `STRIPE_SECRET_KEY`, pick Stripe at checkout. You land on `/payment/stripe/sim` and choose how the payment ends: success, card declined, insufficient funds, expired card, bad CVC, 3-D Secure failure, processing error, closed tab (webhook only), paid after hold expired, session expired, underpaid, back-button cancel. Endpoint `POST /v1/dev/stripe/simulate` returns 404 outside dev.

**Real test mode.**

```bash
export STRIPE_SECRET_KEY=sk_test_...
stripe listen --forward-to localhost:8000/v1/payments/stripe/webhook   # prints whsec_..., set STRIPE_WEBHOOK_SECRET
stripe trigger checkout.session.completed
```

Newer CLI versions need `--events` on `listen`:

```bash
stripe listen --events checkout.session.completed,checkout.session.async_payment_succeeded,checkout.session.async_payment_failed,checkout.session.expired,charge.refunded --forward-to localhost:8000/v1/payments/stripe/webhook
```

**Refunds.** Admin dashboard, Bookings: a Refund button shows on paid Stripe bookings (confirmed, or "Refund due" after a late payment). It refunds the full amount, releases the seats and voids the tickets. A refund done in the Stripe dashboard also lands through the `charge.refunded` webhook. Refused when a ticket was already scanned. In the simulator the refund is fake but the booking changes are real.

Test cards: `4242 4242 4242 4242` pays, `4000 0000 0000 0002` declines, `4000 0025 0000 3155` asks for 3-D Secure, `4000 0000 0000 9995` has no funds. If the Stripe account cannot charge NPR, set `STRIPE_CURRENCY=usd`.

## Tests

```bash
python manage.py test
```

## Docker

```bash
docker build -t swiftpass-api .
docker run -p 8000:8000 --env-file .env swiftpass-api
```

`start.sh` is the container entrypoint. On every boot it runs `migrate`, `seed_real_data`, `seed_stays`, `reconcile_payments`, `openfga_setup` (if `OPENFGA_API_URL` set) and `create_admin` (if `ADMIN_IDENTIFIER` set). All are idempotent. Then it starts gunicorn on `$PORT` (default 8000).

Full local stack (Postgres, OpenFGA, backend, voice agent, frontend):

```bash
docker compose up --build   # open http://localhost:8088
```

Expects `../frontend` and `../models/agent-project` next to this directory. Agent settings live in `../models/agent-project/.env`.

`render.yaml` holds the Render deploy config.
