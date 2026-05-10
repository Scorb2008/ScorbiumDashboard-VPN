// Scorbium Mini App - Production Ready
// Fixed: initData handling, session, avatar

class MiniApp {
  constructor() {
    this.apiBase = '/app';
    this.initData = window.Telegram?.WebApp?.initData || '';
    this.initDataUnsafe = window.Telegram?.WebApp?.initDataUnsafe || {};
    this.user = null;
    this.token = null;
    this.maxRetries = 3;
    this.isOnline = true;
    this.currentScreen = 'home';
    this.userPhotoUrl = this.initDataUnsafe?.user?.photo_url || '';

    this.init();
  }

  async init() {
    // Simple init - WebApp is usually ready when script runs
    if (window.Telegram?.WebApp) {
      // Call ready immediately (safe to call multiple times)
      window.Telegram.WebApp.ready();
      window.Telegram.WebApp.expand();
      
      // Get initData (may be empty if opened via direct link)
      this.initData = window.Telegram.WebApp.initData || '';
      this.initDataUnsafe = window.Telegram.WebApp.initDataUnsafe || {};
      this.userPhotoUrl = this.initDataUnsafe?.user?.photo_url || '';

      document.body.className = this.initDataUnsafe.theme_params?.bg_color === '#000000' ? 'dark' : 'light';

      window.addEventListener('online', () => this.setOnline(true));
      window.addEventListener('offline', () => this.setOnline(false));
    }

    window.addEventListener('unhandledrejection', (e) => {
      console.error('Promise rejection:', e.reason);
      this.showToast('Ошибка приложения. Попробуйте обновить.', 'err');
    });

    if (!this.initData) {
      this.showError('Перезайдите в приложение через бота');
      return;
    }

    const authOk = await this.auth();
    if (authOk) {
      this.loadHome();
    }
    this.bindEvents();
  }

  setOnline(online) {
    this.isOnline = online;
    const bar = document.getElementById('offlineBar');
    if (bar) bar.classList.toggle('on', !online);
  }

