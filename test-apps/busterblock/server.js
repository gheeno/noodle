const express = require('express');
const jwt = require('jsonwebtoken');
const cors = require('cors');
const { parse } = require('csv-parse/sync');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const openapi = require('./openapi.json');

const app = express();
const PORT = process.env.PORT || 3333;
const JWT_SECRET = 'busterblock-vhs-secret';

// Load static data at startup
const users = parse(fs.readFileSync(path.join(__dirname, 'data/users.csv'), 'utf8'), { columns: true });
const movies = JSON.parse(fs.readFileSync(path.join(__dirname, 'data/movies.json'), 'utf8'));

// In-memory stores — keyed by userId, parallel-safe because each user has their own key
const carts = new Map();
const orders = new Map();
const reviews = new Map();   // NOOD_0201 — id → review, the api wok's local target

// Startup snapshot of stock so the test-reset endpoint can restore it.
const ORIGINAL_STOCK = new Map(movies.map(m => [m.id, m.stock]));

app.use(cors());
app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));
app.use('/assets', express.static(path.join(__dirname, 'assets')));

// Auth middleware
function auth(req, res, next) {
  const token = (req.headers.authorization || '').replace('Bearer ', '');
  if (!token) return res.status(401).json({ error: 'Unauthorized' });
  try {
    req.user = jwt.verify(token, JWT_SECRET);
    next();
  } catch {
    res.status(401).json({ error: 'Invalid token' });
  }
}

// ─── Auth ────────────────────────────────────────────────────────────────────

app.post('/api/auth/login', (req, res) => {
  const { username, password } = req.body || {};
  const user = users.find(u => u.username === username && u.password === password);
  if (!user) return res.status(401).json({ error: 'Invalid credentials' });
  const token = jwt.sign({ userId: user.username, role: user.role }, JWT_SECRET, { expiresIn: '8h' });
  res.json({ token, user: { username: user.username, email: user.email, role: user.role } });
});

app.post('/api/auth/logout', auth, (req, res) => {
  // Stateless JWT — client drops the token; server acknowledges
  res.json({ message: 'Logged out' });
});

// ─── Movies ──────────────────────────────────────────────────────────────────

app.get('/api/movies', (req, res) => {
  let result = [...movies];
  if (req.query.genre) result = result.filter(m => m.genre === req.query.genre);
  if (req.query.q) {
    const q = req.query.q.toLowerCase();
    result = result.filter(m =>
      m.title.toLowerCase().includes(q) ||
      m.director.toLowerCase().includes(q) ||
      m.cast.toLowerCase().includes(q)
    );
  }
  const page = Math.max(1, parseInt(req.query.page) || 1);
  const limit = Math.min(100, parseInt(req.query.limit) || 50);
  const total = result.length;
  const genres = [...new Set(movies.map(m => m.genre))].sort();
  res.json({ movies: result.slice((page - 1) * limit, page * limit), total, page, limit, genres });
});

app.get('/api/movies/:id', (req, res) => {
  const movie = movies.find(m => m.id === parseInt(req.params.id));
  movie ? res.json(movie) : res.status(404).json({ error: 'Not found' });
});

// ─── Cart ─────────────────────────────────────────────────────────────────────

function cartResponse(userId) {
  const items = (carts.get(userId) || []).map(item => ({
    ...item,
    movie: movies.find(m => m.id === item.movieId)
  }));
  const subtotal = items.reduce((s, i) => s + (i.movie?.price || 0) * i.qty, 0);
  const itemCount = items.reduce((s, i) => s + i.qty, 0);
  return { items, subtotal: +subtotal.toFixed(2), itemCount };
}

app.get('/api/cart', auth, (req, res) => res.json(cartResponse(req.user.userId)));

app.post('/api/cart', auth, (req, res) => {
  const { movieId, qty = 1 } = req.body || {};
  const movie = movies.find(m => m.id === movieId);
  if (!movie) return res.status(404).json({ error: 'Movie not found' });
  if (movie.stock < 1) return res.status(400).json({ error: 'Out of stock' });

  const items = carts.get(req.user.userId) || [];
  const existing = items.find(i => i.movieId === movieId);
  existing ? (existing.qty += qty) : items.push({ movieId, qty });
  carts.set(req.user.userId, items);
  res.json(cartResponse(req.user.userId));
});

app.delete('/api/cart/:movieId', auth, (req, res) => {
  const movieId = parseInt(req.params.movieId);
  carts.set(req.user.userId, (carts.get(req.user.userId) || []).filter(i => i.movieId !== movieId));
  res.json(cartResponse(req.user.userId));
});

app.delete('/api/cart', auth, (req, res) => {
  carts.delete(req.user.userId);
  res.json(cartResponse(req.user.userId));
});

// ─── Orders ──────────────────────────────────────────────────────────────────

app.post('/api/orders', auth, (req, res) => {
  const cartItems = carts.get(req.user.userId) || [];
  if (!cartItems.length) return res.status(400).json({ error: 'Cart is empty' });

  const items = cartItems.map(item => ({
    ...item,
    movie: movies.find(m => m.id === item.movieId)
  }));
  const subtotal = items.reduce((s, i) => s + (i.movie?.price || 0) * i.qty, 0);
  const tax = subtotal * 0.13; // Canadian HST
  const total = subtotal + tax;

  const orderId = `BB-${Date.now()}-${req.user.userId.slice(0, 4).toUpperCase()}`;
  const order = {
    orderId,
    userId: req.user.userId,
    items,
    subtotal: +subtotal.toFixed(2),
    tax: +tax.toFixed(2),
    total: +total.toFixed(2),
    createdAt: new Date().toISOString()
  };
  orders.set(orderId, order);
  carts.delete(req.user.userId);

  // Decrement stock (best-effort, in-memory only)
  items.forEach(({ movieId, qty }) => {
    const m = movies.find(m => m.id === movieId);
    if (m) m.stock = Math.max(0, m.stock - qty);
  });

  res.status(201).json(order);
});

