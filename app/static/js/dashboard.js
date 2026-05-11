// Dashboard shared JavaScript
function showToast(msg, type) {
  const t = document.createElement('div');
  t.className = 'toast toast-' + (type || 'info');
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3000);
}

async function apiPost(url, data) {
  const fd = data instanceof FormData ? data : new FormData();
  if (!(data instanceof FormData)) {
    for (const [k, v] of Object.entries(data)) fd.append(k, v);
  }
  const res = await fetch(url, { method: 'POST', body: fd });
  return res.json();
}