  _xhr(method, url, headers, body) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open(method, url, true);
      for (const [k, v] of Object.entries(headers)) {
        xhr.setRequestHeader(k, v);
      }
      xhr.onreadystatechange = () => {
        if (xhr.readyState === 4) {
          try {
            const data = JSON.parse(xhr.responseText || '{}');
            resolve({ ok: xhr.status >= 200 && xhr.status < 300, status: xhr.status, data });
          } catch {
            resolve({ ok: false, status: xhr.status, data: {} });
          }
        }
      };
      xhr.onerror = () => reject(new Error('XHR error'));
      xhr.ontimeout = () => reject(new Error('XHR timeout'));
      xhr.send(body || null);
    });
  }

  async api(path, options = {}) {
    const url = `${this.apiBase}${path}`;
    if (!this.initData) {
      return { ok: false, error: 'auth_required' };
    }

    for (let i = 0; i < this.maxRetries; i++) {
      try {
        if (!this.isOnline) throw new Error('OFFLINE');

        const headers = {};
        if (this.initData) headers['X-Telegram-Init-Data'] = this.initData;
        if (options.method && options.method !== 'GET') {
          headers['Content-Type'] = 'application/json';
        }

        const resp = await this._xhr(
          options.method || 'GET',
          url,
          headers,
          options.body || null
        );

        if (!resp.ok) {
          if (resp.status === 401) {
            this.showToast('Перезайдите в приложение', 'err');
            return { ok: false, error: 'auth_required' };
          }
          if (resp.data?.error?.includes('not found')) {
            return { ok: false, error: 'not_found' };
          }
          throw new Error(resp.data?.error || `HTTP ${resp.status}`);
        }

        return resp.data;

      } catch (e) {
        console.warn(`API ${path} attempt ${i + 1} failed:`, e);
        if (i === this.maxRetries - 1) {
          this.showToast('Нет соединения. Проверьте интернет.', 'err');
          return { ok: false, error: e.message };
        }
        await new Promise(r => setTimeout(r, 1000 * (i + 1)));
      }
    }
  }

  async auth() {
    if (!this.initData) {
      this.showError('Перезайдите в приложение');
      return false;
    }

    try {
      const resp = await this.api('/auth', {
        method: 'POST',
        body: JSON.stringify({ initData: this.initData })
      });

      if (resp.ok) {
        this.user = resp.user;
        this.token = resp.token || null;
        if (this.user.is_admin) {
          const el = document.getElementById('n-admin');
          if (el) el.style.display = 'block';
        }
        return true;
      }

      this.showError('Ошибка авторизации');
      return false;

    } catch (e) {
      this.showToast('Ошибка входа. Перезапустите приложение.', 'err');
      return false;
    }
  }

  async loadHome() {
    this.currentScreen = 'home';
    try {
      this.showLoading('home-c');

      const [profile, plans, settings, servers] = await Promise.all([
        this.api('/profile'),
        this.api('/plans'),
        this.api('/settings'),
        this.api('/servers/status')
      ]);

      if (!profile.ok || !plans.ok || !settings.ok) {
        throw new Error('Failed to load data');
      }

      this.renderHome({
        user: profile.user,
        plans: plans.plans,
        settings,
        servers: (servers && servers.ok) ? (servers.servers || []) : [],
        overall: (servers && servers.ok) ? (servers.overall || 'unknown') : 'unknown'
      });

    } catch (e) {
      this.renderError('home-c', 'Не удалось загрузить главную страницу');
    }
  }

  renderHome(data) {
    const c = document.getElementById('home-c');

    const srvStatus = data.servers.length ?
      `<div class="srv-item"><span class="srv-dot ${data.overall === 'degraded' ? 'warn' : ''}"></span>Серверы ${data.overall}</div>` :
      '';

    const avatarContent = this.userPhotoUrl
      ? `<img src="${this.userPhotoUrl}" alt="avatar" onerror="this.style.display='none'">`
      : (data.user.full_name?.[0] || '@');

    c.innerHTML = `
      <div class="hero">
        <div style="display:flex;align-items:center;gap:14px;margin-bottom:12px">
          <div class="av">${avatarContent}</div>
          <div>
            <div style="font-size:22px;font-weight:800">${data.user.full_name || '@user'}</div>
            <div style="font-size:14px;color:rgba(255,255,255,.9)">@${data.user.username || 'user'}</div>
          </div>
        </div>
      </div>

      ${srvStatus}

      ${data.plans.length ? `
        <div class="sl2">Доступные тарифы</div>
        ${data.plans.map(p => `
          <div class="pc" onclick="app.buyPlan(${p.id}, '${p.name.replace(/'/g, "\\'")}', ${p.price}, ${p.duration_days})">
            <div class="pn">${p.name}</div>
            <div class="pp">${p.price} ₽</div>
            <div class="pd">${p.duration_days} дней</div>
          </div>
        `).join('')}
      ` : '<div class="em"><div class="ei">😔</div><div class="em-title">Нет тарифов</div></div>'}

      <div style="height:24px"></div>
    `;
  }

  async loadSubs() {
    this.currentScreen = 'subs';
    try {
      this.showLoading('subs-c');
      const resp = await this.api('/profile');

      if (!resp.ok) throw new Error('Load failed');

      const c = document.getElementById('subs-c');
      if (resp.active_keys && resp.active_keys.length) {
        c.innerHTML = resp.active_keys.map(k => `
          <div class="kc active-key">
            <div class="kn">${k.name}</div>
            <div class="ke">Действует до ${k.expires_at ? new Date(k.expires_at).toLocaleDateString('ru') : '—'}</div>
            <div class="ku" onclick="app.copyKey('${k.access_url}')">🔗 Копировать ключ</div>
            <div class="db"><div class="df" style="width:100%"></div></div>
          </div>
        `).join('') + `
          <div class="pc" onclick="app.go('buy')">
            <div class="pn">🔄 Продлить подписку</div>
          </div>
        `;
      } else {
        c.innerHTML = `
          <div class="em">
            <div class="ei">🔑</div>
            <div class="em-title">У вас нет активных подписок</div>
            <div class="em-sub">Купите тариф для получения VPN-ключа</div>
            <button class="btn bp" onclick="app.go('buy')">💳 Купить подписку</button>
          </div>
        `;
      }

    } catch (e) {
      this.renderError('subs-c', 'Не удалось загрузить подписки');
    }
  }

  async loadBuy() {
    this.currentScreen = 'buy';
    try {
      this.showLoading('buy-c');
      const [plans, settings] = await Promise.all([
        this.api('/plans'),
        this.api('/settings')
      ]);

      if (!plans.ok || !settings.ok) throw new Error('Load failed');

      const c = document.getElementById('buy-c');
      const ps = settings;
      c.innerHTML = `
        ${ps.has_yookassa ? `<div class="pb" onclick="app.buyWithYookassa()">
          <div class="pb-icon" style="background:linear-gradient(135deg,#00d4aa,#0ea5e9)">💳</div>
          ЮKassa (карта, СБП)
        </div>` : ''}

        ${ps.has_freekassa ? `<div class="pb" onclick="app.buyWithFreeKassa()">
          <div class="pb-icon" style="background:#6b7280">🏦</div>
          FreeKassa
        </div>` : ''}

        ${ps.has_cryptobot ? `<div class="pb" onclick="app.buyWithCrypto()">
          <div class="pb-icon" style="background:linear-gradient(135deg,#f59e0b,#eab308)">₿</div>
          Криптовалюта
        </div>` : ''}

        ${ps.has_stars ? `<div class="pb" onclick="app.payStars()">
          <div class="pb-icon" style="background:#00d4aa">⭐</div>
          Telegram Stars
        </div>` : ''}

        <div class="div"></div>
        <button class="btn bo" onclick="app.openPromo()">🎁 Промокод</button>

        <div class="div"></div>
        <div class="sl2">Выберите тариф</div>
        ${(plans.plans || []).map(p => `
          <div class="pc" onclick="app.buyPlan(${p.id}, '${p.name.replace(/'/g, "\\'")}', ${p.price}, ${p.duration_days})">
            <div class="pn">${p.name}</div>
            <div class="pp">${p.price} ₽</div>
            <div class="pd">${p.duration_days} дней</div>
          </div>
        `).join('')}
      `;

    } catch (e) {
      this.renderError('buy-c', 'Не удалось загрузить способы оплаты');
    }
  }

  async loadProfile() {
    this.currentScreen = 'profile';
    try {
      this.showLoading('profile-c');
      const [profile, stats, payments] = await Promise.all([
        this.api('/profile'),
        this.api('/user/stats'),
        this.api('/user/payments?limit=5')
      ]);

      if (!profile.ok || !stats.ok) throw new Error('Load failed');

      const pmts = payments?.payments || [];
      const pmtsHtml = pmts.length ? pmts.map(p => {
        const statusColor = p.status === 'succeeded' ? '#22c55e' : p.status === 'pending' ? '#eab308' : '#ef4444';
        const statusIcon = p.status === 'succeeded' ? '✅' : p.status === 'pending' ? '⏳' : '❌';
        return `<div style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--br)">
          <span style="font-size:13px">${statusIcon} ${parseFloat(p.amount).toFixed(2)} ₽</span>
          <span style="font-size:12px;color:var(--hi)">${p.provider || '—'}</span>
          <span style="font-size:12px;color:${statusColor}">${p.status}</span>
        </div>`;
      }).join('') : '<div style="font-size:13px;color:var(--hi);text-align:center;padding:12px">Нет платежей</div>';

      const c = document.getElementById('profile-c');
      const avatarContent = this.userPhotoUrl
        ? `<img src="${this.userPhotoUrl}" alt="avatar" onerror="this.style.display='none'">`
        : (profile.user.full_name?.[0] || '@');

      c.innerHTML = `
        <div style="display:flex;align-items:center;gap:16px;margin-bottom:20px">
          <div class="av">${avatarContent}</div>
          <div>
            <div style="font-size:20px;font-weight:800">${profile.user.full_name}</div>
            <div style="font-size:13px;color:var(--hi)">@${profile.user.username || 'user'}</div>
          </div>
        </div>

        <div class="stats-grid">
          <div class="stat-item">
            <div class="stat-val">${(stats.stats?.balance || 0).toFixed(2)} ₽</div>
            <div class="stat-lbl">Баланс</div>
          </div>
          <div class="stat-item">
            <div class="stat-val">${stats.stats?.active_keys || 0}</div>
            <div class="stat-lbl">Активных ключей</div>
          </div>
        </div>

        <div class="card" style="margin-top:12px">
          <div style="font-size:14px;font-weight:700;margin-bottom:8px">💳 Последние платежи</div>
          ${pmtsHtml}
        </div>

        <div style="font-size:12px;color:var(--hi);text-align:center;margin:24px 0">
          Реферальный код: <code style="background:var(--bg3);padding:4px 8px;border-radius:6px">${profile.user.referral_code || '—'}</code>
        </div>

        ${this.user?.is_admin ? `
          <button class="btn bp" onclick="app.go('admin')">👑 Админ панель</button>
        ` : ''}
      `;

    } catch (e) {
      this.renderError('profile-c', 'Не удалось загрузить профиль');
    }
  }

  async loadHelp() {
    this.currentScreen = 'help';
    try {
      this.showLoading('help-c');
      const faq = await this.api('/faq');

      const c = document.getElementById('help-c');
      c.innerHTML = `
        <div style="text-align:center;margin-bottom:24px">
          <div style="font-size:28px;margin-bottom:8px">❓</div>
          <div style="font-size:18px;font-weight:700;margin-bottom:12px">Помощь и FAQ</div>
          ${faq.about ? `<div style="font-size:13px;color:var(--hi)">${faq.about}</div>` : ''}
        </div>

        <div class="faq-item" onclick="toggleFaq(this)">
          <div class="faq-q">
            Как подключить VPN?
            <svg class="faq-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>
          </div>
          <div class="faq-a">1. Скопируйте ссылку из «Подписки»<br>2. Откройте V2Ray/Outline/Nekobox<br>3. Импортируйте ссылку</div>
        </div>

        <div class="faq-item" onclick="toggleFaq(this)">
          <div class="faq-q">
            Сколько устройств?
            <svg class="faq-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>
          </div>
          <div class="faq-a">Неограниченно! Один ключ = все устройства</div>
        </div>

        <div class="faq-item" onclick="toggleFaq(this)">
          <div class="faq-q">
            Пополнить баланс?
            <svg class="faq-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>
          </div>
          <div class="faq-a">Купите тариф -> средства зачислятся автоматически</div>
        </div>

        <button class="btn bo" onclick="app.go('profile')" style="margin-top:24px">👤 Профиль</button>
      `;
    } catch (e) {
      this.renderError('help-c', 'Не удалось загрузить помощь');
    }
  }

  async loadAdmin() {
    this.currentScreen = 'admin';
    try {
      this.showLoading('admin-c');
      const stats = await this.api('/admin/stats');

      if (!stats.ok) throw new Error('Admin load failed');

      const c = document.getElementById('admin-c');
      c.innerHTML = `
        <div class="stats-grid">
          <div class="stat-item">
            <div class="stat-val">${stats.total_users || 0}</div>
            <div class="stat-lbl">Пользователей</div>
          </div>
          <div class="stat-item">
            <div class="stat-val">${stats.active_subs || 0}</div>
            <div class="stat-lbl">Активных</div>
          </div>
          <div class="stat-item">
            <div class="stat-val">${stats.new_today || 0}</div>
            <div class="stat-lbl">Сегодня</div>
          </div>
          <div class="stat-item">
            <div class="stat-val">${(stats.revenue || 0).toFixed(0)} ₽</div>
            <div class="stat-lbl">Выручка</div>
          </div>
        </div>
      `;

    } catch (e) {
      this.renderError('admin-c', 'Не удалось загрузить админ-панель');
    }
  }

  // Navigation
  go(screen) {
    document.querySelectorAll('.scr').forEach(s => s.classList.remove('on'));
    const target = document.getElementById(`s-${screen}`);
    if (target) target.classList.add('on');

    document.querySelectorAll('.nb').forEach(b => b.classList.remove('on'));
    const navBtn = document.getElementById(`n-${screen}`);
    if (navBtn) navBtn.classList.add('on');

    if (screen === 'home') this.loadHome();
    else if (screen === 'subs') this.loadSubs();
    else if (screen === 'buy') this.loadBuy();
    else if (screen === 'profile') this.loadProfile();
    else if (screen === 'help') this.loadHelp();
    else if (screen === 'admin') this.loadAdmin();
  }

  // Payments
  async buyPlan(planId, name, price, days) {
    try {
      const settings = await this.api('/settings');
      if (!settings.has_yookassa) {
        this.showToast('Платежная система не настроена', 'err');
        return;
      }

      this.showLoading('home-c', true);
      const resp = await this.api('/pay/yookassa', {
        method: 'POST',
        body: JSON.stringify({
          plan_id: planId,
          bot_username: settings.bot_username
        })
      });

      if (resp.ok && resp.confirm_url) {
        window.Telegram.WebApp.openLink(resp.confirm_url);
      } else {
        this.showToast('Не удалось создать платеж', 'err');
      }
    } catch (e) {
      this.showToast('Ошибка создания платежа', 'err');
    }
  }

  async payStars() {
    this.showToast('⭐ Telegram Stars скоро', 'info');
  }

  async buyWithYookassa() {
    const plansResp = await this.api('/plans');
    if (!plansResp.ok || !plansResp.plans || !plansResp.plans.length) {
      this.showToast('Нет доступных тарифов', 'err');
      return;
    }
    this.showPlanSelector(plansResp.plans, (plan) => this.buyPlan(plan.id, plan.name, plan.price, plan.duration_days));
  }

  async buyWithFreeKassa() {
    const plansResp = await this.api('/plans');
    if (!plansResp.ok || !plansResp.plans || !plansResp.plans.length) {
      this.showToast('Нет доступных тарифов', 'err');
      return;
    }
    this.showPlanSelector(plansResp.plans, (plan) => this.buyPlanFreeKassa(plan.id, plan.name, plan.price, plan.duration_days));
  }

  async buyWithCrypto() {
    const plansResp = await this.api('/plans');
    if (!plansResp.ok || !plansResp.plans || !plansResp.plans.length) {
      this.showToast('Нет доступных тарифов', 'err');
      return;
    }
    this.showPlanSelector(plansResp.plans, (plan) => this.buyPlanCrypto(plan.id, plan.name, plan.price, plan.duration_days));
  }

  showPlanSelector(plans, onSelect) {
    const c = document.getElementById('buy-c');
    c.innerHTML = `
      <div class="ph"><div class="pt">💳 Выберите тариф</div><button class="btn bo bs" onclick="app.loadBuy()" style="width:auto;padding:6px 12px;font-size:12px">← Назад</button></div>
      <div class="sl2">Доступные тарифы</div>
      ${plans.map(p => `
        <div class="pc" onclick="app._selectedPlan(${p.id}, '${p.name.replace(/'/g, "\\'")}', ${p.price}, ${p.duration_days})">
          <div class="pn">${p.name}</div>
          <div class="pp">${p.price} ₽</div>
          <div class="pd">${p.duration_days} дней</div>
        </div>
      `).join('')}
    `;
    this._onPlanSelect = onSelect;
  }

  _selectedPlan(planId, name, price, days) {
    if (this._onPlanSelect) {
      this._onPlanSelect({ id: planId, name, price, duration_days: days });
    }
  }

  async buyPlanFreeKassa(planId, name, price, days) {
    try {
      const resp = await this.api('/pay/freekassa', {
        method: 'POST',
        body: JSON.stringify({ plan_id: planId })
      });
      if (resp.ok && resp.pay_url) {
        window.Telegram.WebApp.openLink(resp.pay_url);
      } else {
        this.showToast('Не удалось создать платеж', 'err');
      }
    } catch (e) {
      this.showToast('Ошибка создания платежа', 'err');
    }
  }

  async buyPlanCrypto(planId, name, price, days) {
    this.showToast('₿ Крипто-оплата скоро', 'info');
  }

  openPromo() {
    document.getElementById('pm').classList.add('on');
    document.getElementById('pi').focus();
  }

  async applyPromo() {
    const code = document.getElementById('pi').value.trim().toUpperCase();
    if (!code) return;

    try {
      const resp = await this.api('/promo/apply', {
        method: 'POST',
        body: JSON.stringify({ code })
      });

      closePromo();

      if (resp.ok) {
        this.showToast(resp.result?.message || 'Промокод активирован', 'ok');
        setTimeout(() => this.loadHome(), 1500);
      } else {
        this.showToast(resp.error || 'Неверный код', 'err');
      }
    } catch (e) {
      this.showToast('Ошибка промокода', 'err');
    }
  }

  copyKey(url) {
    if (navigator.clipboard) {
      navigator.clipboard.writeText(url).then(() => {
        this.showToast('Ключ скопирован!', 'ok');
      }).catch(() => {
        this.showToast('Не удалось скопировать', 'err');
      });
    }
  }

  // UI Helpers
  showLoading(id, block = false) {
    const el = document.getElementById(id);
    if (!el) return;
    el.innerHTML = block ? '' : '<div class="ld"><div class="ls"></div></div>';
  }

  renderError(id, msg) {
    const el = document.getElementById(id);
    if (!el) return;
    el.innerHTML = `
      <div class="em">
        <div class="ei">⚠️</div>
        <div class="em-title">${msg}</div>
        <button class="btn bo" onclick="app.go(app.currentScreen)">🔄 Попробовать снова</button>
      </div>
    `;
  }

  showError(msg) {
    const t = document.getElementById('toast');
    if (!t) return;
    t.textContent = msg;
    t.className = 'toast err';
    t.classList.add('on');
    setTimeout(() => t.classList.remove('on'), 3000);
  }

  showToast(msg, type = 'ok') {
    const t = document.getElementById('toast');
    if (!t) return;
    t.textContent = msg;
    t.className = `toast ${type}`;
    t.classList.add('on');
    setTimeout(() => t.classList.remove('on'), 3000);
  }

  bindEvents() {
    document.addEventListener('click', (e) => {
      if (e.target.closest('.faq-item')) {
        e.target.closest('.faq-item').classList.toggle('open');
      }
    });

    window.closePromo = () => document.getElementById('pm').classList.remove('on');

    let startY;
    document.addEventListener('touchstart', e => {
      startY = e.touches[0].clientY;
    });

    document.addEventListener('touchmove', e => {
      if (!startY) return;
      const currentY = e.touches[0].clientY;
      const scr = document.querySelector('.scr.on');
      if (currentY - startY > 80 && scr && scr.scrollTop === 0) {
        document.getElementById('ptr').classList.add('show');
      }
    });

    document.addEventListener('touchend', async () => {
      if (startY) {
        const scr = document.querySelector('.scr.on');
        if (scr && scr.scrollTop === 0) {
          document.getElementById('ptr').classList.remove('show');
          this.go(this.currentScreen);
        }
      }
      startY = null;
    });
  }
}

// Global app instance
const app = new MiniApp();

// Global navigation function (called from HTML onclick)
function go(screen) {
  app.go(screen);
}

function applyPromo() {
  app.applyPromo();
}

function toggleTheme() {
  const isDark = document.body.classList.contains('dark');
  document.body.classList.toggle('dark');
  document.body.classList.toggle('light');
  const tbtn = document.getElementById('tbtn');
  if (tbtn) tbtn.textContent = isDark ? '☀️' : '🌙';
  localStorage.theme = document.body.classList.contains('dark') ? 'dark' : 'light';
}

function toggleFaq(el) {
  el.classList.toggle('open');
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => {
    if (localStorage.theme) {
      document.body.className = localStorage.theme;
    }
  });
}