// No auth on receipt — orderId is the token (share-safe)
app.get('/api/orders/:id', (req, res) => {
  const order = orders.get(req.params.id);
  order ? res.json(order) : res.status(404).json({ error: 'Order not found' });
});

app.get('/api/orders', auth, (req, res) => {
  const userOrders = [...orders.values()].filter(o => o.userId === req.user.userId);
  res.json(userOrders);
});

// ─── Reviews (NOOD_0201) ──────────────────────────────────────────────────────
// A create-validate-persist-paginate resource, so the api wok has a LOCAL
// target for the shapes public sample APIs can't exercise: 201-on-create,
// 400-with-no-persist, a paginated newest-first list, a bearer-protected
// delete, and a published OpenAPI document for discovery to find.
//
// Note the path: reviews are created at POST /api/reviews/new, not
// POST /api/reviews. That is deliberate — a ticket almost always names the
// resource, not the route, and a test authored from the ticket's own wording
// must be corrected by DISCOVERY (GET /openapi.json), never by a guess.

app.get('/api/reviews', (req, res) => {
  const page = Math.max(1, parseInt(req.query.page) || 1);
  const size = Math.min(100, Math.max(1, parseInt(req.query.size) || 10));
  const all = [...reviews.values()].sort((a, b) => b.date.localeCompare(a.date));
  const start = (page - 1) * size;
  res.json({
    page, size, total: all.length,
    totalPages: Math.max(1, Math.ceil(all.length / size)),
    items: all.slice(start, start + size)
  });
});

app.get('/api/reviews/:id', (req, res) => {
  const review = reviews.get(req.params.id);
  review ? res.json(review) : res.status(404).json({ error: 'Review not found' });
});

app.post('/api/reviews/new', (req, res) => {
  const { name, rating, comment } = req.body || {};
  // Validation rejects at the boundary and persists NOTHING — the negative
  // path a test asserts by checking the count did not move.
  if (typeof name !== 'string' || !name.trim())
    return res.status(400).json({ error: 'name is required' });
  if (name.trim().length > 80)
    return res.status(400).json({ error: 'name must be 80 characters or fewer' });
  if (rating !== undefined && (!Number.isInteger(rating) || rating < 1 || rating > 5))
    return res.status(400).json({ error: 'rating must be an integer 1-5' });

  const review = {
    id: crypto.randomUUID(),
    name: name.trim(),
    date: new Date().toISOString(),
    rating: rating === undefined ? null : rating,
    comment: typeof comment === 'string' ? comment : null,
    response: `Thanks for the review, ${name.trim()}!`
  };
  reviews.set(review.id, review);
  res.status(201).json(review);
});

app.delete('/api/reviews/:id', auth, (req, res) => {
  if (!reviews.has(req.params.id)) return res.status(404).json({ error: 'Review not found' });
  reviews.delete(req.params.id);
  res.json({ message: 'deleted', id: req.params.id });
});

// The API's own contract, at the route springdoc/FastAPI would publish it on —
// this is what `noodle api-scan http://localhost:3333` reads to learn the REAL
// endpoints (and their request bodies) without being told any of them.
app.get('/openapi.json', (req, res) => res.json(openapi));

// ─── Test seam ─────────────────────────────────────────────────────────────────
// Test-only data-manipulation endpoints — the BDD precondition/teardown surface.
// The in-memory Maps above ARE the "database"; these let a test seed/reset it
// before asserting, the way JDBC fixtures do in Java. ponytail: gated by env, not
// auth — it's a test app. Set BB_TEST_API=0 to disable.
if (process.env.BB_TEST_API !== '0') {
  // Universal teardown: empty carts + orders, restore stock to startup values.
  app.post('/api/test/reset', (req, res) => {
    carts.clear();
    orders.clear();
    reviews.clear();
    movies.forEach(m => { m.stock = ORIGINAL_STOCK.get(m.id) ?? m.stock; });
    res.json({ message: 'reset', movies: movies.length });
  });

  // Force a movie's stock (e.g. 0 to exercise the out-of-stock path).
  app.patch('/api/test/stock', (req, res) => {
    const { movieId, stock } = req.body || {};
    const movie = movies.find(m => m.id === movieId);
    if (!movie) return res.status(404).json({ error: 'Movie not found' });
    if (typeof stock !== 'number' || stock < 0) return res.status(400).json({ error: 'stock must be a number >= 0' });
    movie.stock = stock;
    res.json({ movieId, stock: movie.stock });
  });

  // Pre-fill a user's cart without driving the UI.
  app.post('/api/test/seed-cart', (req, res) => {
    const { username, items } = req.body || {};
    if (!users.find(u => u.username === username)) return res.status(404).json({ error: 'User not found' });
    if (!Array.isArray(items)) return res.status(400).json({ error: 'items must be an array' });
    carts.set(username, items.map(i => ({ movieId: i.movieId, qty: i.qty || 1 })));
    res.json(cartResponse(username));
  });
}

// ─── Health ───────────────────────────────────────────────────────────────────

app.get('/api/health', (req, res) =>
  res.json({ status: 'ok', uptime: process.uptime(), movies: movies.length, users: users.length })
);

// ─── Start ────────────────────────────────────────────────────────────────────

app.listen(PORT, () => {
  console.log(`\n🎬  BusterBlock.ca  →  http://localhost:${PORT}`);
  console.log(`📼  ${movies.length} VHS titles | 👥 ${users.length} users`);
  console.log(`\n    Login: reel_ryan / Popcorn1!\n`);
});
