// Real, persistent SQLite database — the `users` table mirrors the shape
// already defined in ../schema.sql, kept small since this is the one slice
// of the (otherwise unbuilt) backend this prototype needs: auth.
import { DatabaseSync } from 'node:sqlite';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const dbPath = path.join(__dirname, 'swiftpass.db');

export const db = new DatabaseSync(dbPath);

db.exec(`
  CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    phone_e164    TEXT UNIQUE,
    email         TEXT UNIQUE,
    password_hash TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    full_name     TEXT NOT NULL,
    locale        TEXT NOT NULL DEFAULT 'en-NP',
    role          TEXT NOT NULL DEFAULT 'traveller',
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
  );
`);
