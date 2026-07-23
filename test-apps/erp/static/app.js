/* BeanCounter ERP front-end: generic spreadsheet grid + dashboard charts. */

const $ = (sel) => document.querySelector(sel);
const fmtMoney = (n) => "$" + Number(n).toLocaleString("en-CA", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

async function fetchJSON(url, opts = {}) {
  if (opts.body) opts.headers = { "Content-Type": "application/json", ...opts.headers };
  const res = await fetch(url, opts);
  if (res.status === 401) { window.location = "/login"; throw new Error("unauthenticated"); }
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function hideSpinner() {
  const sp = $("#spinner");
  if (sp) sp.classList.add("hidden");
}

/* ---------- spreadsheet grid ----------
   cols: [{key, label, type: 'text'|'num'|'money', editable, total}] */
function renderGrid(table, cols, rows, hooks = {}) {
  let sortKey = null, sortDir = 1, filter = "";
  const state = { rows };

  function visibleRows() {
    let out = state.rows;
    if (filter) {
      const f = filter.toLowerCase();
      out = out.filter((r) => cols.some((c) => String(r[c.key] ?? "").toLowerCase().includes(f)));
    }
    if (sortKey) {
      const col = cols.find((c) => c.key === sortKey);
      out = [...out].sort((a, b) => {
        const av = a[sortKey], bv = b[sortKey];
        const cmp = col.type === "text" ? String(av).localeCompare(String(bv)) : av - bv;
        return cmp * sortDir;
      });
    }
    return out;
  }

  function cellText(col, row) {
    const v = row[col.key];
    return col.type === "money" ? fmtMoney(v) : String(v ?? "");
  }

  function draw() {
    const vis = visibleRows();
    const letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";
    let html = "<thead><tr class='col-letters'><th></th>" +
      cols.map((_, i) => `<th>${letters[i]}</th>`).join("") +
      (hooks.rowActions ? "<th></th>" : "") + "</tr><tr>" +
      "<th class='row-num'>#</th>" +
      cols.map((c) => {
        const arrow = sortKey === c.key ? (sortDir === 1 ? " ▲" : " ▼") : "";
        return `<th data-sort="${c.key}" data-testid="col-${c.key}" class="sortable">${c.label}${arrow}</th>`;
      }).join("") +
      (hooks.rowActions ? "<th>Actions</th>" : "") + "</tr></thead><tbody>";
    for (const row of vis) {
      html += `<tr data-id="${row.id}" data-testid="row-${row.id}">` +
        `<td class="row-num">${row.id}</td>` +
        cols.map((c) => {
          const cls = [c.type !== "text" ? "num" : "", c.editable ? "editable" : ""].join(" ").trim();
          const custom = hooks.cell && hooks.cell(c, row);
          return `<td data-key="${c.key}" data-testid="cell-${row.id}-${c.key}" class="${cls}">` +
            (custom ?? cellText(c, row)) + "</td>";
        }).join("") +
        (hooks.rowActions ? `<td class="actions">${hooks.rowActions(row)}</td>` : "") + "</tr>";
    }
    html += "</tbody>";
    const totals = cols.filter((c) => c.total);
    if (totals.length) {
      html += "<tfoot><tr><td class='row-num'>Σ</td>" + cols.map((c) => {
        if (!c.total) return "<td></td>";
        const sum = vis.reduce((s, r) => s + Number(r[c.key] || 0), 0);
        return `<td class="num" data-testid="total-${c.key}">${c.type === "money" ? fmtMoney(sum) : sum.toLocaleString()}</td>`;
      }).join("") + (hooks.rowActions ? "<td></td>" : "") + "</tr></tfoot>";
    }
    table.innerHTML = html;
    table.classList.remove("hidden");
    const rc = $("#row-count");
    if (rc) rc.textContent = `${vis.length} of ${state.rows.length} rows`;
  }

  table.addEventListener("click", (e) => {
    const th = e.target.closest("th[data-sort]");
    if (!th) return;
    const key = th.dataset.sort;
    sortDir = sortKey === key ? -sortDir : 1;
    sortKey = key;
    draw();
  });

  const filterBox = $("#grid-filter");
  if (filterBox) filterBox.addEventListener("input", () => { filter = filterBox.value; draw(); });

  if (hooks.onEdit) {
    table.addEventListener("dblclick", (e) => {
      const td = e.target.closest("td.editable");
      if (!td || td.isContentEditable) return;
      const col = cols.find((c) => c.key === td.dataset.key);
      const row = state.rows.find((r) => r.id === Number(td.closest("tr").dataset.id));
      td.textContent = row[col.key];  // raw value while editing
      td.contentEditable = "true";
      td.focus();
      document.getSelection().selectAllChildren(td);
      const finish = async (commit) => {
        td.contentEditable = "false";
        const val = td.textContent.trim();
        if (commit && val !== String(row[col.key])) {
          try {
            const updated = await hooks.onEdit(row, col.key, val);
            Object.assign(row, updated);
          } catch (err) {
            alert(err.message);
          }
        }
        draw();
      };
      td.addEventListener("blur", () => finish(true), { once: true });
      td.addEventListener("keydown", (e2) => {
        if (e2.key === "Enter") { e2.preventDefault(); td.blur(); }
        if (e2.key === "Escape") { td.removeEventListener("blur", finish); finish(false); }
      });
    });
  }

  draw();
  return { draw, state };
}

/* ---------- pages ---------- */

async function renderInventory() {
  const rows = await fetchJSON("/api/products");
  hideSpinner();
  $("#add-row-form").classList.remove("hidden");
  const cols = [
    { key: "sku", label: "SKU", type: "text" },
    { key: "name", label: "Product", type: "text", editable: true },
    { key: "category", label: "Category", type: "text", editable: true },
    { key: "qty", label: "Qty", type: "num", editable: true, total: true },
    { key: "unit_price", label: "Unit price", type: "money", editable: true },
    { key: "reorder_level", label: "Reorder lvl", type: "num", editable: true },
    { key: "supplier", label: "Supplier", type: "text", editable: true },
  ];
  const grid = renderGrid($("#grid"), cols, rows, {
    onEdit: (row, key, val) => fetchJSON(`/api/products/${row.id}`, { method: "PUT", body: JSON.stringify({ [key]: val }) }),
    rowActions: (row) => `<button class="btn-delete" data-del="${row.id}" data-testid="delete-${row.id}">Delete</button>`,
    cell: (col, row) => {
      if (col.key === "qty" && row.qty <= row.reorder_level)
        return `${row.qty} <span class="badge badge-low" data-testid="low-stock-${row.id}">LOW</span>`;
    },
  });
  $("#grid").addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-del]");
    if (!btn || !confirm("Delete this product?")) return;
    await fetchJSON(`/api/products/${btn.dataset.del}`, { method: "DELETE" });
    grid.state.rows = grid.state.rows.filter((r) => r.id !== Number(btn.dataset.del));
    grid.draw();
  });
  $("#add-row-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = Object.fromEntries(new FormData(e.target));
    const errBox = $("#add-row-error");
    errBox.textContent = "";
    try {
      const created = await fetchJSON("/api/products", { method: "POST", body: JSON.stringify(body) });
      grid.state.rows.push(created);
      grid.draw();
      e.target.reset();
    } catch (err) {
      errBox.textContent = err.message;
    }
  });
}

