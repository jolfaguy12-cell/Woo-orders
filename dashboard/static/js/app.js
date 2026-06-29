'use strict';

/* ── Sidebar mobile toggle ───────────────────────────────────────────── */
const sidebar  = document.getElementById('sidebar');
const overlay  = document.getElementById('sidebarOverlay');
const menuBtn  = document.getElementById('mobileMenuBtn');

if (menuBtn) {
  menuBtn.addEventListener('click', () => {
    sidebar.classList.toggle('open');
    overlay.classList.toggle('show');
  });
}

if (overlay) {
  overlay.addEventListener('click', () => {
    sidebar.classList.remove('open');
    overlay.classList.remove('show');
  });
}

/* ── Toast notifications ─────────────────────────────────────────────── */
function showToast(msg, type = 'success') {
  let container = document.getElementById('toastContainer');
  if (!container) {
    container = document.createElement('div');
    container.id = 'toastContainer';
    container.style.cssText = 'position:fixed;bottom:24px;left:24px;z-index:9999;display:flex;flex-direction:column;gap:8px;';
    document.body.appendChild(container);
  }

  const colors = {
    success: '#10b981',
    error:   '#ef4444',
    warning: '#f59e0b',
    info:    '#3b82f6',
  };

  const icons = { success: '✓', error: '✗', warning: '⚠', info: 'ℹ' };

  const toast = document.createElement('div');
  toast.style.cssText = `
    display:flex;align-items:center;gap:10px;
    background:#fff;border-radius:10px;
    box-shadow:0 4px 20px rgba(0,0,0,.12);
    padding:12px 18px;min-width:260px;max-width:380px;
    border-right:4px solid ${colors[type] || colors.info};
    font-family:'Vazirmatn',sans-serif;font-size:13px;
    animation:slideIn .2s ease;
  `;
  toast.innerHTML = `
    <span style="font-size:18px;color:${colors[type]};">${icons[type]}</span>
    <span style="flex:1;">${msg}</span>
  `;

  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateX(-20px)';
    toast.style.transition = '.3s ease';
    setTimeout(() => toast.remove(), 300);
  }, 3500);
}

/* ── Auto-dismiss alerts ─────────────────────────────────────────────── */
document.querySelectorAll('.alert[data-auto-dismiss]').forEach(el => {
  setTimeout(() => {
    el.style.opacity = '0';
    el.style.transition = '.4s';
    setTimeout(() => el.remove(), 400);
  }, 4000);
});

/* ── Telegram test message ───────────────────────────────────────────── */
const testBtn = document.getElementById('sendTestBtn');
if (testBtn) {
  testBtn.addEventListener('click', async () => {
    const chatId = document.getElementById('testChatId')?.value || '';
    const text   = document.getElementById('testText')?.value || '✅ پیام آزمایشی از پنل مدیریت بهداشتیک';

    testBtn.disabled = true;
    testBtn.textContent = '...در حال ارسال';

    try {
      const resp = await fetch(testBtn.dataset.url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_id: chatId, text }),
      });
      const data = await resp.json();
      if (data.ok) {
        showToast(data.message || 'پیام ارسال شد.', 'success');
      } else {
        showToast('خطا: ' + (data.error || 'ارسال ناموفق'), 'error');
      }
    } catch (e) {
      showToast('خطای شبکه: ' + e.message, 'error');
    }

    testBtn.disabled = false;
    testBtn.textContent = '📤 ارسال پیام آزمایشی';
  });
}

/* ── Shortcode copy-to-clipboard ─────────────────────────────────────── */
document.querySelectorAll('.shortcode-chip').forEach(chip => {
  chip.addEventListener('click', () => {
    const code = chip.querySelector('code')?.textContent || '';
    navigator.clipboard.writeText(code).then(() => {
      showToast(`کد ${code} کپی شد.`, 'info');
    }).catch(() => {
      showToast('کپی ناموفق بود.', 'warning');
    });
  });
  chip.title = 'کلیک کنید تا کپی شود';
});

/* ── Dashboard status refresh ────────────────────────────────────────── */
const statusUrl = document.getElementById('statusApiUrl');
if (statusUrl) {
  async function refreshStatus() {
    try {
      const r = await fetch(statusUrl.value);
      if (!r.ok) return;
      const d = await r.json();

      const setStatus = (id, up) => {
        const el = document.getElementById(id);
        if (!el) return;
        el.className = 'status-badge ' + (up ? 'up' : 'down');
        el.textContent = up ? '● فعال' : '● غیرفعال';
      };

      setStatus('statusWebhook', d.webhook_up);
      setStatus('statusHub',     d.hub_up);
      setStatus('statusBot',     d.bot_configured);

      const ts = document.getElementById('statusTimestamp');
      if (ts) ts.textContent = 'آخرین بروزرسانی: ' + new Date().toLocaleTimeString('fa-IR');
    } catch (_) {}
  }

  refreshStatus();
  setInterval(refreshStatus, 30000);
}

/* ── Log viewer auto-scroll & color ─────────────────────────────────── */
const logViewer = document.getElementById('logViewer');
if (logViewer) {
  logViewer.scrollTop = logViewer.scrollHeight;

  document.querySelectorAll('.log-line').forEach(line => {
    const t = line.textContent;
    if (t.includes('ERROR') || t.includes('failed') || t.includes('error'))
      line.classList.add('error');
    else if (t.includes('WARNING') || t.includes('warning'))
      line.classList.add('warning');
    else if (t.includes('processed') || t.includes('notified') || t.includes('INFO'))
      line.classList.add('info');
  });

  const refreshLogsBtn = document.getElementById('refreshLogsBtn');
  const logsUrl = document.getElementById('logsApiUrl');
  if (refreshLogsBtn && logsUrl) {
    refreshLogsBtn.addEventListener('click', async () => {
      refreshLogsBtn.textContent = '...در حال بارگذاری';
      try {
        const r = await fetch(logsUrl.value + '?n=500');
        const d = await r.json();
        logViewer.innerHTML = d.lines.map(l =>
          `<div class="log-line">${escapeHtml(l)}</div>`
        ).join('');
        logViewer.querySelectorAll('.log-line').forEach(line => {
          const t = line.textContent;
          if (t.includes('ERROR') || t.includes('failed')) line.classList.add('error');
          else if (t.includes('WARNING')) line.classList.add('warning');
          else line.classList.add('info');
        });
        logViewer.scrollTop = logViewer.scrollHeight;
      } catch (e) {
        showToast('خطا در بارگذاری لاگ: ' + e.message, 'error');
      }
      refreshLogsBtn.textContent = '🔄 بروزرسانی';
    });
  }
}

function escapeHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

/* ── Confirm-before-delete ───────────────────────────────────────────── */
document.querySelectorAll('[data-confirm]').forEach(btn => {
  btn.addEventListener('click', e => {
    if (!confirm(btn.dataset.confirm)) e.preventDefault();
  });
});

/* CSS animation for toast */
const style = document.createElement('style');
style.textContent = `
  @keyframes slideIn {
    from { opacity:0; transform:translateX(-20px); }
    to   { opacity:1; transform:translateX(0); }
  }
`;
document.head.appendChild(style);
