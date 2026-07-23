/* ── Auth helpers ───────────────────────────────────────────────────────── */
const getToken = () => localStorage.getItem('bb_token');
const setToken = t => localStorage.setItem('bb_token', t);
const clearToken = () => localStorage.removeItem('bb_token');
const getUser = () => JSON.parse(localStorage.getItem('bb_user') || 'null');
const setUser = u => localStorage.setItem('bb_user', JSON.stringify(u));
const clearUser = () => localStorage.removeItem('bb_user');

function requireAuth() {
  if (!getToken()) { window.location.href = '/'; return false; }
  return true;
}

/* ── API ─────────────────────────────────────────────────────────────────── */
async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...(getToken() ? { Authorization: `Bearer ${getToken()}` } : {})
    },
    body: body != null ? JSON.stringify(body) : undefined
  });
  const data = await res.json();
  if (!res.ok) throw Object.assign(new Error(data.error || 'Request failed'), { status: res.status });
  return data;
}

/* ── Toast ───────────────────────────────────────────────────────────────── */
function toast(msg, type = 'success') {
  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  el.textContent = msg;
  document.body.appendChild(el);
  requestAnimationFrame(() => { requestAnimationFrame(() => el.classList.add('visible')); });
  setTimeout(() => {
    el.classList.remove('visible');
    setTimeout(() => el.remove(), 300);
  }, 2200);
}

/* ── Cart badge ──────────────────────────────────────────────────────────── */
async function syncCartBadge() {
  if (!getToken()) return;
  try {
    const { itemCount } = await api('GET', '/api/cart');
    document.querySelectorAll('[data-cart-count]').forEach(el => {
      el.textContent = itemCount;
      el.classList.toggle('hidden', itemCount === 0);
    });
  } catch { /* non-critical */ }
}

/* ── Stars SVG ───────────────────────────────────────────────────────────── */
function starsHTML(rating) {
  // ponytail: defs injected once at page load in each HTML file
  const pts = '10,2 12.4,7.6 18.5,8.3 14,12.6 15.3,18.8 10,15.6 4.7,18.8 6,12.6 1.5,8.3 7.6,7.6';
  let html = '<span class="stars">';
  for (let i = 1; i <= 5; i++) {
    const cls = i <= Math.floor(rating) ? 'filled'
              : (i === Math.ceil(rating) && rating % 1 >= 0.5) ? 'half'
              : 'empty';
    html += `<svg class="star ${cls}" viewBox="0 0 20 20" width="13" height="13"><polygon points="${pts}"/></svg>`;
  }
  html += `</span><span class="rating-val">${rating}</span>`;
  return html;
}

/* ── Genre icon SVG ──────────────────────────────────────────────────────── */
const GENRE_ICONS = {
  Thriller: `<svg width="13" height="13" viewBox="0 0 20 20"><circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" stroke-width="2"/><line x1="13" y1="13" x2="18" y2="18" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/></svg>`,
  'Sci-Fi':  `<svg width="13" height="13" viewBox="0 0 20 20"><circle cx="10" cy="10" r="5" fill="currentColor" opacity=".9"/><ellipse cx="10" cy="10" rx="9" ry="3.5" fill="none" stroke="currentColor" stroke-width="1.5" transform="rotate(-25,10,10)"/></svg>`,
  Horror:    `<svg width="13" height="13" viewBox="0 0 20 20"><ellipse cx="10" cy="9" rx="7" ry="8" fill="currentColor"/><circle cx="7.5" cy="8.5" r="2" fill="#000" opacity=".7"/><circle cx="12.5" cy="8.5" r="2" fill="#000" opacity=".7"/><path d="M7 14 Q10 17 13 14v3H7z" fill="#000" opacity=".5"/></svg>`,
  Action:    `<svg width="13" height="13" viewBox="0 0 20 20"><polygon points="11,1 5,11 10,11 9,19 15,9 10,9" fill="currentColor"/></svg>`,
  Comedy:    `<svg width="13" height="13" viewBox="0 0 20 20"><circle cx="10" cy="10" r="8" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M6.5 10.5 Q10 14.5 13.5 10.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/><circle cx="7" cy="8" r="1.2" fill="currentColor"/><circle cx="13" cy="8" r="1.2" fill="currentColor"/></svg>`,
  Drama:     `<svg width="13" height="13" viewBox="0 0 20 20"><ellipse cx="7" cy="10" rx="5" ry="6.5" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M5 11.5 Q7 15 9 11.5" fill="none" stroke="currentColor" stroke-width="1.2"/><ellipse cx="14" cy="10" rx="5" ry="6.5" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M12 9 Q14 5.5 16 9" fill="none" stroke="currentColor" stroke-width="1.2"/></svg>`,
  War:       `<svg width="13" height="13" viewBox="0 0 20 20"><path d="M10 2 L17 5.5v5.5Q17 16.5 10 19Q3 16.5 3 11V5.5Z" fill="none" stroke="currentColor" stroke-width="1.8"/></svg>`,
  Romance:   `<svg width="13" height="13" viewBox="0 0 20 20"><path d="M10 17 L2.5 9Q1 4 5.5 3Q8 2 10 6Q12 2 14.5 3Q19 4 17.5 9Z" fill="currentColor"/></svg>`,
  Crime:     `<svg width="13" height="13" viewBox="0 0 20 20"><polygon points="10,1 19,9.5 10,19 1,9.5" fill="none" stroke="currentColor" stroke-width="1.8"/><line x1="1" y1="9.5" x2="19" y2="9.5" stroke="currentColor" stroke-width="1"/></svg>`,
  Western:   `<svg width="13" height="13" viewBox="0 0 20 20"><polygon points="10,2 11.8,7.5 17.6,7.5 13,11 14.8,17 10,13.5 5.2,17 7,11 2.4,7.5 8.2,7.5" fill="currentColor"/></svg>`
};