async function renderOrders() {
  const rows = await fetchJSON("/api/orders");
  hideSpinner();
  const statuses = ["pending", "shipped", "delivered", "cancelled"];
  const cols = [
    { key: "order_no", label: "Order #", type: "text" },
    { key: "customer", label: "Customer", type: "text" },
    { key: "product", label: "Product", type: "text" },
    { key: "qty", label: "Qty", type: "num", total: true },
    { key: "total", label: "Total", type: "money", total: true },
    { key: "order_date", label: "Date", type: "text" },
    { key: "status", label: "Status", type: "text" },
  ];
  renderGrid($("#grid"), cols, rows, {
    cell: (col, row) => {
      if (col.key !== "status") return;
      return `<select class="status-select status-${row.status}" data-status="${row.id}" data-testid="status-${row.id}">` +
        statuses.map((s) => `<option value="${s}" ${s === row.status ? "selected" : ""}>${s}</option>`).join("") +
        "</select>";
    },
  });
  $("#grid").addEventListener("change", async (e) => {
    const sel = e.target.closest("[data-status]");
    if (!sel) return;
    const updated = await fetchJSON(`/api/orders/${sel.dataset.status}`, { method: "PATCH", body: JSON.stringify({ status: sel.value }) });
    sel.className = `status-select status-${updated.status}`;
  });
}

