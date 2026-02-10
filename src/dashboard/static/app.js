/* ======================================================
   Avtor24 Bot Dashboard — SPA Application
   Alpine.js + Chart.js + WebSocket
   ====================================================== */

function dashboard() {
    return {
        /* ---- Navigation ---- */
        currentPage: 'overview',
        sidebarOpen: false,
        navItems: [
            { page: 'overview',      label: 'Обзор',        icon: '<svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="m2.25 12 8.954-8.955c.44-.439 1.152-.439 1.591 0L21.75 12M4.5 9.75v10.125c0 .621.504 1.125 1.125 1.125H9.75v-4.875c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125V21h4.125c.621 0 1.125-.504 1.125-1.125V9.75M8.25 21h8.25"/></svg>' },
            { page: 'orders',        label: 'Заказы',       icon: '<svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M9 12h3.75M9 15h3.75M9 18h3.75m3 .75H18a2.25 2.25 0 0 0 2.25-2.25V6.108c0-1.135-.845-2.098-1.976-2.192a48.424 48.424 0 0 0-1.123-.08m-5.801 0c-.065.21-.1.433-.1.664 0 .414.336.75.75.75h4.5a.75.75 0 0 0 .75-.75 2.25 2.25 0 0 0-.1-.664m-5.8 0A2.251 2.251 0 0 1 13.5 2.25H15a2.25 2.25 0 0 1 2.15 1.586m-5.8 0c-.376.023-.75.05-1.124.08C9.095 4.01 8.25 4.973 8.25 6.108V8.25m0 0H4.875c-.621 0-1.125.504-1.125 1.125v11.25c0 .621.504 1.125 1.125 1.125h9.75c.621 0 1.125-.504 1.125-1.125V9.375c0-.621-.504-1.125-1.125-1.125H8.25Z"/></svg>' },
            { page: 'analytics',     label: 'Аналитика',    icon: '<svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 0 1 3 19.875v-6.75ZM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V8.625ZM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V4.125Z"/></svg>' },
            { page: 'notifications', label: 'Уведомления',  icon: '<svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M14.857 17.082a23.848 23.848 0 0 0 5.454-1.31A8.967 8.967 0 0 1 18 9.75V9A6 6 0 0 0 6 9v.75a8.967 8.967 0 0 1-2.312 6.022c1.733.64 3.56 1.085 5.455 1.31m5.714 0a24.255 24.255 0 0 1-5.714 0m5.714 0a3 3 0 1 1-5.714 0"/></svg>' },
            { page: 'logs',          label: 'Логи',         icon: '<svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M6.75 7.5l3 2.25-3 2.25m4.5 0h3m-9 8.25h13.5A2.25 2.25 0 0021 18V6a2.25 2.25 0 00-2.25-2.25H5.25A2.25 2.25 0 003 6v12a2.25 2.25 0 002.25 2.25z"/></svg>' },
            { page: 'settings',      label: 'Настройки',    icon: '<svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.325.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 0 1 1.37.49l1.296 2.247a1.125 1.125 0 0 1-.26 1.431l-1.003.827c-.293.241-.438.613-.43.992a7.723 7.723 0 0 1 0 .255c-.008.378.137.75.43.991l1.004.827c.424.35.534.955.26 1.43l-1.298 2.247a1.125 1.125 0 0 1-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.47 6.47 0 0 1-.22.128c-.331.183-.581.495-.644.869l-.213 1.281c-.09.543-.56.94-1.11.94h-2.594c-.55 0-1.019-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 0 1-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 0 1-1.369-.49l-1.297-2.247a1.125 1.125 0 0 1 .26-1.431l1.004-.827c.292-.24.437-.613.43-.991a6.932 6.932 0 0 1 0-.255c.007-.38-.138-.751-.43-.992l-1.004-.827a1.125 1.125 0 0 1-.26-1.43l1.297-2.247a1.125 1.125 0 0 1 1.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.086.22-.128.332-.183.582-.495.644-.869l.214-1.28Z"/><path stroke-linecap="round" stroke-linejoin="round" d="M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z"/></svg>' },
            { page: 'chats',         label: 'Чаты',         icon: '<svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M20.25 8.511c.884.284 1.5 1.128 1.5 2.097v4.286c0 1.136-.847 2.1-1.98 2.193-.34.027-.68.052-1.02.072v3.091l-3-3c-1.354 0-2.694-.055-4.02-.163a2.115 2.115 0 0 1-.825-.242m9.345-8.334a2.126 2.126 0 0 0-.476-.095 48.64 48.64 0 0 0-8.048 0c-1.131.094-1.976 1.057-1.976 2.192v4.286c0 .837.46 1.58 1.155 1.951m9.345-8.334V6.637c0-1.621-1.152-3.026-2.76-3.235A48.455 48.455 0 0 0 11.25 3c-2.115 0-4.198.137-6.24.402-1.608.209-2.76 1.614-2.76 3.235v6.226c0 1.621 1.152 3.026 2.76 3.235.577.075 1.157.14 1.74.194V21l4.155-4.155"/></svg>' },
        ],

        /* ---- State ---- */
        botRunning: false,
        uptime: 0,
        unreadCount: 0,
        stats: {},
        events: [],
        toasts: [],
        _toastId: 0,

        /* ---- Orders ---- */
        ordersList: [],
        ordersPagination: { total: 0, pages: 0 },
        ordersFilter: { status: null, page: 1 },
        orderDetail: null,
        orderTabs: [
            { label: 'Все',          value: null },
            { label: 'Активные',     value: 'accepted' },
            { label: 'Ставки',       value: 'bid_placed' },
            { label: 'Генерация',    value: 'generating' },
            { label: 'Выполненные',  value: 'delivered' },
            { label: 'Завершённые',  value: 'completed' },
            { label: 'Отклонённые',  value: 'rejected' },
        ],

        /* ---- Analytics ---- */
        analyticsPeriod: '7d',
        analyticsData: {},
        analyticsPeriods: [
            { label: 'Сегодня', value: '1d' },
            { label: '7 дней',  value: '7d' },
            { label: '30 дней', value: '30d' },
            { label: '90 дней', value: '90d' },
            { label: 'Всё',    value: 'all' },
        ],
        _incomeChart: null,
        _ordersChart: null,
        _analyticsIncomeChart: null,
        _analyticsApiChart: null,

        /* ---- Notifications ---- */
        notifList: [],
        notifFilter: null,
        notifTabs: [
            { label: 'Все',         value: null },
            { label: 'Заказы',      value: 'new_order' },
            { label: 'Сообщения',   value: 'new_message' },
            { label: 'Ошибки',      value: 'error' },
            { label: 'Доставка',    value: 'order_delivered' },
        ],

        /* ---- Logs ---- */
        logsList: [],
        logFilter: null,
        logAutoScroll: true,
        logTabs: [
            { label: 'Все',       value: null },
            { label: 'SCAN',      value: 'scan' },
            { label: 'SCORE',     value: 'score' },
            { label: 'BID',       value: 'bid' },
            { label: 'GENERATE',  value: 'generate' },
            { label: 'PLAGIARISM',value: 'plagiarism' },
            { label: 'CHAT',      value: 'chat' },
            { label: 'ERROR',     value: 'error' },
        ],

        /* ---- Settings ---- */
        settingsData: {},

        /* ---- Chats ---- */
        chatsList: [],
        activeChat: null,
        chatMessages: [],
        chatInput: '',

        /* ---- WebSockets ---- */
        _wsNotif: null,
        _wsLogs: null,

        /* ==================================================
           INIT
           ================================================== */
        async init() {
            /* Route from hash */
            const hash = window.location.hash.replace('#', '') || 'overview';
            this.currentPage = hash;

            window.addEventListener('hashchange', () => {
                const h = window.location.hash.replace('#', '') || 'overview';
                this.navigate(h);
            });

            /* Load initial data */
            await this.loadStats();
            this.loadPageData();

            /* WebSocket connections */
            this.connectWS();

            /* Auto-refresh stats every 30s */
            setInterval(() => this.loadStats(), 30000);

            /* Uptime ticker */
            setInterval(() => { if (this.botRunning) this.uptime++; }, 1000);
        },

        /* ==================================================
           NAVIGATION
           ================================================== */
        navigate(page) {
            this.currentPage = page;
            window.location.hash = page;
            this.loadPageData();
        },

        get pageTitle() {
            const titles = {
                overview: 'Обзор', orders: 'Заказы', analytics: 'Аналитика',
                notifications: 'Уведомления', logs: 'Логи', settings: 'Настройки', chats: 'Чаты',
            };
            return titles[this.currentPage] || '';
        },

        loadPageData() {
            switch (this.currentPage) {
                case 'overview':      this.loadOverview(); break;
                case 'orders':        this.loadOrders(); break;
                case 'analytics':     this.loadAnalytics(); break;
                case 'notifications': this.loadNotifications(); break;
                case 'logs':          this.loadLogs(); break;
                case 'settings':      this.loadSettings(); break;
                case 'chats':         this.loadChats(); break;
            }
        },

        /* ==================================================
           API HELPERS
           ================================================== */
        async api(url, opts = {}) {
            try {
                const res = await fetch(url, {
                    headers: { 'Content-Type': 'application/json', ...opts.headers },
                    ...opts,
                });
                if (res.status === 401) {
                    window.location.href = '/dashboard/login';
                    return null;
                }
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                return await res.json();
            } catch (e) {
                console.error('API Error:', url, e);
                return null;
            }
        },

        /* ==================================================
           STATS / OVERVIEW
           ================================================== */
        async loadStats() {
            const data = await this.api('/api/dashboard/stats');
            if (data) {
                this.stats = data;
                this.botRunning = data.bot_running || false;
                this.uptime = data.uptime || 0;
            }
            /* Load unread count */
            const notifs = await this.api('/api/dashboard/notifications?per_page=1');
            if (notifs) this.unreadCount = notifs.unread_count || 0;
        },

        async loadOverview() {
            /* Load recent notifications as events */
            const data = await this.api('/api/dashboard/notifications?per_page=50');
            if (data) this.events = data.items || [];

            /* Build overview charts */
            await this.loadAnalyticsForCharts();
        },

        async loadAnalyticsForCharts() {
            const d = new Date();
            const to = d.toISOString().split('T')[0];
            const from = new Date(d.getTime() - 30 * 86400000).toISOString().split('T')[0];
            const data = await this.api(`/api/dashboard/analytics?date_from=${from}&date_to=${to}`);
            if (!data) return;

            const daily = data.daily || [];
            const labels = daily.map(d => d.date ? d.date.slice(5) : '');
            const incomes = daily.map(d => d.income_rub || 0);
            const delivered = daily.map(d => d.orders_delivered || 0);

            this.$nextTick(() => {
                this.renderChart('incomeChart', '_incomeChart', 'bar', labels, [{
                    label: 'Доход (руб)', data: incomes,
                    backgroundColor: 'rgba(124,58,237,0.5)', borderColor: '#7c3aed',
                    borderWidth: 1, borderRadius: 4,
                }]);
                this.renderChart('ordersChart', '_ordersChart', 'line', labels, [{
                    label: 'Выполнено', data: delivered,
                    borderColor: '#4ade80', backgroundColor: 'rgba(74,222,128,0.1)',
                    fill: true, tension: 0.3, pointRadius: 2,
                }]);
            });
        },

        /* ==================================================
           ORDERS
           ================================================== */
        async loadOrders() {
            let url = `/api/dashboard/orders?page=${this.ordersFilter.page}&per_page=20`;
            if (this.ordersFilter.status) url += `&status=${this.ordersFilter.status}`;
            const data = await this.api(url);
            if (data) {
                this.ordersList = data.items || [];
                this.ordersPagination = { total: data.total, pages: data.pages };
            }
        },

        async openOrderDetail(id) {
            const data = await this.api(`/api/dashboard/orders/${id}`);
            if (data) this.orderDetail = data;
        },

        async stopOrder(id) {
            await this.api(`/api/dashboard/orders/${id}/stop`, { method: 'POST' });
            this.orderDetail = null;
            this.loadOrders();
            this.showToast('Заказ остановлен');
        },

        async regenOrder(id) {
            await this.api(`/api/dashboard/orders/${id}/regen`, { method: 'POST' });
            this.orderDetail = null;
            this.loadOrders();
            this.showToast('Перегенерация запущена');
        },

        /* ==================================================
           ANALYTICS
           ================================================== */
        async loadAnalytics() {
            const d = new Date();
            const to = d.toISOString().split('T')[0];
            let from;
            switch (this.analyticsPeriod) {
                case '1d':  from = to; break;
                case '7d':  from = new Date(d.getTime() - 7  * 86400000).toISOString().split('T')[0]; break;
                case '30d': from = new Date(d.getTime() - 30 * 86400000).toISOString().split('T')[0]; break;
                case '90d': from = new Date(d.getTime() - 90 * 86400000).toISOString().split('T')[0]; break;
                default:    from = '2020-01-01';
            }

            const data = await this.api(`/api/dashboard/analytics?date_from=${from}&date_to=${to}`);
            if (!data) return;
            this.analyticsData = data;

            const daily = data.daily || [];
            const labels = daily.map(d => d.date ? d.date.slice(5) : '');
            const incomes = daily.map(d => d.income_rub || 0);

            this.$nextTick(() => {
                this.renderChart('analyticsIncomeChart', '_analyticsIncomeChart', 'bar', labels, [{
                    label: 'Доход (руб)', data: incomes,
                    backgroundColor: 'rgba(124,58,237,0.5)', borderColor: '#7c3aed',
                    borderWidth: 1, borderRadius: 4,
                }]);

                /* API by model pie */
                const apiModels = data.api_by_model || [];
                const modelLabels = apiModels.map(m => m.model);
                const modelCosts = apiModels.map(m => m.cost_usd || 0);
                const colors = ['#7c3aed', '#4ade80', '#facc15', '#f87171', '#60a5fa'];
                this.renderChart('analyticsApiChart', '_analyticsApiChart', 'doughnut', modelLabels, [{
                    data: modelCosts,
                    backgroundColor: colors.slice(0, modelLabels.length),
                    borderWidth: 0,
                }], { cutout: '65%' });
            });
        },

        /* ==================================================
           NOTIFICATIONS
           ================================================== */
        async loadNotifications() {
            let url = '/api/dashboard/notifications?per_page=100';
            if (this.notifFilter) url += `&type=${this.notifFilter}`;
            const data = await this.api(url);
            if (data) {
                this.notifList = data.items || [];
                this.unreadCount = data.unread_count || 0;
            }
        },

        async markRead(ids) {
            await this.api('/api/dashboard/notifications/read', {
                method: 'POST', body: JSON.stringify({ ids }),
            });
            this.notifList.forEach(n => { if (ids.includes(n.id)) n.is_read = true; });
            this.unreadCount = Math.max(0, this.unreadCount - ids.length);
        },

        async markAllRead() {
            const ids = this.notifList.filter(n => !n.is_read).map(n => n.id);
            if (ids.length) await this.markRead(ids);
        },

        /* ==================================================
           LOGS
           ================================================== */
        async loadLogs() {
            let url = '/api/dashboard/logs?per_page=200';
            if (this.logFilter) url += `&action=${this.logFilter}`;
            const data = await this.api(url);
            if (data) {
                this.logsList = (data.items || []).reverse();
                this.$nextTick(() => this.scrollLogTerminal());
            }
        },

        scrollLogTerminal() {
            if (!this.logAutoScroll) return;
            const el = document.getElementById('logTerminal');
            if (el) el.scrollTop = el.scrollHeight;
        },

        /* ==================================================
           SETTINGS
           ================================================== */
        async loadSettings() {
            const data = await this.api('/api/dashboard/settings');
            if (data) this.settingsData = data;
        },

        async saveSettings() {
            await this.api('/api/dashboard/settings', {
                method: 'PUT', body: JSON.stringify(this.settingsData),
            });
            this.showToast('Настройки сохранены');
        },

        /* ==================================================
           CHATS
           ================================================== */
        async loadChats() {
            /* Load orders that have messages (active orders) */
            const data = await this.api('/api/dashboard/orders?per_page=50&sort_by=updated_at&sort_dir=desc');
            if (data) {
                this.chatsList = (data.items || []).filter(o =>
                    ['accepted','generating','checking_plagiarism','delivered','completed','bid_placed'].includes(o.status)
                );
            }
        },

        async openChat(order) {
            this.activeChat = order;
            const data = await this.api(`/api/dashboard/orders/${order.id}`);
            if (data) {
                this.chatMessages = data.messages || [];
                this.$nextTick(() => {
                    const el = document.getElementById('chatMessages');
                    if (el) el.scrollTop = el.scrollHeight;
                });
            }
        },

        async sendChatMessage() {
            if (!this.chatInput.trim() || !this.activeChat) return;
            const text = this.chatInput.trim();
            this.chatInput = '';

            await this.api(`/api/dashboard/chat/${this.activeChat.id}/send`, {
                method: 'POST', body: JSON.stringify({ text }),
            });

            /* Add optimistically */
            this.chatMessages.push({
                id: Date.now(), direction: 'outgoing', text, is_auto_reply: false,
                created_at: new Date().toISOString(),
            });
            this.$nextTick(() => {
                const el = document.getElementById('chatMessages');
                if (el) el.scrollTop = el.scrollHeight;
            });
        },

        /* ==================================================
           BOT CONTROL
           ================================================== */
        async toggleBot() {
            const data = await this.api('/api/dashboard/bot/toggle', { method: 'POST' });
            if (data) {
                this.botRunning = data.bot_running;
                this.showToast(this.botRunning ? 'Бот запущен' : 'Бот остановлен');
            }
        },

        logout() {
            document.cookie = 'dashboard_token=; Max-Age=0; path=/';
            window.location.href = '/dashboard/login';
        },

        /* ==================================================
           WEBSOCKET
           ================================================== */
        connectWS() {
            const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
            const base = `${proto}//${location.host}`;

            /* Notifications WS */
            this._connectOneWS(`${base}/ws/notifications`, 'notif', (data) => {
                /* New notification arrived */
                this.events.unshift(data);
                this.unreadCount++;
                if (this.currentPage === 'notifications') {
                    this.notifList.unshift(data);
                }
                this.showToast(data.title || 'Новое уведомление');
                this.playNotifSound();
            });

            /* Logs WS */
            this._connectOneWS(`${base}/ws/logs`, 'logs', (data) => {
                this.logsList.push(data);
                /* Keep only last 500 */
                if (this.logsList.length > 500) this.logsList.splice(0, this.logsList.length - 500);
                this.$nextTick(() => this.scrollLogTerminal());
            });
        },

        _connectOneWS(url, name, onMessage) {
            try {
                const ws = new WebSocket(url);
                ws.onmessage = (ev) => {
                    try {
                        const data = JSON.parse(ev.data);
                        onMessage(data);
                    } catch (e) { /* ignore non-json */ }
                };
                ws.onclose = () => {
                    /* Reconnect after 5s */
                    setTimeout(() => this._connectOneWS(url, name, onMessage), 5000);
                };
                ws.onerror = () => ws.close();
                if (name === 'notif') this._wsNotif = ws;
                else this._wsLogs = ws;
            } catch (e) { /* WS unavailable */ }
        },

        playNotifSound() {
            try {
                const audio = document.getElementById('notifSound');
                if (audio) { audio.currentTime = 0; audio.play().catch(() => {}); }
            } catch (e) { /* ignore */ }
        },

        /* ==================================================
           CHART HELPER
           ================================================== */
        renderChart(canvasId, storeKey, type, labels, datasets, extraOpts = {}) {
            const canvas = document.getElementById(canvasId);
            if (!canvas) return;
            if (this[storeKey]) this[storeKey].destroy();

            const isDoughnut = type === 'doughnut' || type === 'pie';
            this[storeKey] = new Chart(canvas, {
                type,
                data: { labels, datasets },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { display: isDoughnut, position: 'bottom', labels: { color: '#a1a1aa', padding: 16, font: { size: 11 } } },
                    },
                    scales: isDoughnut ? {} : {
                        x: { grid: { color: 'rgba(255,255,255,0.03)' }, ticks: { color: '#71717a', font: { size: 10 } } },
                        y: { grid: { color: 'rgba(255,255,255,0.03)' }, ticks: { color: '#71717a', font: { size: 10 } }, beginAtZero: true },
                    },
                    ...extraOpts,
                },
            });
        },

        /* ==================================================
           FORMATTING HELPERS
           ================================================== */
        fmt(n) {
            if (n == null) return '0';
            return Number(n).toLocaleString('ru-RU');
        },

        formatUptime(sec) {
            if (!sec) return '0m';
            const h = Math.floor(sec / 3600);
            const m = Math.floor((sec % 3600) / 60);
            return h > 0 ? `${h}h ${m}m` : `${m}m`;
        },

        formatDate(d) {
            if (!d) return '';
            try {
                const dt = new Date(d);
                return dt.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' });
            } catch { return d; }
        },

        formatTime(d) {
            if (!d) return '';
            try {
                const dt = new Date(d);
                return dt.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
            } catch { return d; }
        },

        timeAgo(d) {
            if (!d) return '';
            try {
                const now = Date.now();
                const dt = new Date(d).getTime();
                const diff = Math.floor((now - dt) / 1000);
                if (diff < 60) return 'только что';
                if (diff < 3600) return Math.floor(diff / 60) + ' мин';
                if (diff < 86400) return Math.floor(diff / 3600) + ' ч';
                return Math.floor(diff / 86400) + ' д';
            } catch { return ''; }
        },

        statusLabel(s) {
            const map = {
                new: 'Новый', scored: 'Оценён', bid_placed: 'Ставка',
                accepted: 'Принят', generating: 'Генерация', checking_plagiarism: 'Проверка',
                rewriting: 'Рерайт', delivered: 'Отправлен', completed: 'Завершён',
                rejected: 'Отклонён', error: 'Ошибка',
            };
            return map[s] || s || '—';
        },

        statusClass(s) {
            const map = {
                new: 'badge-new', scored: 'badge-scored', bid_placed: 'badge-bid',
                accepted: 'badge-accepted', generating: 'badge-generating',
                checking_plagiarism: 'badge-checking', rewriting: 'badge-rewriting',
                delivered: 'badge-delivered', completed: 'badge-completed',
                rejected: 'badge-rejected', error: 'badge-error',
            };
            return map[s] || 'badge-new';
        },

        logActionClass(a) {
            const map = {
                scan: 'log-scan', score: 'log-score', bid: 'log-bid',
                accept: 'log-accept', generate: 'log-generate',
                plagiarism: 'log-plagiarism', deliver: 'log-deliver',
                chat: 'log-chat', error: 'log-error', system: 'log-system',
                rewrite: 'log-rewrite',
            };
            return map[a] || 'log-default';
        },

        notifIcon(type) {
            const icons = {
                new_order:       '<span class="text-blue-400 text-base">&#x1F195;</span>',
                order_accepted:  '<span class="text-indigo-400 text-base">&#x1F389;</span>',
                order_delivered: '<span class="text-green-400 text-base">&#x1F4E4;</span>',
                new_message:     '<span class="text-cyan-400 text-base">&#x1F4AC;</span>',
                error:           '<span class="text-red-400 text-base">&#x26A0;&#xFE0F;</span>',
                daily_summary:   '<span class="text-purple-400 text-base">&#x1F4CA;</span>',
            };
            return icons[type] || '<span class="text-gray-500 text-base">&#x2022;</span>';
        },

        /* ==================================================
           TOASTS
           ================================================== */
        showToast(message, type = 'info') {
            const id = ++this._toastId;
            const toast = { id, message, type, visible: true };
            this.toasts.push(toast);
            setTimeout(() => { toast.visible = false; }, 3500);
            setTimeout(() => { this.toasts = this.toasts.filter(t => t.id !== id); }, 4000);
        },
    };
}