function genreBadge(genre) {
  const icon = GENRE_ICONS[genre] || '';
  return `<span class="genre-badge genre-${genre.replace('/', '-')}">${icon}${genre}</span>`;
}

/* ── Page dispatch ───────────────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
  syncCartBadge();
  const page = document.body.dataset.page;
  if (page === 'login')   initLogin();
  if (page === 'catalog') initCatalog();
  if (page === 'cart')    initCart();
  if (page === 'receipt') initReceipt();
  if (page === 'trailer') initTrailer();
});

/* ════════════════════════════════════════════════════════════════════════════
   LOGIN
   ════════════════════════════════════════════════════════════════════════════ */
function initLogin() {
  if (getToken()) { window.location.href = '/catalog.html'; return; }

  document.getElementById('login-form').addEventListener('submit', async e => {
    e.preventDefault();
    const username = document.getElementById('username').value.trim();
    const password = document.getElementById('password').value;
    const errorEl  = document.getElementById('login-error');
    const btn      = e.target.querySelector('button[type="submit"]');

    btn.disabled = true;
    btn.textContent = 'Logging in…';
    errorEl.classList.add('hidden');

    try {
      const { token, user } = await api('POST', '/api/auth/login', { username, password });
      setToken(token);
      setUser(user);
      window.location.href = '/catalog.html';
    } catch {
      errorEl.textContent = 'Invalid credentials. Please try again.';
      errorEl.classList.remove('hidden');
      btn.disabled = false;
      btn.textContent = 'Login';
    }
  });
}

/* ════════════════════════════════════════════════════════════════════════════
   CATALOG
   ════════════════════════════════════════════════════════════════════════════ */
function initCatalog() {
  if (!requireAuth()) return;

  const user = getUser();
  if (user) {
    const el = document.getElementById('nav-username');
    if (el) el.textContent = user.username;
  }

  document.getElementById('logout-btn')?.addEventListener('click', logout);

  // Load genres, then movies
  loadMovies();

  const searchInput = document.getElementById('search-input');
  const genreSelect = document.getElementById('genre-filter');
  if (searchInput) searchInput.addEventListener('input', debounce(loadMovies, 280));
  if (genreSelect) genreSelect.addEventListener('change', loadMovies);
}

async function loadMovies() {
  const q     = document.getElementById('search-input')?.value || '';
  const genre = document.getElementById('genre-filter')?.value || '';
  const params = new URLSearchParams();
  if (q)     params.set('q', q);
  if (genre) params.set('genre', genre);

  try {
    const { movies, total, genres } = await api('GET', `/api/movies?${params}`);
    renderGenreOptions(genres);
    renderMovieTable(movies, total);
  } catch (err) {
    toast(err.message, 'error');
  }
}

