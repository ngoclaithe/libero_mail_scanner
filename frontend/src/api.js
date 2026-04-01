const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';
console.log('--- ENV DEBUG ---');
console.log('VITE_API_URL:', import.meta.env.VITE_API_URL);
console.log('Final API_URL Used:', API_URL);
console.log('-----------------');

function getToken() {
  return localStorage.getItem('token');
}

function authHeaders() {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export async function apiLogin(username, password) {
  const res = await fetch(`${API_URL}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || 'Đăng nhập thất bại');
  }
  return res.json();
}

export async function apiGetMe() {
  const res = await fetch(`${API_URL}/api/auth/me`, {
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error('Unauthorized');
  return res.json();
}

export async function apiGetState() {
  const res = await fetch(`${API_URL}/api/state`, {
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error('Failed to fetch state');
  return res.json();
}

export async function apiStart() {
  const res = await fetch(`${API_URL}/api/start`, {
    method: 'POST',
    headers: { ...authHeaders(), 'Content-Type': 'application/json' },
  });
  return res.json();
}

export async function apiStop() {
  const res = await fetch(`${API_URL}/api/stop`, {
    method: 'POST',
    headers: { ...authHeaders(), 'Content-Type': 'application/json' },
  });
  return res.json();
}

export async function apiUploadAccounts(file) {
  const fd = new FormData();
  fd.append('file', file);
  const res = await fetch(`${API_URL}/api/upload-accounts`, {
    method: 'POST',
    headers: authHeaders(),
    body: fd,
  });
  return res.json();
}

export async function apiUploadProxies(file) {
  const fd = new FormData();
  fd.append('file', file);
  const res = await fetch(`${API_URL}/api/upload-proxies`, {
    method: 'POST',
    headers: authHeaders(),
    body: fd,
  });
  return res.json();
}

export async function apiGetGallery() {
  const res = await fetch(`${API_URL}/api/gallery`, {
    headers: authHeaders(),
  });
  return res.json();
}

export async function apiGetAdminUsers() {
  const res = await fetch(`${API_URL}/api/admin/users`, {
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error('Forbidden');
  return res.json();
}

export async function apiGetAdminLogs() {
  const res = await fetch(`${API_URL}/api/admin/logs`, {
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error('Forbidden');
  return res.json();
}

export async function apiCreateUser(username, password, credits) {
  const res = await fetch(`${API_URL}/api/admin/create_user`, {
    method: 'POST',
    headers: { ...authHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password, credits }),
  });
  return res.json();
}

export async function apiUpdateCredits(userId, amount, action) {
  const res = await fetch(`${API_URL}/api/admin/update_credits`, {
    method: 'POST',
    headers: { ...authHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: userId, amount, action }),
  });
  return res.json();
}

export function getMediaUrl(path) {
  return `${API_URL}/media/${path}?token=${getToken()}`;
}

export async function apiDeleteGallery(files) {
  const res = await fetch(`${API_URL}/api/gallery/delete`, {
    method: 'POST',
    headers: { ...authHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify({ files }),
  });
  return res.json();
}

export async function apiClearAllGallery() {
  const res = await fetch(`${API_URL}/api/gallery/clear-all`, {
    method: 'POST',
    headers: { ...authHeaders(), 'Content-Type': 'application/json' },
  });
  return res.json();
}

export async function apiDownloadGallery(files) {
  const res = await fetch(`${API_URL}/api/gallery/download`, {
    method: 'POST',
    headers: { ...authHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify({ files }),
  });
  if (!res.ok) throw new Error('Download failed');
  return res.blob();
}

export async function apiGetAccounts() {
  const res = await fetch(`${API_URL}/api/accounts`, {
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error('Failed to fetch accounts');
  return res.json();
}

export async function apiSaveAccounts(accounts) {
  const res = await fetch(`${API_URL}/api/accounts/save`, {
    method: 'POST',
    headers: { ...authHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify({ accounts }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || 'Lưu thất bại');
  }
  return res.json();
}