async function renderEmployees() {
  const rows = await fetchJSON("/api/employees");
  hideSpinner();
  renderGrid($("#grid"), [
    { key: "name", label: "Name", type: "text" },
    { key: "dept", label: "Department", type: "text" },
    { key: "title", label: "Title", type: "text" },
    { key: "email", label: "Email", type: "text" },
    { key: "salary", label: "Salary", type: "money", total: true },
    { key: "hired", label: "Hired", type: "text" },
  ], rows);
}

/* ---------- dashboard charts (palette: validated reference set) ---------- */

const SERIES = ["#2a78d6", "#1baf7a", "#eda100", "#008300"]; // categorical slots 1-4
const INK = { muted: "#898781", grid: "#e1e0d9", primary: "#0b0b0b", surface: "#fcfcfb" };

function barChart(svg, data) {
  const W = 460, H = 260, m = { t: 16, r: 12, b: 44, l: 12 };
  const max = Math.max(...data.map((d) => d.value));
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const bw = Math.min(48, (iw / data.length) * 0.6);
  let out = "";
  for (let i = 1; i <= 4; i++) { // recessive gridlines
    const y = m.t + ih - (ih * i) / 4;
    out += `<line x1="${m.l}" x2="${W - m.r}" y1="${y}" y2="${y}" stroke="${INK.grid}" stroke-width="1"/>`;
  }
  data.forEach((d, i) => {
    const x = m.l + (iw / data.length) * (i + 0.5) - bw / 2;
    const h = Math.max(4, (d.value / max) * ih);
    const y = m.t + ih - h;
    // 4px rounded top, square base anchored to the baseline
    out += `<path class="bar" data-testid="bar-${d.label}" d="M${x},${y + h} V${y + 4} Q${x},${y} ${x + 4},${y} H${x + bw - 4} Q${x + bw},${y} ${x + bw},${y + 4} V${y + h} Z" fill="#2a78d6">` +
      `<title>${d.label}: ${fmtMoney(d.value)}</title></path>` +
      `<text x="${x + bw / 2}" y="${y - 5}" text-anchor="middle" class="chart-value">${fmtMoney(d.value)}</text>` +
      `<text x="${x + bw / 2}" y="${m.t + ih + 16}" text-anchor="middle" class="chart-label">${d.label}</text>`;
  });
  out += `<line x1="${m.l}" x2="${W - m.r}" y1="${m.t + ih}" y2="${m.t + ih}" stroke="#c3c2b7" stroke-width="1"/>`;
  svg.innerHTML = out;
}

function donutChart(svg, legendEl, data) {
  const cx = 100, cy = 100, r = 80, hole = 48;
  const total = data.reduce((s, d) => s + d.value, 0);
  let angle = -Math.PI / 2, out = "";
  data.forEach((d, i) => {
    const frac = d.value / total;
    const a2 = angle + frac * 2 * Math.PI;
    const large = frac > 0.5 ? 1 : 0;
    const p = (a, rad) => `${cx + rad * Math.cos(a)},${cy + rad * Math.sin(a)}`;
    out += `<path data-testid="slice-${d.label}" fill="${SERIES[i % SERIES.length]}" stroke="${INK.surface}" stroke-width="2" ` +
      `d="M${p(angle, r)} A${r},${r} 0 ${large} 1 ${p(a2, r)} L${p(a2, hole)} A${hole},${hole} 0 ${large} 0 ${p(angle, hole)} Z">` +
      `<title>${d.label}: ${d.value} orders</title></path>`;
    angle = a2;
  });
  out += `<text x="${cx}" y="${cy - 2}" text-anchor="middle" class="donut-total" data-testid="donut-total">${total}</text>` +
    `<text x="${cx}" y="${cy + 16}" text-anchor="middle" class="chart-label">orders</text>`;
  svg.innerHTML = out;
  legendEl.innerHTML = data.map((d, i) =>
    `<li data-testid="legend-${d.label}"><span class="swatch" style="background:${SERIES[i % SERIES.length]}"></span>${d.label} <b>${d.value}</b></li>`).join("");
}

async function renderDashboard() {
  const stats = await fetchJSON("/api/stats");
  hideSpinner();
  $("#dash").classList.remove("hidden");
  $("#kpi-revenue").textContent = fmtMoney(stats.kpis.revenue);
  $("#kpi-open-orders").textContent = stats.kpis.open_orders;
  $("#kpi-low-stock").textContent = stats.kpis.low_stock;
  $("#kpi-headcount").textContent = stats.kpis.headcount;
  barChart($("#bar-chart"), stats.revenue_by_category);
  donutChart($("#donut-chart"), $("#donut-legend"), stats.orders_by_status);
}