function renderGenreOptions(genres) {
  const sel = document.getElementById('genre-filter');
  if (!sel || sel.dataset.populated) return;
  genres.forEach(g => {
    const opt = document.createElement('option');
    opt.value = g;
    opt.textContent = g;
    sel.appendChild(opt);
  });
  sel.dataset.populated = '1';
}

function renderMovieTable(movies, total) {
  const tbody  = document.getElementById('movies-tbody');
  const countEl = document.getElementById('movie-count');
  if (countEl) countEl.textContent = `${total} movies`;

  tbody.innerHTML = movies.map(m => `
    <tr>
      <td class="col-title">${esc(m.title)}</td>
      <td>${m.year}</td>
      <td>${genreBadge(m.genre)}</td>
      <td>${esc(m.director)}</td>
      <td class="col-cast" title="${esc(m.cast)}">${esc(m.cast)}</td>
      <td>${m.runtime}m</td>
      <td><span class="format-vhs">📼 VHS</span></td>
      <td class="${m.stock === 0 ? 'out-of-stock' : ''}">${m.stock === 0 ? 'Out' : m.stock}</td>
      <td class="mono">$${m.price.toFixed(2)}</td>
      <td>${starsHTML(m.rating)}</td>
      <td>
        <div class="action-btns">
          <button class="btn btn-sm btn-primary" onclick="addToCart(${m.id})"${m.stock === 0 ? ' disabled' : ''}>Add to Cart</button>
          <a class="btn btn-sm btn-ghost" href="/trailer.html?id=${m.id}" target="_blank">&#9654; Preview</a>
        </div>
      </td>
    </tr>
  `).join('');
}

async function addToCart(movieId) {
  try {
    const cart = await api('POST', '/api/cart', { movieId, qty: 1 });
    document.querySelectorAll('[data-cart-count]').forEach(el => {
      el.textContent = cart.itemCount;
      el.classList.toggle('hidden', cart.itemCount === 0);
    });
    toast('Added to cart!');
  } catch (err) {
    toast(err.message, 'error');
  }
}

/* ════════════════════════════════════════════════════════════════════════════
   CART
   ════════════════════════════════════════════════════════════════════════════ */
function initCart() {
  if (!requireAuth()) return;
  document.getElementById('logout-btn')?.addEventListener('click', logout);
  document.getElementById('checkout-btn')?.addEventListener('click', checkout);
  loadCart();
}

async function loadCart() {
  try {
    const cart = await api('GET', '/api/cart');
    renderCart(cart);
  } catch (err) {
    toast(err.message, 'error');
  }
}

function renderCart(cart) {
  const tbody      = document.getElementById('cart-tbody');
  const emptyMsg   = document.getElementById('cart-empty');
  const cartWrap   = document.getElementById('cart-wrap');
  const subtotalEl = document.getElementById('cart-subtotal');
  const taxEl      = document.getElementById('cart-tax');
  const totalEl    = document.getElementById('cart-total');

  const checkoutBtn = document.getElementById('checkout-btn');
  if (cart.items.length === 0) {
    emptyMsg?.classList.remove('hidden');
    cartWrap?.classList.add('hidden');
    if (checkoutBtn) checkoutBtn.disabled = true;
    return;
  }
  emptyMsg?.classList.add('hidden');
  cartWrap?.classList.remove('hidden');
  if (checkoutBtn) checkoutBtn.disabled = false;

  tbody.innerHTML = cart.items.map(item => `
    <tr>
      <td class="col-title">${esc(item.movie.title)}</td>
      <td>${item.movie.year}</td>
      <td>${genreBadge(item.movie.genre)}</td>
      <td class="mono">$${item.movie.price.toFixed(2)}</td>
      <td>${item.qty}</td>
      <td class="mono fw-700">$${(item.movie.price * item.qty).toFixed(2)}</td>
      <td><button class="btn btn-sm btn-danger" onclick="removeFromCart(${item.movieId})">Remove</button></td>
    </tr>
  `).join('');

  const tax = cart.subtotal * 0.13;
  const total = cart.subtotal + tax;
  if (subtotalEl) subtotalEl.textContent = `$${cart.subtotal.toFixed(2)}`;
  if (taxEl)      taxEl.textContent      = `$${tax.toFixed(2)}`;
  if (totalEl)    totalEl.textContent    = `$${total.toFixed(2)}`;
}

