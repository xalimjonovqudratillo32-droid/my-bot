/* TradeX Mini App вЂ” Trading Simulator. Autentifikatsiya: Telegram initData в†’ JWT.
   Brauzerda test uchun: ?dev=TELEGRAM_ID */
(() => {
  "use strict";

  /* ---------- Yordamchilar ---------- */
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const num = (n) => (n === null || n === undefined || n === "" || isNaN(Number(n)) ? null : Number(n));
  const fmt = (n, d = 2) => (num(n) === null ? "вЂ”" : Number(n).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }));
  const usd = (n, d = 2) => (num(n) === null ? "вЂ”" : (Number(n) < 0 ? "-$" : "$") + fmt(Math.abs(Number(n)), d));
  const pxFmt = (n) => (num(n) === null ? "вЂ”" : usd(n, Number(n) < 1 ? 4 : 2));
  const pct = (n) => (num(n) === null ? "вЂ”" : `${Number(n) >= 0 ? "+" : ""}${fmt(n)}%`);
  const cls = (n) => (Number(n) >= 0 ? "up" : "down");
  const ts = (s) => new Date(String(s).endsWith("Z") ? s : s + "Z");
  const ic = (id, c = "") => `<svg class="ic ${c}"><use href="#i-${id}"/></svg>`;
  const initials = (name) => (name || "T").trim().split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
  const COIN = { BTC: ["#f7931a", "в‚ї"], ETH: ["#627eea", "Оћ"], SOL: ["#9945ff", "в—Ћ"], BNB: ["#f3ba2f", "B"],
    XRP: ["#9aa6b8", "вњ•"], DOGE: ["#c2a633", "Гђ"], ADA: ["#3b82f6", "в‚і"] };
  const coinColor = (sym) => (COIN[sym] || ["#8b5cf6"])[0];
  const coinIc = (sym, big = false) => {
    const [c, g] = COIN[sym] || ["#8b5cf6", sym[0]];
    return `<span class="coin-ic${big ? " big" : ""}" style="background:${c}">${g}</span>`;
  };
  const STATUS = { open: "Ochiq", filled: "Bajarilgan", cancelled: "Bekor" };

  const S = {
    token: null, me: null, prices: null, txs: [], orders: [], notifs: [], missions: null, lb: [], refs: null,
    premium: null, analytics: null,
    view: "dashboard", sym: "BTC", tf: 900, ofilter: "", chart: null, candles: null,
    spark: {}, sparkAt: 0, favs: [], trade: { side: "buy", type: "market" },
  };

  /* ---------- Toast ---------- */
  function toast(msg, kind = "ok") {
    const el = document.createElement("div");
    el.className = `toast ${kind}`;
    el.textContent = msg;
    $("#toasts").appendChild(el);
    setTimeout(() => { el.style.transition = "opacity .3s"; el.style.opacity = "0"; setTimeout(() => el.remove(), 300); }, 3000);
  }

  /* ---------- API ---------- */
  async function api(path, opts = {}) {
    const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
    if (S.token) headers.Authorization = `Bearer ${S.token}`;
    const res = await fetch(`/api${path}`, { ...opts, headers, body: opts.body ? JSON.stringify(opts.body) : undefined });
    let data = null;
    try { data = await res.json(); } catch (_) { /* bo'sh javob */ }
    if (!res.ok) throw new Error((data && data.detail) || `Xato (${res.status})`);
    return data;
  }

  async function login() {
    const tg = window.Telegram && window.Telegram.WebApp;
    const q = new URLSearchParams(location.search);
    const headers = {};
    if (tg && tg.initData) {
      tg.ready(); tg.expand();
      headers["X-Telegram-Init-Data"] = tg.initData;
    } else if (q.get("dev")) {
      headers["X-Dev-User"] = q.get("dev");
    } else {
      throw new Error("Telegram ichida oching yoki ?dev=TELEGRAM_ID qo'shing");
    }
    const d = await api("/auth/telegram", { method: "POST", headers });
    S.token = d.token;
    if (d.created) toast("Xush kelibsiz! Virtual balans berildi рџЋ‰");
  }

  /* ---------- Ma'lumot yuklash ---------- */
  async function refreshAll() {
    const [me, prices, txs, orders, notifs, missions, lb, refs, premium] = await Promise.all([
      api("/me"), api("/market/prices"), api("/trade/transactions?limit=200"), api("/trade/orders"),
      api("/notifications"), api("/missions"), api("/leaderboard"), api("/referrals"), api("/premium"),
    ]);
    const analytics = premium.is_premium ? await api("/premium/analytics").catch(() => null) : null;
    Object.assign(S, { me, prices, txs, orders, notifs, missions, lb, refs, premium, analytics });
    S.favs = me.user.favorites || [];
    renderAll();
    await ensureSparklines(false);
    await loadChart();
  }

  async function ensureSparklines(force) {
    if (!force && Date.now() - S.sparkAt < 300000) return;
    if (!S.prices) return;
    const results = await Promise.allSettled(S.prices.coins.map((c) => api(`/market/${c.symbol}/chart`)));
    results.forEach((r, i) => { if (r.status === "fulfilled") S.spark[S.prices.coins[i].symbol] = r.value.points; });
    S.sparkAt = Date.now();
    renderTickers();
  }

  const priceOf = (sym) => {
    const c = S.prices && S.prices.coins.find((x) => x.symbol === sym);
    return c ? num(c.price) : null;
  };

  /* ---------- Sparkline & donut (SVG) ---------- */
  function spark(vals, color) {
    if (!vals || vals.length < 2) return "";
    const mn = Math.min(...vals), mx = Math.max(...vals), span = mx - mn || 1;
    const pts = vals.map((v, i) => `${(i / (vals.length - 1)) * 100},${27 - ((v - mn) / span) * 24}`).join(" ");
    return `<svg class="spark" viewBox="0 0 100 28" preserveAspectRatio="none"><polyline fill="none" stroke="${color}" stroke-width="2" vector-effect="non-scaling-stroke" stroke-linejoin="round" points="${pts}"/></svg>`;
  }

  function donut(parts, total) {
    const R = 52, C = 2 * Math.PI * R;
    let acc = 0;
    const segs = total > 0 ? parts.filter((p) => p.v > 0).map((p) => {
      const f = p.v / total;
      const s = `<circle r="${R}" cx="70" cy="70" fill="none" stroke="${p.color}" stroke-width="16" stroke-dasharray="${Math.max(0, f * C - 2)} ${C}" stroke-dashoffset="${-acc * C}"/>`;
      acc += f;
      return s;
    }).join("") : "";
    return `<svg viewBox="0 0 140 140" class="donut"><g transform="rotate(-90 70 70)">
      <circle r="${R}" cx="70" cy="70" fill="none" stroke="rgba(255,255,255,.06)" stroke-width="16"/>${segs}</g></svg>`;
  }

  /* ---------- Render: topbar ---------- */
  function renderTop() {
    const u = S.me.user;
    const p = u.next_level_xp ? ((u.xp - u.level_floor_xp) / (u.next_level_xp - u.level_floor_xp)) * 100 : 100;
    $("#lvl").innerHTML = `${ic("star")}<div class="lvl-b"><div class="lvl-t"><b>Level ${u.level} В· ${esc(u.name)}</b>
      <span>${u.next_level_xp ? `${u.xp} / ${u.next_level_xp} XP` : `${u.xp} XP`}</span></div>
      <div class="bar"><i style="width:${Math.min(100, Math.max(0, p))}%"></i></div></div>`;
    $("#streak").innerHTML = `${ic("flame")}<b>${u.streak} Day Streak</b>`;
    $("#avatar span").textContent = initials(u.first_name);
    $("#welcome").textContent = `Welcome back, ${u.first_name || "Trader"}!`;
    const unread = S.notifs.filter((n) => !n.is_read).length;
    $("#bellDot").hidden = !unread;
    $("#bellDot").textContent = unread > 9 ? "9+" : unread;
    $("#proTag").hidden = !u.is_premium;
    $("#promoBonus").textContent = usd(S.refs.bonus_per_referral, 0);
    $("#invBonus").textContent = usd(S.refs.bonus_per_referral, 0);
  }

  /* ---------- Balans kartasi ---------- */
  function renderBalance() {
    const p = S.me.portfolio;
    const equity = Number(p.equity), totalPnl = Number(p.total_pnl);
    const start = equity - totalPnl;
    const pctVal = start ? (totalPnl / start) * 100 : 0;
    $("#balValue").textContent = usd(equity);
    $("#balChip").className = `chip ${cls(totalPnl)}`;
    $("#balChip").textContent = `${totalPnl >= 0 ? "в–І" : "в–ј"} ${pct(pctVal)}`;
    const series = [start, ...S.txs.slice().reverse().map((t) => Number(t.balance_after)), Number(p.cash)];
    $("#balSpark").innerHTML = spark(series, totalPnl >= 0 ? "#22e07a" : "#ff4d6d");
  }

  /* ---------- Coin kartalari ---------- */
  function renderTickers() {
    if (!S.prices) return;
    $("#tickers").innerHTML = S.prices.coins.map((c) => {
      const ch = num(c.change_24h);
      const up = ch === null || ch >= 0;
      const points = (S.spark[c.symbol] || []).map((p) => p.value);
      return `<div class="tk ${c.symbol === S.sym ? "on" : ""}" data-sym="${c.symbol}" role="button" tabindex="0">
        <div class="tk-top">${coinIc(c.symbol)}<div><b>${c.symbol}</b><small>${esc(c.name)}</small></div></div>
        <div class="tk-mid"><div class="tk-px">${pxFmt(c.price)}</div>
          ${ch === null ? "" : `<span class="chip ${up ? "up" : "down"}">${up ? "в–І" : "в–ј"} ${fmt(Math.abs(ch))}%</span>`}</div>
        ${spark(points, up ? "#22e07a" : "#ff4d6d")}
      </div>`;
    }).join("");
  }

  /* ---------- Grafik ---------- */
  function ensureChart() {
    if (S.chart || !window.LightweightCharts) return;
    const el = $("#chart");
    S.chart = LightweightCharts.createChart(el, {
      width: el.clientWidth || 600, height: 280,
      layout: { background: { color: "transparent" }, textColor: "#7b86a3" },
      grid: { vertLines: { color: "rgba(255,255,255,0.03)" }, horzLines: { color: "rgba(255,255,255,0.04)" } },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false, timeVisible: true },
    });
    S.candles = S.chart.addCandlestickSeries({
      upColor: "#22e07a", downColor: "#ff4d6d", borderVisible: false,
      wickUpColor: "#22e07a", wickDownColor: "#ff4d6d",
    });
    new ResizeObserver(() => { if (el.clientWidth) S.chart.applyOptions({ width: el.clientWidth }); }).observe(el);
  }

  // 24 soatlik nuqtalardan OHLC shamlar yasaymiz (haqiqiy ma'lumotdan hosila)
  function toCandles(points, bucket) {
    const map = new Map();
    for (const p of points) {
      const t = Math.floor(p.time / bucket) * bucket;
      const c = map.get(t);
      if (!c) map.set(t, { time: t, open: p.value, high: p.value, low: p.value, close: p.value });
      else { c.high = Math.max(c.high, p.value); c.low = Math.min(c.low, p.value); c.close = p.value; }
    }
    return [...map.values()].sort((a, b) => a.time - b.time);
  }

  async function loadChart() {
    ensureChart();
    if (!S.chart) return;
    const sym = S.sym;
    try {
      const data = await api(`/market/${sym}/chart`);
      if (sym !== S.sym) return;
      S.candles.setData(toCandles(data.points, S.tf));
      S.chart.timeScale().fitContent();
    } catch (e) { toast(e.message, "err"); }
  }

  function renderChartHead() {
    const c = S.prices.coins.find((x) => x.symbol === S.sym);
    if (!c) return;
    const ch = num(c.change_24h);
    $("#cIc").innerHTML = coinIc(S.sym, true);
    $("#cName").textContent = `${c.name} (${S.sym})`;
    $("#cSym").textContent = `Virtual savdo В· ${S.sym}/USD`;
    $("#cPx").textContent = pxFmt(c.price);
    $("#cChg").className = `chip ${ch === null || ch >= 0 ? "up" : "down"}`;
    $("#cChg").textContent = ch === null ? "вЂ”" : `${ch >= 0 ? "в–І" : "в–ј"} ${pct(ch)}`;
    $("#cFav").classList.toggle("on", S.favs.includes(S.sym));
  }

  /* ---------- Savdo paneli ---------- */
  function syncTrade() {
    const t = S.trade, p = S.me.portfolio, px = priceOf(S.sym);
    const isLimit = t.type === "limit";
    $$("#typeSeg button").forEach((b) => b.classList.toggle("on", b.dataset.type === t.type));
    $$("#sideSeg button").forEach((b) => b.classList.toggle("on", b.dataset.side === t.side));
    $("#sideSeg .buy").className = `buy ${t.side === "buy" ? "on" : ""}`;
    $("#sideSeg .sell").className = `sell ${t.side === "sell" ? "on" : ""}`;
    $("#limitWrap").hidden = !isLimit;
    const amt = num($("#amount").value) || 0;
    const price = isLimit ? (num($("#limitPx").value) || px) : px;
    const qty = price ? amt / price : 0;
    $("#recvIc").innerHTML = coinIc(S.sym);
    $("#recvQty").textContent = qty ? fmt(qty, 6) : "0";
    $("#recvSym").textContent = S.sym;
    const submit = $("#submit");
    submit.className = `btn-go ${t.side === "sell" ? "sell" : ""}`;
    submit.textContent = `${isLimit ? "Limit " : ""}${t.side === "buy" ? "Buy" : "Sell"} ${S.sym}`;
    const held = (p.positions.find((x) => x.symbol === S.sym) || {}).quantity || 0;
    let avail = t.side === "buy" ? `Mavjud: ${usd(p.cash)}` : `Mavjud: ${fmt(held, 6)} ${S.sym}`;
    if (S.prices.stale) avail += " В· Narx yangilanmagan, savdo cheklangan";
    $("#avail").textContent = avail;
  }

  function applyPct(percent) {
    const p = S.me.portfolio, px = priceOf(S.sym);
    let base = 0;
    if (S.trade.side === "buy") base = Number(p.cash);
    else {
      const held = (p.positions.find((x) => x.symbol === S.sym) || {}).quantity || 0;
      base = px ? held * px : 0;
    }
    $("#amount").value = (base * percent / 100).toFixed(2);
    syncTrade();
  }

  async function submitTrade() {
    const t = S.trade, amt = num($("#amount").value);
    if (!amt || amt <= 0) return toast("Summani kiriting", "err");
    const isLimit = t.type === "limit";
    const price = isLimit ? num($("#limitPx").value) : priceOf(S.sym);
    if (!price || price <= 0) return toast(isLimit ? "Limit narxni kiriting" : "Narx mavjud emas", "err");
    const qty = Number((amt / price).toFixed(8));
    if (!(qty > 0)) return toast("Miqdor juda kichik", "err");
    const body = { symbol: S.sym, side: t.side, quantity: qty };
    let path = "/trade/market";
    if (isLimit) { body.limit_price = price; path = "/trade/limit"; }
    const btn = $("#submit");
    btn.disabled = true;
    try {
      const key = (crypto.randomUUID && crypto.randomUUID()) || `${Date.now()}-${Math.random()}`;
      await api(path, { method: "POST", body, headers: { "Idempotency-Key": key } });
      toast(isLimit ? "Limit buyurtma qo'yildi" : `${S.sym} ${t.side === "buy" ? "sotib olindi" : "sotildi"} вњ“`);
      $("#amount").value = "";
      await refreshAll();
    } catch (e) { toast(e.message, "err"); }
    finally { btn.disabled = false; }
  }

  function selectSym(sym) {
    if (!sym || sym === S.sym) return;
    S.sym = sym;
    renderTickers();
    renderChartHead();
    syncTrade();
    loadChart();
  }

  /* ---------- Portfel ---------- */
  function renderPortfolio() {
    const p = S.me.portfolio, eq = Number(p.equity), totalPnl = Number(p.total_pnl);
    const start = eq - totalPnl;
    const parts = [{ v: Number(p.cash), color: "#22e07a", label: "Naqd (USD)" },
      ...p.positions.map((x) => ({ v: Number(x.market_value), color: coinColor(x.symbol), label: x.symbol }))];
    $("#donut").innerHTML = donut(parts, eq);
    $("#portV").textContent = usd(eq);
    $("#portChg").className = `chip ${cls(totalPnl)}`;
    $("#portChg").textContent = `${totalPnl >= 0 ? "в–І" : "в–ј"} ${pct(start ? (totalPnl / start) * 100 : 0)}`;
    $("#legend").innerHTML = parts.map((x) => `<div class="lg"><span class="lgd" style="background:${x.color}"></span>
      <span>${esc(x.label)}</span><b>${eq ? ((x.v / eq) * 100).toFixed(0) : 0}%</b></div>`).join("");
  }

  function renderTrades() {
    const limit = S.view === "portfolio" ? 30 : 6;
    const rows = S.txs.slice(0, limit);
    $("#trades").innerHTML = rows.length ? rows.map((t) => `<tr>
      <td>${ts(t.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</td>
      <td><span class="asset">${coinIc(t.symbol)} ${t.symbol}</span></td>
      <td class="${t.side === "buy" ? "up" : "down"}"><b>${t.side === "buy" ? "Buy" : "Sell"}</b></td>
      <td class="r">${usd(t.total)}</td>
      <td class="r ${t.side === "sell" ? cls(t.realized_pnl) : ""}">${t.side === "sell" ? usd(t.realized_pnl) : "вЂ”"}</td>
    </tr>`).join("") : `<tr><td colspan="5" class="empty">Hali savdo yo'q</td></tr>`;
  }

  function renderOrders() {
    const list = S.orders.filter((o) => !S.ofilter || o.status === S.ofilter).slice(0, 40);
    $("#orders").innerHTML = list.length ? list.map((o) => `<div class="ord">
      ${coinIc(o.symbol)}
      <div class="ord-b"><b class="${o.side === "buy" ? "up" : "down"}">${o.side.toUpperCase()}</b> ${o.symbol} В· ${o.type === "limit" ? "Limit" : "Market"}
        <small>${fmt(o.quantity, 6)}${o.limit_price ? ` @ ${pxFmt(o.limit_price)}` : ""} В· ${ts(o.created_at).toLocaleString()}${o.cancel_reason ? ` В· ${esc(o.cancel_reason)}` : ""}</small></div>
      <div class="ord-r"><span class="chip ${o.status === "filled" ? "up" : o.status === "open" ? "muted-chip" : "down"}">${STATUS[o.status] || o.status}</span>
        ${o.status === "open" ? `<button class="btn-ghost sm" data-cancel="${o.id}">Bekor</button>` : ""}</div>
    </div>`).join("") : `<div class="empty">Buyurtma yo'q</div>`;
  }

  /* ---------- Missiyalar ---------- */
  function countdown() {
    const now = new Date();
    const next = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + 1);
    const ms = next - now.getTime();
    return `${Math.floor(ms / 3600000)}h ${String(Math.floor((ms % 3600000) / 60000)).padStart(2, "0")}m`;
  }

  function renderMissions() {
    const m = S.missions;
    const daily = m.missions.filter((x) => x.period === "daily");
    const weekly = m.missions.filter((x) => x.period !== "daily");
    const row = (x) => {
      const p = Math.min(100, (x.progress / x.target) * 100);
      const action = x.claimed ? `<span class="chip muted-chip">Olingan</span>`
        : x.completed ? `<button class="btn-go sm" data-claim="${x.id}">+${x.xp} XP</button>`
        : `<span class="xp-tag">+${x.xp} XP</span>`;
      return `<div class="mis">
        <div class="mis-ic ${x.completed ? "done" : ""}">${ic(x.completed ? "check" : "mission")}</div>
        <div class="mis-b"><b>${esc(x.title)}</b><div class="bar"><i style="width:${p}%"></i></div>
          <small>${x.progress}/${x.target} В· ${x.period === "daily" ? "Kunlik" : "Haftalik"}</small></div>
        <div>${action}</div></div>`;
    };
    const isMissionsView = S.view === "missions" || S.view === "achievements";
    $("#missions").innerHTML = daily.map(row).join("") +
      (isMissionsView && weekly.length ? `<div class="sub">Haftalik</div>` + weekly.map(row).join("") : "");
    $("#ach").innerHTML = m.achievements.map((a) => `<div class="ach ${a.unlocked ? "on" : ""}">
      <div class="ach-ic">${ic(a.unlocked ? "star" : "lock")}</div><b>${esc(a.title)}</b><small>+${a.xp} XP</small></div>`).join("");
    const claimable = m.missions.filter((x) => x.completed && !x.claimed).length;
    $("#missionBadge").hidden = !claimable;
    $("#missionBadge").textContent = claimable;
    $("#timer").textContent = countdown();
  }

  /* ---------- Reyting & statistika ---------- */
  function renderLeaderboard() {
    const n = S.view === "dashboard" ? 5 : 20;
    $("#lb").innerHTML = S.lb.slice(0, n).map((r) => `<div class="lb ${r.is_me ? "me" : ""}">
      <span class="rk ${r.rank <= 3 ? "top" : ""}">${r.rank}</span>
      <span class="av">${esc(initials(r.name))}</span>
      <div class="lb-b"><b>${esc(r.name)}${r.is_me ? " (Siz)" : ""}</b><small>${Number(r.xp).toLocaleString()} XP В· Lv.${r.level}</small></div>
      <div class="lb-r">${usd(r.equity, 0)}</div></div>`).join("") || `<div class="empty">Reyting bo'sh</div>`;
  }

  function renderStats() {
    const p = S.me.portfolio, u = S.me.user;
    const sells = S.txs.filter((t) => t.side === "sell");
    const wins = sells.filter((t) => Number(t.realized_pnl) > 0).length;
    $("#stP").innerHTML = `<span class="${cls(p.total_pnl)}">${usd(p.total_pnl)}</span>`;
    $("#stW").textContent = sells.length ? `${Math.round((wins / sells.length) * 100)}%` : "вЂ”";
    $("#stT").textContent = S.txs.length >= 200 ? "200+" : String(S.txs.length);
    $("#stLvl").textContent = u.level;
    const lp = u.next_level_xp ? ((u.xp - u.level_floor_xp) / (u.next_level_xp - u.level_floor_xp)) * 100 : 100;
    $("#stBar").style.width = `${Math.min(100, Math.max(0, lp))}%`;
    $("#stXp").textContent = u.next_level_xp ? `${u.xp} / ${u.next_level_xp} XP` : `${u.xp} XP (maksimal daraja)`;
  }

  /* ---------- Referral ---------- */
  function renderReferral() {
    const r = S.refs;
    $("#refLink").textContent = r.link;
    $("#refCount").textContent = r.invited_count;
    $("#refBonus").textContent = usd(r.total_bonus, 0);
    $("#refHist").innerHTML = r.history.length ? r.history.map((h) => `<div class="ord">
      <span class="av">${esc(initials(h.name))}</span>
      <div class="ord-b"><b>${esc(h.name || "Foydalanuvchi")}</b><small>${ts(h.joined_at).toLocaleDateString()}</small></div>
      <div class="ord-r up">+${usd(h.bonus, 0)}</div></div>`).join("")
      : `<div class="empty">Hali taklif yo'q. Havolani do'stlaringizga yuboring!</div>`;
    $("#refTop").innerHTML = r.leaderboard.map((t) => `<div class="lb">
      <span class="rk">${t.rank}</span><span class="av">${esc(initials(t.name))}</span>
      <div class="lb-b"><b>${esc(t.name)}</b></div><div class="lb-r">${t.invited}</div></div>`).join("")
      || `<div class="empty">вЂ”</div>`;
  }

  /* ---------- Premium ---------- */
  function renderPremium() {
    const pr = S.premium;
    if (!pr) return;
    $("#premSub").textContent = pr.is_premium
      ? `Faol В· ${ts(pr.premium_until).toLocaleDateString()} gacha`
      : "Hozir faol emas";
    $("#premChip").className = `chip ${pr.is_premium ? "up" : "muted-chip"}`;
    $("#premChip").textContent = pr.is_premium ? "в­ђ Faol" : "Faol emas";
    $("#premPerks").innerHTML = pr.perks.map((p) => `<div class="perk">вњ“ ${esc(p)}</div>`).join("");
    const body = $("#premBody");
    if (!pr.is_premium) {
      body.innerHTML = `<div class="locked">${ic("lock")}<b>Kengaytirilgan statistika Premium uchun</b>
        <small>Premium hozircha administrator tomonidan beriladi. To'lov tizimi keyinroq qo'shiladi.</small></div>`;
      return;
    }
    const a = S.analytics;
    if (!a) { body.innerHTML = `<div class="empty">Ma'lumot yuklanmoqdaвЂ¦</div>`; return; }
    const best = a.best_trade ? usd(a.best_trade.realized_pnl) : "вЂ”";
    const worst = a.worst_trade ? usd(a.worst_trade.realized_pnl) : "вЂ”";
    body.innerHTML = `<div class="an-grid">
      <div class="an"><span class="muted small">Realized P/L</span><b class="${cls(a.realized_pnl)}">${usd(a.realized_pnl)}</b></div>
      <div class="an"><span class="muted small">Win rate</span><b>${a.win_rate === null ? "вЂ”" : a.win_rate + "%"}</b></div>
      <div class="an"><span class="muted small">Savdolar</span><b>${a.trades}</b></div>
      <div class="an"><span class="muted small">Jami hajm</span><b>${usd(a.total_volume, 0)}</b></div>
      <div class="an"><span class="muted small">O'rtacha savdo</span><b>${usd(a.avg_trade_size, 0)}</b></div>
      <div class="an"><span class="muted small">Eng yaxshi savdo</span><b class="up">${best}</b></div>
      <div class="an"><span class="muted small">Eng yomon savdo</span><b class="down">${worst}</b></div>
    </div>
    <h3 class="mt">Coin bo'yicha</h3>
    <div class="tbl-wrap"><table class="tbl"><thead><tr><th>Coin</th><th class="r">Savdo</th><th class="r">Hajm</th><th class="r">Realized P/L</th></tr></thead><tbody>
      ${a.by_symbol.map((r) => `<tr><td><span class="asset">${coinIc(r.symbol)} ${r.symbol}</span></td>
        <td class="r">${r.trades}</td><td class="r">${usd(r.volume, 0)}</td>
        <td class="r ${cls(r.realized_pnl)}">${usd(r.realized_pnl)}</td></tr>`).join("")
        || `<tr><td colspan="4" class="empty">Ma'lumot yo'q</td></tr>`}
    </tbody></table></div>`;
  }

  /* ---------- Render hammasi ---------- */
  function renderAll() {
    if (!S.me || !S.prices || !S.missions) return;
    renderTop();
    renderBalance();
    renderTickers();
    renderChartHead();
    syncTrade();
    renderPortfolio();
    renderTrades();
    renderOrders();
    renderMissions();
    renderLeaderboard();
    renderStats();
    renderReferral();
    renderPremium();
  }

  /* ---------- Ko'rinishlar (sidebar bo'limlari) ---------- */
  function setView(v) {
    S.view = v;
    $("#main").dataset.view = v;
    $$("#main > [data-in]").forEach((el) => { el.hidden = !el.dataset.in.split(" ").includes(v); });
    $$(".menu [data-view]").forEach((b) => b.classList.toggle("on", b.dataset.view === v));
    $$(".tabbar [data-view]").forEach((b) => b.classList.toggle("on", b.dataset.view === v));
    renderAll();
    requestAnimationFrame(() => {
      if (S.chart) { const el = $("#chart"); if (el.clientWidth) S.chart.applyOptions({ width: el.clientWidth }); }
    });
  }

  /* ---------- Modal ---------- */
  function openModal(title, html) {
    $("#mTitle").textContent = title;
    $("#mBody").innerHTML = html;
    $("#modal").hidden = false;
  }
  const closeModal = () => { $("#modal").hidden = true; };

  async function openNotifs() {
    const list = S.notifs.length
      ? S.notifs.map((n) => `<div class="ntf ${n.is_read ? "" : "new"}"><p>${esc(n.message)}</p><small>${ts(n.created_at).toLocaleString()}</small></div>`).join("")
      : `<div class="empty">Bildirishnoma yo'q</div>`;
    openModal("Bildirishnomalar", list);
    if (S.notifs.some((n) => !n.is_read)) {
      try {
        await api("/notifications/read", { method: "POST" });
        S.notifs = await api("/notifications");
        renderTop();
      } catch (_) { /* jim */ }
    }
  }

  function openHelp() {
    openModal("TradeX yordam", `<ul class="help-list">
      <li><b>/start</b> вЂ” ro'yxatdan o'tish va Mini App</li>
      <li><b>/balance</b> вЂ” balans va umumiy P/L</li>
      <li><b>/portfolio</b> вЂ” ochiq pozitsiyalar</li>
      <li><b>/leaderboard</b> вЂ” reyting jadvali</li>
      <li><b>/invite</b> вЂ” do'st taklif qilish havolasi</li>
      <li><b>/daily</b> вЂ” kunlik sovg'a</li></ul>
      <p class="small" style="margin-top:14px">Barcha savdolar virtual. Haqiqiy pul yoki kripto ishtirok etmaydi.</p>`);
  }

  /* ---------- Harakatlar ---------- */
  async function toggleFav(sym) {
    try {
      const r = await api(`/favorites/${sym}`, { method: "POST" });
      S.favs = r.favorites;
      renderChartHead();
    } catch (e) { toast(e.message, "err"); }
  }

  async function claimMission(id) {
    try {
      const r = await api(`/missions/${id}/claim`, { method: "POST" });
      toast(`+${r.xp_awarded} XP olindi рџЋ‰`);
      await refreshAll();
    } catch (e) { toast(e.message, "err"); }
  }

  async function cancelOrder(id) {
    try {
      await api(`/trade/orders/${id}/cancel`, { method: "POST" });
      toast("Buyurtma bekor qilindi");
      await refreshAll();
    } catch (e) { toast(e.message, "err"); }
  }

  async function dailyReward() {
    try {
      const r = await api("/daily-reward", { method: "POST" });
      toast(`+${usd(r.balance_bonus, 0)} va +${r.xp_awarded} XP рџЋЃ`);
      await refreshAll();
    } catch (e) { toast(e.message, "err"); }
  }

  async function copyLink() {
    const link = S.refs && S.refs.link;
    if (!link) return;
    try { await navigator.clipboard.writeText(link); toast("Havola nusxalandi"); }
    catch (_) { toast("Nusxalab bo'lmadi", "err"); }
  }

  function shareLink() {
    const link = S.refs && S.refs.link;
    if (!link) return;
    const tg = window.Telegram && window.Telegram.WebApp;
    const url = `https://t.me/share/url?url=${encodeURIComponent(link)}&text=${encodeURIComponent("TradeX'da birga savdo qilamiz! рџ“€")}`;
    if (tg && tg.openTelegramLink) tg.openTelegramLink(url); else window.open(url, "_blank");
  }

  /* ---------- Hodisalar (delegation) ---------- */
  function bindEvents() {
    document.addEventListener("click", (e) => {
      const t = e.target;
      const view = t.closest("[data-view]");
      if (view && !view.disabled) return setView(view.dataset.view);
      if (t.closest("#helpBtn")) return openHelp();
      if (t.closest("#bell")) return openNotifs();
      if (t.closest("[data-close]") || t.id === "modal") return closeModal();
      const tk = t.closest("[data-sym]");
      if (tk) return selectSym(tk.dataset.sym);
      const tf = t.closest("[data-tf]");
      if (tf) {
        S.tf = Number(tf.dataset.tf);
        $$("#tf button").forEach((b) => b.classList.toggle("on", b === tf));
        return loadChart();
      }
      const ty = t.closest("[data-type]");
      if (ty) { S.trade.type = ty.dataset.type; return syncTrade(); }
      const sd = t.closest("[data-side]");
      if (sd) { S.trade.side = sd.dataset.side; return syncTrade(); }
      const pc = t.closest("[data-pct]");
      if (pc) return applyPct(Number(pc.dataset.pct));
      const of = t.closest("[data-of]");
      if (of) {
        S.ofilter = of.dataset.of;
        $$("#ofilter button").forEach((b) => b.classList.toggle("on", b === of));
        return renderOrders();
      }
      if (t.closest("#submit")) return submitTrade();
      if (t.closest("#cFav")) return toggleFav(S.sym);
      if (t.closest("[data-copy]")) return copyLink();
      if (t.closest("[data-share]")) return shareLink();
      const cl = t.closest("[data-claim]");
      if (cl) return claimMission(cl.dataset.claim);
      const ca = t.closest("[data-cancel]");
      if (ca) return cancelOrder(ca.dataset.cancel);
      if (t.closest("#dailyBtn")) return dailyReward();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && e.target.classList && e.target.classList.contains("tk")) selectSym(e.target.dataset.sym);
      if (e.key === "Escape") closeModal();
    });
    $("#amount").addEventListener("input", syncTrade);
    $("#limitPx").addEventListener("input", syncTrade);
  }

  /* ---------- Boot ---------- */
  async function boot() {
    bindEvents();
    setView("dashboard");
    try {
      await login();
      await refreshAll();
    } catch (e) {
      $("#bootMsg").textContent = e.message;
      $("#bootSpin").hidden = true;
      return;
    }
    $("#boot").classList.add("hide");
    setTimeout(() => { const b = $("#boot"); if (b) b.remove(); }, 450);
    setInterval(() => { refreshAll().catch(() => {}); }, 30000);
    setInterval(() => { if (S.missions) $("#timer").textContent = countdown(); }, 60000);
  }

  window.addEventListener("DOMContentLoaded", boot);
})();


