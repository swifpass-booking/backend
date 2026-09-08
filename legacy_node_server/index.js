import express from 'express';
import cors from 'cors';
import jwt from 'jsonwebtoken';
import { db } from './db.js';
import { hashPassword, verifyPassword, newUserId } from './auth.js';

// Dev-only signing secret. A real deployment reads this from env/secret
// manager — there is no production path in this prototype, so a fixed
// local constant is fine.
const JWT_SECRET = 'swiftpass-dev-secret-do-not-use-in-production';
const TOKEN_TTL = '7d';

const app = express();
app.use(cors());
app.use(express.json());

function toPublicUser(row) {
  return {
    id: row.id,
    fullName: row.full_name,
    email: row.email,
    phoneE164: row.phone_e164,
    locale: row.locale,
    role: row.role,
  };
}

function isValidEmail(v) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v);
}
function isValidPhone(v) {
  return /^\+?\d{7,15}$/.test(v);
}

app.post('/v1/auth/register', (req, res) => {
  const { fullName, identifier, password } = req.body || {};

  if (!fullName || typeof fullName !== 'string' || fullName.trim().length < 2) {
    return res.status(400).json({ error: { code: 'validation_failed', message: 'Full name is required.' } });
  }
  if (!password || typeof password !== 'string' || password.length < 8) {
    return res.status(400).json({ error: { code: 'validation_failed', message: 'Password must be at least 8 characters.' } });
  }
  const id = (identifier || '').trim();
  const isEmail = isValidEmail(id);
  const isPhone = isValidPhone(id);
  if (!isEmail && !isPhone) {
    return res.status(400).json({ error: { code: 'validation_failed', message: 'Enter a valid email or phone number.' } });
  }

  const existing = db
    .prepare('SELECT id FROM users WHERE email = ? OR phone_e164 = ?')
    .get(isEmail ? id : null, isPhone ? id : null);
  if (existing) {
    return res.status(409).json({ error: { code: 'idempotency_conflict', message: 'An account with that email or phone already exists.' } });
  }

  const { hash, salt } = hashPassword(password);
  const userId = newUserId();
  db.prepare(
    `INSERT INTO users (id, email, phone_e164, password_hash, password_salt, full_name)
     VALUES (?, ?, ?, ?, ?, ?)`
  ).run(userId, isEmail ? id : null, isPhone ? id : null, hash, salt, fullName.trim());

  const user = db.prepare('SELECT * FROM users WHERE id = ?').get(userId);
  const token = jwt.sign({ sub: user.id }, JWT_SECRET, { expiresIn: TOKEN_TTL });
  res.status(201).json({ token, user: toPublicUser(user) });
});

app.post('/v1/auth/login', (req, res) => {
  const { identifier, password } = req.body || {};
  const id = (identifier || '').trim();
  if (!id || !password) {
    return res.status(400).json({ error: { code: 'validation_failed', message: 'Enter your email/phone and password.' } });
  }

  const user = db.prepare('SELECT * FROM users WHERE email = ? OR phone_e164 = ?').get(id, id);
  if (!user || !verifyPassword(password, user.password_hash, user.password_salt)) {
    return res.status(401).json({ error: { code: 'unauthorised', message: 'Incorrect email/phone or password.' } });
  }

  const token = jwt.sign({ sub: user.id }, JWT_SECRET, { expiresIn: TOKEN_TTL });
  res.json({ token, user: toPublicUser(user) });
});

function requireAuth(req, res, next) {
  const header = req.headers.authorization || '';
  const token = header.startsWith('Bearer ') ? header.slice(7) : null;
  if (!token) return res.status(401).json({ error: { code: 'unauthorised', message: 'Sign in required.' } });
  try {
    req.userId = jwt.verify(token, JWT_SECRET).sub;
    next();
  } catch {
    res.status(401).json({ error: { code: 'unauthorised', message: 'Session expired — sign in again.' } });
  }
}

app.get('/v1/auth/me', requireAuth, (req, res) => {
  const user = db.prepare('SELECT * FROM users WHERE id = ?').get(req.userId);
  if (!user) return res.status(401).json({ error: { code: 'unauthorised', message: 'Account no longer exists.' } });
  res.json({ user: toPublicUser(user) });
});

const PORT = 3000;
app.listen(PORT, () => {
  console.log(`Swiftpass auth API listening on http://localhost:${PORT}`);
});