async function removeFromCart(movieId) {
  try {
    const cart = await api('DELETE', `/api/cart/${movieId}`);
    renderCart(cart);
    document.querySelectorAll('[data-cart-count]').forEach(el => {
      el.textContent = cart.itemCount;
      el.classList.toggle('hidden', cart.itemCount === 0);
    });
  } catch (err) {
    toast(err.message, 'error');
  }
}

async function checkout() {
  const btn = document.getElementById('checkout-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Processing…'; }
  try {
    const order = await api('POST', '/api/orders', {});
    // Open receipt in new tab — framework can then switch to it
    window.open(`/receipt.html?order=${order.orderId}`, '_blank');
    renderCart({ items: [], subtotal: 0, itemCount: 0 });
    document.querySelectorAll('[data-cart-count]').forEach(el => {
      el.textContent = '0';
      el.classList.add('hidden');
    });
    toast('Order placed! Receipt opened in new tab.');
  } catch (err) {
    toast(err.message, 'error');
    if (btn) { btn.disabled = false; btn.textContent = 'Checkout'; }
  }
}

/* ════════════════════════════════════════════════════════════════════════════
   RECEIPT
   ════════════════════════════════════════════════════════════════════════════ */
async function initReceipt() {
  const orderId = new URLSearchParams(location.search).get('order');
  if (!orderId) {
    document.getElementById('receipt-error')?.classList.remove('hidden');
    return;
  }
  try {
    const order = await fetch(`/api/orders/${orderId}`).then(r => r.json());
    renderReceipt(order);
  } catch {
    document.getElementById('receipt-error')?.classList.remove('hidden');
  }
}

function renderReceipt(order) {
  document.getElementById('receipt-order-id').textContent  = order.orderId;
  document.getElementById('receipt-date').textContent      = new Date(order.createdAt).toLocaleString('en-CA');
  document.getElementById('receipt-customer').textContent  = order.userId;

  const tbody = document.getElementById('receipt-tbody');
  tbody.innerHTML = order.items.map(item => `
    <tr>
      <td>${esc(item.movie.title)}</td>
      <td>${item.qty}</td>
      <td class="mono">$${item.movie.price.toFixed(2)}</td>
      <td class="mono fw-700">$${(item.movie.price * item.qty).toFixed(2)}</td>
    </tr>
  `).join('');

  document.getElementById('receipt-subtotal').textContent = `$${order.subtotal.toFixed(2)}`;
  document.getElementById('receipt-tax').textContent      = `$${order.tax.toFixed(2)}`;
  document.getElementById('receipt-total').textContent    = `$${order.total.toFixed(2)}`;
  document.getElementById('receipt-content')?.classList.remove('hidden');
}

/* ════════════════════════════════════════════════════════════════════════════
   TRAILER
   ════════════════════════════════════════════════════════════════════════════ */
async function initTrailer() {
  const movieId = parseInt(new URLSearchParams(location.search).get('id'));
  if (!movieId) return;
  try {
    const movie = await fetch(`/api/movies/${movieId}`).then(r => r.json());
    if (!movie || movie.error) throw new Error('Movie not found'); // 404 still resolves with a JSON body
    document.getElementById('trailer-title').textContent    = movie.title;
    document.getElementById('trailer-year').textContent     = movie.year;
    document.getElementById('trailer-director').textContent = movie.director;
    document.getElementById('trailer-cast').textContent     = movie.cast;
    document.getElementById('trailer-runtime').textContent  = `${movie.runtime} min`;
    document.getElementById('trailer-genre').innerHTML      = genreBadge(movie.genre);
    document.getElementById('trailer-rating').innerHTML     = starsHTML(movie.rating);
    document.getElementById('trailer-desc').textContent     = movie.description;
    document.title = `${movie.title} — BusterBlock.ca`;
  } catch {
    document.getElementById('trailer-title').textContent = 'Movie not found';
  }
}

/* ── Shared ──────────────────────────────────────────────────────────────── */
function logout() {
  clearToken();
  clearUser();
  window.location.href = '/';
}

function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

// Prevent XSS from movie data (defense in depth)
function esc(str) {
  return String(str).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]
  );
}
