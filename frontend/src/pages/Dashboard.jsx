import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { apiGetState, apiStart, apiStop, apiUploadAccounts, apiUploadProxies, apiGetGallery, getMediaUrl, apiDeleteGallery, apiDownloadGallery, apiClearAllGallery, apiGetAccounts, apiSaveAccounts, apiGetProxies, apiSaveProxies } from '../api';
import {
  Menu, Mail, Image as ImageIcon, Activity, Globe,
  Play, Square, LogOut, Settings, User as UserIcon,
  Trash2, Download, CheckSquare, X, ChevronLeft, ChevronRight,
  Upload, Plus, Edit3, Save
} from 'lucide-react';

const POLL_MS = 2000;
const MAX_LOGS = 150;

function Badge({ status }) {
  return <span className={`badge badge-${status}`}>{status}</span>;
}

function ProgressBar({ done, total }) {
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;
  return (
    <div className="prog-wrap">
      <div className="prog-track">
        <div className="prog-bar" style={{ width: `${pct}%` }} />
      </div>
      <span className="prog-label">{pct}%</span>
    </div>
  );
}

function esc(s) {
  return String(s ?? '');
}

export default function Dashboard() {
  const { user, logout, refreshUser } = useAuth();
  const [state, setState] = useState({ status: 'idle', totals: {}, accounts: {}, proxies: [], ai_logs: [] });
  const [activeTab, setActiveTab] = useState('accounts');
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);
  const [logs, setLogs] = useState([]);
  const [gallery, setGallery] = useState({});
  const [showAccountsModal, setShowAccountsModal] = useState(false);
  const [showProxiesModal, setShowProxiesModal] = useState(false);
  const prevStateRef = useRef({});

  const addLog = useCallback((msg) => {
    const now = new Date().toLocaleTimeString('vi-VN');
    setLogs(prev => {
      const next = [{ t: now, msg: String(msg) }, ...prev];
      return next.slice(0, MAX_LOGS);
    });
  }, []);

  // Poll state
  useEffect(() => {
    let active = true;

    const fetchState = async () => {
      try {
        const s = await apiGetState();

        // Detect account changes for log
        const prevAccounts = prevStateRef.current.accounts || {};
        const curAccounts = s.accounts || {};
        Object.entries(curAccounts).forEach(([email, a]) => {
          const prev = prevAccounts[email] || {};
          if (prev.status !== a.status && a.status !== 'pending') {
            addLog(`[${email}] → ${a.status}${a.error ? ': ' + a.error : ''}`);
          }
          if (a.last_file && a.last_file !== prev.last_file) {
            addLog(`[${email}] 📎 ${a.last_file}`);
          }
        });

        prevStateRef.current = s;
        if (active) setState(s);
      } catch (e) {
        addLog('⚠ Poll error: ' + e.message);
      }
    };

    fetchState();
    const interval = setInterval(fetchState, POLL_MS);
    return () => { active = false; clearInterval(interval); };
  }, [addLog]);

  // Poll gallery
  useEffect(() => {
    let active = true;
    const fetchGallery = async () => {
      try {
        const data = await apiGetGallery();
        if (active) setGallery(data);
      } catch (e) {
        console.error('Gallery poll fail', e);
      }
    };

    fetchGallery();
    const interval = setInterval(fetchGallery, 6000);
    return () => { active = false; clearInterval(interval); };
  }, []);

  const handleStart = async () => {
    addLog('▶ Đang khởi động hệ thống quét...');
    const data = await apiStart();
    addLog(data.msg);
  };

  const handleStop = async () => {
    addLog('■ Đang gửi lệnh dừng khẩn cấp...');
    const data = await apiStop();
    addLog(data.msg);
  };





  const t = state.totals || {};
  const running = state.status === 'running';
  const accounts = state.accounts || {};
  const proxies = state.proxies || [];
  const aiLogs = state.ai_logs || [];

  return (
    <div className="app-wrapper">
      {/* ── Sidebar ── */}
      <aside className={`main-sidebar ${isSidebarOpen ? '' : 'collapsed'}`}>
        <div className="main-sidebar-header">
          {isSidebarOpen ? <h1>Libero Scanner</h1> : <h1>LS</h1>}
        </div>
        <nav className="main-nav" style={{ flex: 1 }}>
          {[
            ['accounts', 'Thông tin Quét Thư', <Mail size={18} />],
            ['gallery', 'Thư Viện Ảnh', <ImageIcon size={18} />],
            ['ai', 'Tiến Trình AI', <Activity size={18} />],
            ['proxies', 'Quản Lý Proxy', <Globe size={18} />],
          ].map(([id, label, iconEl]) => (
            <button
              key={id}
              className={`nav-item ${activeTab === id ? 'active' : ''}`}
              onClick={() => setActiveTab(id)}
              title={!isSidebarOpen ? label : ''}
            >
              <span className="nav-icon">{iconEl}</span>
              {isSidebarOpen && <span className="nav-label">{label}</span>}
            </button>
          ))}
        </nav>
        <div className="main-sidebar-footer" style={{ padding: '12px' }}>
          <button
            className="nav-item"
            style={{ width: '100%', justifyContent: isSidebarOpen ? 'flex-start' : 'center' }}
            onClick={() => setIsSidebarOpen(!isSidebarOpen)}
            title={isSidebarOpen ? 'Thu gọn' : 'Mở rộng'}
          >
            <span className="nav-icon">{isSidebarOpen ? <ChevronLeft size={18} /> : <ChevronRight size={18} />}</span>
            {isSidebarOpen && <span className="nav-label">Thu gọn</span>}
          </button>
        </div>
      </aside>

      {/* ── Main Layout ── */}
      <div className="main-content-wrapper">
        {/* Topbar */}
        <header className="main-topbar">
          <div className="topbar-actions">
            <div className="status-wrap">
              <span className={`status-dot ${state.status}`} />
              <span id="status-text">{capitalize(state.status)}</span>
            </div>
            <button className="btn btn-start" onClick={handleStart} disabled={running} title="Khởi động">
              <Play size={16} /> {isSidebarOpen ? 'Khởi động' : ''}
            </button>
            <button className="btn btn-stop" onClick={handleStop} disabled={!running} title="Dừng lại">
              <Square size={16} /> {isSidebarOpen ? 'Dừng lại' : ''}
            </button>
          </div>
          
          <div className="header-user">
            <span style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
              <UserIcon size={16} /> {user?.username}
            </span>
            <span className="credits">💎 Credit: {user?.credits ?? 0}</span>
            {user?.role === 'admin' && (
              <Link to="/admin" style={{ color: 'var(--accent)', display: 'flex', alignItems: 'center', gap: '4px' }}>
                <Settings size={16} /> Admin
              </Link>
            )}
            <a onClick={logout} style={{ color: 'var(--red)', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '4px' }}>
              <LogOut size={16} /> Thoát
            </a>
          </div>
        </header>

        {/* Content Area */}
        <div className="main-area fade-in">
          {/* ── Dashboard Stats (ONLY on Accounts Tab) ── */}
          {activeTab === 'accounts' && (
            <>
              <div className="upload-bar">
                <label>📂 Danh sách Accounts:</label>
                <button className="btn btn-upload" onClick={() => setShowAccountsModal(true)} style={{ gap: '6px' }}>
                  <Settings size={14} /> Quản lý Accounts
                </button>
                <span className="file-label muted">Tổng: {t.accounts_total ?? 0} tài khoản đã nạp</span>
              </div>

              <div className="cards">
                <div className="card cyan">
                  <div className="card-label">Tổng Tài Khoản</div>
                  <div className="card-value">{t.accounts_total ?? 0}</div>
                </div>
                <div className="card green">
                  <div className="card-label">Hoàn Thành</div>
                  <div className="card-value">{t.accounts_done ?? 0}</div>
                </div>
                <div className="card red">
                  <div className="card-label">Thất Bại</div>
                  <div className="card-value">{t.accounts_failed ?? 0}</div>
                </div>
                <div className="card yellow">
                  <div className="card-label">Tệp Đã Tải Lọc</div>
                  <div className="card-value">{t.images_total ?? 0}</div>
                </div>
                <div className="card purple">
                  <div className="card-label">Giấy Tờ Hợp Lệ</div>
                  <div className="card-value">{t.documents_found ?? 0}</div>
                </div>
              </div>
            </>
          )}

        {/* ── Accounts Tab ── */}
        {activeTab === 'accounts' && (
          <div className="section">
            <div className="section-title">Tiến Độ Quét Tài Khoản</div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Email</th><th>Trạng Thái</th><th>Proxy</th><th>Luồng Xử Lý</th>
                    <th>Tiến Độ (%)</th><th>Tổng Số Thư</th><th>Tệp Chứa Ảnh</th>
                    <th>Tệp Gần Nhất</th><th>Giải Trình Lỗi</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(accounts).map(([email, a]) => (
                    <tr key={email}>
                      <td className="mono small">{esc(email)}</td>
                      <td><Badge status={a.status} /></td>
                      <td className="mono small muted">{esc(a.proxy ?? '—')}</td>
                      <td className="small muted">{esc((a.thread ?? '').replace('worker_', 'W'))}</td>
                      <td><ProgressBar done={a.processed ?? 0} total={a.total_mail ?? 0} /></td>
                      <td className="mono">{a.processed ?? 0}/{a.total_mail ?? 0}</td>
                      <td className="mono yellow">{a.images_found ?? 0}</td>
                      <td className="small muted ellipsis" style={{ maxWidth: 150 }}>{esc(a.last_file)}</td>
                      <td className="small red ellipsis" style={{ maxWidth: 180 }}>{esc(a.error)}</td>
                    </tr>
                  ))}
                  {Object.keys(accounts).length === 0 && (
                    <tr><td colSpan={9} className="muted" style={{ textAlign: 'center', padding: 20 }}>Chưa có dữ liệu quét</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* ── Gallery Tab ── */}
        {activeTab === 'gallery' && (
          <GalleryTab gallery={gallery} />
        )}

        {/* ── AI Log Tab ── */}
        {activeTab === 'ai' && (
          <>
            <div className="section">
              <div className="section-title">Log Chẩn Đoán Của Mô Hình Trí Tuệ Nhân Tạo (AI Classifier)</div>
              <div className="log-box tall">
                {[...aiLogs].reverse().map((l, i) => {
                  const m = l.match(/^\[(.*?)\] (.*)/);
                  return (
                    <div key={i} className="log-line">
                      {m ? <><span className="log-time">{m[1]}</span>{esc(m[2])}</> : esc(l)}
                    </div>
                  );
                })}
              </div>
            </div>
            <div className="section">
              <div className="section-title">Log Máy Chủ (Thông Báo IMAP)</div>
              <div className="log-box">
                {logs.map((l, i) => (
                  <div key={i} className="log-line">
                    <span className="log-time">{l.t}</span>{esc(l.msg)}
                  </div>
                ))}
              </div>
            </div>
          </>
        )}

        {/* ── Proxy Tab ── */}
        {activeTab === 'proxies' && (
          <div className="section">
            <div className="upload-bar" style={{ marginBottom: '20px' }}>
              <label>🌐 Danh sách Proxy:</label>
              <button className="btn btn-upload" onClick={() => setShowProxiesModal(true)} style={{ gap: '6px' }}>
                <Settings size={14} /> Quản lý Proxy
              </button>
              <span className="file-label muted">Tổng: {proxies.length} proxies đang hoạt động</span>
            </div>

            <div className="section-title">Tình Trạng Dàn Proxies</div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>#</th><th>IP/Host:Cổng</th><th>Trạng Thái</th>
                    <th>Khoán Cho Thread</th><th>Số Tác Vụ</th><th>Lỗi</th>
                    <th>Ghi Chú Lỗi Mạng</th>
                  </tr>
                </thead>
                <tbody>
                  {proxies.map((p, i) => (
                    <tr key={p.id}>
                      <td className="mono muted">{i + 1}</td>
                      <td className="mono">{esc(p.host)}:{p.port}</td>
                      <td><Badge status={p.status} /></td>
                      <td className="mono small muted">{esc(p.used_by ?? '—')}</td>
                      <td className="mono">{p.requests}</td>
                      <td className={`mono ${p.errors > 0 ? 'red' : 'muted'}`}>{p.errors}</td>
                      <td className="small red ellipsis" style={{ maxWidth: 200 }}>{esc(p.last_error)}</td>
                    </tr>
                  ))}
                  {proxies.length === 0 && (
                    <tr><td colSpan={7} className="muted" style={{ textAlign: 'center', padding: 20 }}>Không có proxy</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        )}

      {/* ── Accounts Modal ── */}
      {showAccountsModal && (
        <AccountsModal
          onClose={() => setShowAccountsModal(false)}
          addLog={addLog}
          refreshUser={refreshUser}
        />
      )}

      {/* ── Proxies Modal ── */}
      {showProxiesModal && (
        <ProxiesModal
          onClose={() => setShowProxiesModal(false)}
          addLog={addLog}
        />
      )}
      </div>
    </div>
  </div>
  );
}


/* ══════════════════════════════════════════════════════════════
   AccountsModal — Quản lý danh sách accounts
   ══════════════════════════════════════════════════════════════ */

function AccountsModal({ onClose, addLog, refreshUser }) {
  const [accounts, setAccounts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [editIdx, setEditIdx] = useState(-1);
  const [editEmail, setEditEmail] = useState('');
  const [editPwd, setEditPwd] = useState('');
  const [newEmail, setNewEmail] = useState('');
  const [newPwd, setNewPwd] = useState('');
  const fileRef = useRef(null);

  // Load existing accounts on mount
  useEffect(() => {
    (async () => {
      try {
        const data = await apiGetAccounts();
        setAccounts(data.accounts || []);
      } catch (e) {
        console.error('Load accounts fail', e);
      }
      setLoading(false);
    })();
  }, []);

  // Handle file import (select + parse instantly)
  const handleFileImport = (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      const text = ev.target.result;
      const lines = text.split(/\r?\n/).map(l => l.trim()).filter(Boolean);
      const parsed = [];
      // Check if CSV with header
      const first = lines[0]?.toLowerCase() || '';
      const hasHeader = first.includes('email') && first.includes('password');
      const startIdx = hasHeader ? 1 : 0;
      for (let i = startIdx; i < lines.length; i++) {
        const line = lines[i];
        let email, pwd;
        if (line.includes(',')) {
          [email, pwd] = line.split(',').map(s => s.trim());
        } else if (line.includes(':')) {
          [email, ...pwd] = line.split(':');
          email = email.trim();
          pwd = pwd.join(':').trim();
        } else continue;
        if (email && pwd) parsed.push({ email, password: pwd });
      }
      if (parsed.length > 0) {
        setAccounts(parsed);
        addLog?.(`📂 Đã import ${parsed.length} accounts từ file ${file.name}`);
      } else {
        alert('Không tìm thấy account hợp lệ trong file!');
      }
    };
    reader.readAsText(file);
    e.target.value = '';
  };

  const handleDelete = (idx) => {
    setAccounts(prev => prev.filter((_, i) => i !== idx));
    if (editIdx === idx) setEditIdx(-1);
  };

  const startEdit = (idx) => {
    setEditIdx(idx);
    setEditEmail(accounts[idx].email);
    setEditPwd(accounts[idx].password);
  };

  const saveEdit = () => {
    if (!editEmail.trim() || !editPwd.trim()) return;
    setAccounts(prev => prev.map((a, i) =>
      i === editIdx ? { email: editEmail.trim(), password: editPwd.trim() } : a
    ));
    setEditIdx(-1);
  };

  const handleAddRow = () => {
    if (!newEmail.trim() || !newPwd.trim()) return;
    setAccounts(prev => [...prev, { email: newEmail.trim(), password: newPwd.trim() }]);
    setNewEmail('');
    setNewPwd('');
  };

  const handleSave = async () => {
    if (accounts.length === 0) {
      alert('Danh sách rỗng!');
      return;
    }
    setSaving(true);
    try {
      const data = await apiSaveAccounts(accounts);
      addLog?.(`✅ ${data.msg}`);
      refreshUser?.();
      onClose();
    } catch (e) {
      alert(e.message);
    }
    setSaving(false);
  };

  // Close on Escape
  useEffect(() => {
    const handleKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [onClose]);

  return (
    <div className="acc-modal-overlay" onClick={onClose}>
      <div className="acc-modal" onClick={e => e.stopPropagation()}>
        {/* Header */}
        <div className="acc-modal-header">
          <h2>📝 Quản Lý Danh Sách Accounts</h2>
          <button className="acc-modal-close" onClick={onClose}><X size={20} /></button>
        </div>

        {/* Toolbar */}
        <div className="acc-modal-toolbar">
          <input type="file" ref={fileRef} accept=".csv,.txt" onChange={handleFileImport} style={{ display: 'none' }} />
          <button className="btn btn-upload" onClick={() => fileRef.current?.click()} style={{ gap: '6px' }}>
            <Upload size={14} /> Import từ File (.csv/.txt)
          </button>
          <span className="muted" style={{ fontSize: '12px' }}>
            {accounts.length} accounts
          </span>
        </div>

        {/* Table */}
        <div className="acc-modal-table-wrap">
          {loading ? (
            <div style={{ textAlign: 'center', padding: '40px', color: 'var(--muted)' }}>Loading...</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th style={{ width: 40 }}>#</th>
                  <th>Email</th>
                  <th>Password</th>
                  <th style={{ width: 90 }}>Thao Tác</th>
                </tr>
              </thead>
              <tbody>
                {accounts.map((acc, i) => (
                  <tr key={i}>
                    <td className="mono muted">{i + 1}</td>
                    {editIdx === i ? (
                      <>
                        <td>
                          <input className="acc-edit-input" value={editEmail}
                            onChange={e => setEditEmail(e.target.value)}
                            onKeyDown={e => e.key === 'Enter' && saveEdit()}
                          />
                        </td>
                        <td>
                          <input className="acc-edit-input" value={editPwd}
                            onChange={e => setEditPwd(e.target.value)}
                            onKeyDown={e => e.key === 'Enter' && saveEdit()}
                          />
                        </td>
                        <td>
                          <button className="acc-action-btn green" onClick={saveEdit} title="Lưu">
                            <Save size={14} />
                          </button>
                          <button className="acc-action-btn" onClick={() => setEditIdx(-1)} title="Hủy">
                            <X size={14} />
                          </button>
                        </td>
                      </>
                    ) : (
                      <>
                        <td className="mono small">{acc.email}</td>
                        <td className="mono small muted">{acc.password.replace(/./g, '•').slice(0, 12)}…</td>
                        <td>
                          <button className="acc-action-btn" onClick={() => startEdit(i)} title="Sửa">
                            <Edit3 size={14} />
                          </button>
                          <button className="acc-action-btn red" onClick={() => handleDelete(i)} title="Xóa">
                            <Trash2 size={14} />
                          </button>
                        </td>
                      </>
                    )}
                  </tr>
                ))}
                {/* Add row */}
                <tr className="acc-add-row">
                  <td className="mono muted"><Plus size={14} /></td>
                  <td>
                    <input className="acc-edit-input" placeholder="email@libero.it"
                      value={newEmail} onChange={e => setNewEmail(e.target.value)}
                      onKeyDown={e => e.key === 'Enter' && handleAddRow()}
                    />
                  </td>
                  <td>
                    <input className="acc-edit-input" placeholder="password"
                      value={newPwd} onChange={e => setNewPwd(e.target.value)}
                      onKeyDown={e => e.key === 'Enter' && handleAddRow()}
                    />
                  </td>
                  <td>
                    <button className="acc-action-btn green" onClick={handleAddRow}
                      disabled={!newEmail.trim() || !newPwd.trim()} title="Thêm">
                      <Plus size={14} />
                    </button>
                  </td>
                </tr>
                {accounts.length === 0 && (
                  <tr>
                    <td colSpan={4} className="muted" style={{ textAlign: 'center', padding: 30 }}>
                      Chưa có account nào. Import file hoặc thêm thủ công ở trên.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          )}
        </div>

        {/* Footer */}
        <div className="acc-modal-footer">
          <button className="btn btn-upload" onClick={onClose}>Hủy</button>
          <button className="btn btn-start" onClick={handleSave} disabled={saving || accounts.length === 0}
            style={{ gap: '6px' }}>
            <Save size={14} /> {saving ? 'Đang lưu...' : `Lưu & Nạp (${accounts.length} accounts)`}
          </button>
        </div>
      </div>
    </div>
  );
}


/* ══════════════════════════════════════════════════════════════
   ProxiesModal — Quản lý danh sách proxy
   ══════════════════════════════════════════════════════════════ */

function ProxiesModal({ onClose, addLog }) {
  const [proxies, setProxies] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [editIdx, setEditIdx] = useState(-1);
  const [editData, setEditData] = useState({ host: '', port: '', username: '', password: '' });
  const [newData, setNewData] = useState({ host: '', port: '', username: '', password: '' });
  const fileRef = useRef(null);

  // Load existing proxies
  useEffect(() => {
    (async () => {
      try {
        const data = await apiGetProxies();
        setProxies(data.proxies || []);
      } catch (e) {
        console.error('Load proxies fail', e);
      }
      setLoading(false);
    })();
  }, []);

  // File import
  const handleFileImport = (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      const text = ev.target.result;
      const lines = text.split(/\r?\n/).map(l => l.trim()).filter(Boolean);
      const parsed = [];
      for (const line of lines) {
        if (line.startsWith('#')) continue;
        const parts = line.split(':');
        if (parts.length >= 4) {
          parsed.push({
            host: parts[0],
            port: parseInt(parts[1]) || 0,
            username: parts[2],
            password: parts[3],
          });
        }
      }
      if (parsed.length > 0) {
        setProxies(parsed);
        addLog?.(`🌐 Đã import ${parsed.length} proxies từ file ${file.name}`);
      } else {
        alert('Không tìm thấy proxy hợp lệ! Format: host:port:user:pass');
      }
    };
    reader.readAsText(file);
    e.target.value = '';
  };

  const handleDelete = (idx) => {
    setProxies(prev => prev.filter((_, i) => i !== idx));
    if (editIdx === idx) setEditIdx(-1);
  };

  const startEdit = (idx) => {
    setEditIdx(idx);
    const p = proxies[idx];
    setEditData({ host: p.host, port: String(p.port), username: p.username, password: p.password });
  };

  const saveEdit = () => {
    if (!editData.host.trim() || !editData.port) return;
    setProxies(prev => prev.map((p, i) =>
      i === editIdx ? { ...editData, port: parseInt(editData.port) || 0 } : p
    ));
    setEditIdx(-1);
  };

  const handleAddRow = () => {
    if (!newData.host.trim() || !newData.port) return;
    setProxies(prev => [...prev, { ...newData, port: parseInt(newData.port) || 0 }]);
    setNewData({ host: '', port: '', username: '', password: '' });
  };

  const handleSave = async () => {
    if (proxies.length === 0) { alert('Danh sách rỗng!'); return; }
    setSaving(true);
    try {
      const data = await apiSaveProxies(proxies);
      addLog?.(`✅ ${data.msg}`);
      onClose();
    } catch (e) {
      alert(e.message);
    }
    setSaving(false);
  };

  // Escape to close
  useEffect(() => {
    const handleKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [onClose]);

  return (
    <div className="acc-modal-overlay" onClick={onClose}>
      <div className="acc-modal" onClick={e => e.stopPropagation()} style={{ maxWidth: '900px' }}>
        {/* Header */}
        <div className="acc-modal-header">
          <h2>🌐 Quản Lý Danh Sách Proxy</h2>
          <button className="acc-modal-close" onClick={onClose}><X size={20} /></button>
        </div>

        {/* Toolbar */}
        <div className="acc-modal-toolbar">
          <input type="file" ref={fileRef} accept=".txt,.csv" onChange={handleFileImport} style={{ display: 'none' }} />
          <button className="btn btn-upload" onClick={() => fileRef.current?.click()} style={{ gap: '6px' }}>
            <Upload size={14} /> Import từ File (.txt)
          </button>
          <span className="muted" style={{ fontSize: '12px' }}>
            Format: host:port:user:pass — {proxies.length} proxies
          </span>
        </div>

        {/* Table */}
        <div className="acc-modal-table-wrap">
          {loading ? (
            <div style={{ textAlign: 'center', padding: '40px', color: 'var(--muted)' }}>Loading...</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th style={{ width: 36 }}>#</th>
                  <th>Host</th>
                  <th style={{ width: 80 }}>Port</th>
                  <th>Username</th>
                  <th>Password</th>
                  <th style={{ width: 90 }}>Thao Tác</th>
                </tr>
              </thead>
              <tbody>
                {proxies.map((p, i) => (
                  <tr key={i}>
                    <td className="mono muted">{i + 1}</td>
                    {editIdx === i ? (
                      <>
                        <td><input className="acc-edit-input" value={editData.host}
                          onChange={e => setEditData({ ...editData, host: e.target.value })}
                          onKeyDown={e => e.key === 'Enter' && saveEdit()} /></td>
                        <td><input className="acc-edit-input" value={editData.port} type="number"
                          onChange={e => setEditData({ ...editData, port: e.target.value })}
                          onKeyDown={e => e.key === 'Enter' && saveEdit()} /></td>
                        <td><input className="acc-edit-input" value={editData.username}
                          onChange={e => setEditData({ ...editData, username: e.target.value })}
                          onKeyDown={e => e.key === 'Enter' && saveEdit()} /></td>
                        <td><input className="acc-edit-input" value={editData.password}
                          onChange={e => setEditData({ ...editData, password: e.target.value })}
                          onKeyDown={e => e.key === 'Enter' && saveEdit()} /></td>
                        <td>
                          <button className="acc-action-btn green" onClick={saveEdit} title="Lưu"><Save size={14} /></button>
                          <button className="acc-action-btn" onClick={() => setEditIdx(-1)} title="Hủy"><X size={14} /></button>
                        </td>
                      </>
                    ) : (
                      <>
                        <td className="mono small">{p.host}</td>
                        <td className="mono small">{p.port}</td>
                        <td className="mono small muted">{p.username}</td>
                        <td className="mono small muted">{String(p.password).replace(/./g, '•').slice(0, 8)}</td>
                        <td>
                          <button className="acc-action-btn" onClick={() => startEdit(i)} title="Sửa"><Edit3 size={14} /></button>
                          <button className="acc-action-btn red" onClick={() => handleDelete(i)} title="Xóa"><Trash2 size={14} /></button>
                        </td>
                      </>
                    )}
                  </tr>
                ))}
                {/* Add row */}
                <tr className="acc-add-row">
                  <td className="mono muted"><Plus size={14} /></td>
                  <td><input className="acc-edit-input" placeholder="host" value={newData.host}
                    onChange={e => setNewData({ ...newData, host: e.target.value })}
                    onKeyDown={e => e.key === 'Enter' && handleAddRow()} /></td>
                  <td><input className="acc-edit-input" placeholder="port" value={newData.port} type="number"
                    onChange={e => setNewData({ ...newData, port: e.target.value })}
                    onKeyDown={e => e.key === 'Enter' && handleAddRow()} /></td>
                  <td><input className="acc-edit-input" placeholder="user" value={newData.username}
                    onChange={e => setNewData({ ...newData, username: e.target.value })}
                    onKeyDown={e => e.key === 'Enter' && handleAddRow()} /></td>
                  <td><input className="acc-edit-input" placeholder="password" value={newData.password}
                    onChange={e => setNewData({ ...newData, password: e.target.value })}
                    onKeyDown={e => e.key === 'Enter' && handleAddRow()} /></td>
                  <td>
                    <button className="acc-action-btn green" onClick={handleAddRow}
                      disabled={!newData.host.trim() || !newData.port} title="Thêm">
                      <Plus size={14} />
                    </button>
                  </td>
                </tr>
                {proxies.length === 0 && (
                  <tr>
                    <td colSpan={6} className="muted" style={{ textAlign: 'center', padding: 30 }}>
                      Chưa có proxy nào. Import file hoặc thêm thủ công ở trên.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          )}
        </div>

        {/* Footer */}
        <div className="acc-modal-footer">
          <button className="btn btn-upload" onClick={onClose}>Hủy</button>
          <button className="btn btn-start" onClick={handleSave} disabled={saving || proxies.length === 0}
            style={{ gap: '6px' }}>
            <Save size={14} /> {saving ? 'Đang lưu...' : `Lưu & Nạp (${proxies.length} proxies)`}
          </button>
        </div>
      </div>
    </div>
  );
}

const PER_PAGE = 24;

function GalleryTab({ gallery }) {
  const [lightbox, setLightbox] = useState(null);
  const [selectedEmail, setSelectedEmail] = useState(null);
  const [filter, setFilter] = useState('all');
  const [page, setPage] = useState(1);

  const [selectedPaths, setSelectedPaths] = useState(new Set());
  const [isDeleting, setIsDeleting] = useState(false);
  const [isDownloading, setIsDownloading] = useState(false);

  const handleClearAll = async () => {
    if (!window.confirm("BẠN CÓ CHẮC MUỐN XÓA TẤT CẢ ẢNH TRONG HỆ THỐNG?\nHành động này xóa sạch Data của mọi tài khoản và không thể hoàn tác!")) return;
    setIsDeleting(true);
    try {
      const res = await apiClearAllGallery();
      if(res.ok) alert(res.msg);
    } catch (err) {
      alert("Lỗi khi xóa CSDL ảnh!");
    }
    setIsDeleting(false);
  };

  const [isDownloadingAll, setIsDownloadingAll] = useState(false);

  const handleDownloadAllValid = async () => {
    // Thu thập tất cả path documents từ mọi email
    const allValidPaths = [];
    Object.entries(gallery).forEach(([slug, info]) => {
      (info.documents || []).forEach(f => {
        allValidPaths.push(`${slug}/documents/${f}`);
      });
    });
    if (allValidPaths.length === 0) {
      alert('Không có ảnh hợp lệ nào để tải!');
      return;
    }
    setIsDownloadingAll(true);
    try {
      const blob = await apiDownloadGallery(allValidPaths);
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `all_valid_documents.zip`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
    } catch (err) {
      alert('Tải xuống thất bại!');
    }
    setIsDownloadingAll(false);
  };

  // Build email list with counts
  const emailData = useMemo(() => {
    return Object.entries(gallery).map(([slug, info]) => {
      const raw = info.raw || [];
      const docs = info.documents || [];
      return { slug, rawCount: raw.length, docCount: docs.length, total: raw.length + docs.length };
    }).sort((a, b) => b.total - a.total);
  }, [gallery]);

  // Auto-select first email
  useEffect(() => {
    if (emailData.length > 0 && !selectedEmail) {
      setSelectedEmail(emailData[0].slug);
    }
  }, [emailData]);

  // Images for selected email
  const currentImages = useMemo(() => {
    if (!selectedEmail || !gallery[selectedEmail]) return [];
    const info = gallery[selectedEmail];
    const list = [];
    (info.documents || []).forEach(f => {
      const path = `${selectedEmail}/documents/${f}`;
      list.push({ url: getMediaUrl(path), path, file: f, type: 'valid' });
    });
    (info.raw || []).forEach(f => {
      const path = `${selectedEmail}/raw/${f}`;
      list.push({ url: getMediaUrl(path), path, file: f, type: 'raw' });
    });
    return list;
  }, [gallery, selectedEmail]);

  // Filter
  const filtered = useMemo(() => {
    if (filter === 'valid') return currentImages.filter(i => i.type === 'valid');
    if (filter === 'raw') return currentImages.filter(i => i.type === 'raw');
    return currentImages;
  }, [currentImages, filter]);

  // Current email stats
  const curValid = currentImages.filter(i => i.type === 'valid').length;
  const curRaw = currentImages.filter(i => i.type === 'raw').length;

  // Pagination
  const totalPages = Math.max(1, Math.ceil(filtered.length / PER_PAGE));
  const safePage = Math.min(page, totalPages);
  const pageItems = filtered.slice((safePage - 1) * PER_PAGE, safePage * PER_PAGE);

  // Reset page and selection on email/filter change
  useEffect(() => { 
    setPage(1); 
    setSelectedPaths(new Set());
  }, [selectedEmail, filter]);

  const currentSelectionPaths = Array.from(selectedPaths);
  const isAllSelected = filtered.length > 0 && selectedPaths.size === filtered.length;

  const toggleSelection = (e, path) => {
    e.stopPropagation();
    setSelectedPaths(prev => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  const toggleSelectAll = () => {
    if (isAllSelected) {
      setSelectedPaths(new Set());
    } else {
      setSelectedPaths(new Set(filtered.map(i => i.path)));
    }
  };

  const handleDownload = async () => {
    if (currentSelectionPaths.length === 0) return;
    setIsDownloading(true);
    try {
      const blob = await apiDownloadGallery(currentSelectionPaths);
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `libero_images_${selectedEmail}.zip`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
      setSelectedPaths(new Set());
    } catch (err) {
      alert("Tải xuống thất bại!");
    }
    setIsDownloading(false);
  };

  const handleDelete = async () => {
    if (currentSelectionPaths.length === 0) return;
    if (!window.confirm(`Bạn có chắc muốn xóa ${currentSelectionPaths.length} ảnh đã chọn? LƯU Ý: Hành động này không thể hoàn tác!`)) return;
    
    setIsDeleting(true);
    try {
      await apiDeleteGallery(currentSelectionPaths);
      setSelectedPaths(new Set());
      // Refresh will be handled by auto-poll from Dashboard
    } catch (err) {
      alert("Xóa ảnh thất bại!");
    }
    setIsDeleting(false);
  };

  // Lightbox
  const openLightbox = (idx) => {
    const globalIdx = (safePage - 1) * PER_PAGE + idx;
    setLightbox({ ...filtered[globalIdx], filteredList: filtered, currentIndex: globalIdx });
  };
  const closeLightbox = () => setLightbox(null);
  const navigateLightbox = (dir) => {
    if (!lightbox) return;
    const newIdx = lightbox.currentIndex + dir;
    if (newIdx < 0 || newIdx >= lightbox.filteredList.length) return;
    const item = lightbox.filteredList[newIdx];
    setLightbox({ ...item, filteredList: lightbox.filteredList, currentIndex: newIdx });
  };

  useEffect(() => {
    if (!lightbox) return;
    const handleKey = (e) => {
      if (e.key === 'Escape') closeLightbox();
      if (e.key === 'ArrowLeft') navigateLightbox(-1);
      if (e.key === 'ArrowRight') navigateLightbox(1);
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [lightbox]);

  const buildPageRange = () => {
    const range = [];
    const delta = 2;
    const left = Math.max(2, safePage - delta);
    const right = Math.min(totalPages - 1, safePage + delta);
    range.push(1);
    if (left > 2) range.push('...');
    for (let i = left; i <= right; i++) range.push(i);
    if (right < totalPages - 1) range.push('...');
    if (totalPages > 1) range.push(totalPages);
    return range;
  };

  const isEmpty = emailData.length === 0;

  return (
    <>

        {isEmpty ? (
          <div className="gallery-empty">
            <div className="gallery-empty-icon">📂</div>
            <div className="gallery-empty-text">Chưa có ảnh nào được thu thập</div>
            <div className="gallery-empty-hint">Hệ thống sẽ tự động hiển thị ảnh khi quá trình quét bắt đầu</div>
          </div>
        ) : (
          <>
            {/* Master-Detail Layout */}
            <div className="gal-layout">
              {/* ── Sidebar: Email List ── */}
              <div className="gal-sidebar">
                <div className="gal-sidebar-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '6px' }}>
                  <span>📧 Tài khoản Email</span>
                  <div style={{ display: 'flex', gap: '4px' }}>
                    <button onClick={handleDownloadAllValid} disabled={isDownloadingAll} style={{
                      background: 'var(--accent)', color: 'white', border: 'none', borderRadius: '4px',
                      padding: '4px 8px', fontSize: '12px', cursor: 'pointer', opacity: isDownloadingAll ? 0.6 : 1
                    }} title="Tải toàn bộ ảnh hợp lệ (documents)">
                      <Download size={12} style={{ verticalAlign: 'middle', marginRight: '3px' }} />
                      {isDownloadingAll ? 'Đang nén...' : 'Tải Hợp Lệ'}
                    </button>
                    <button onClick={handleClearAll} disabled={isDeleting} style={{
                      background: 'var(--red)', color: 'white', border: 'none', borderRadius: '4px',
                      padding: '4px 8px', fontSize: '12px', cursor: 'pointer'
                    }} title="Xóa toàn bộ CSDL Ảnh">🗑️ Xóa DB</button>
                  </div>
                </div>
                <div className="gal-sidebar-list">
                  {emailData.map(e => (
                    <button
                      key={e.slug}
                      className={`gal-email-item ${selectedEmail === e.slug ? 'active' : ''}`}
                      onClick={() => { setSelectedEmail(e.slug); setFilter('all'); }}
                      title={e.slug}
                    >
                      <span className="gal-email-name">{e.slug}</span>
                      <span className="gal-email-counts">
                        {e.docCount > 0 && (
                          <span className="gal-email-badge valid">{e.docCount} ✓</span>
                        )}
                        <span className="gal-email-badge total">{e.total}</span>
                      </span>
                    </button>
                  ))}
                </div>
              </div>

              {/* ── Content: Images for Selected Email ── */}
              <div className="gal-content">
                {selectedEmail ? (
                  <>
                    {/* Content Header */}
                    <div className="gal-content-header">
                      <div className="gal-content-title">
                        <span className="gal-content-email">📧 {selectedEmail}</span>
                        <span className="gal-content-total">{currentImages.length} ảnh</span>
                      </div>
                      <div className="gal-content-filters">
                        {[
                          ['all', `Tất cả (${currentImages.length})`],
                          ['valid', `✅ Hợp lệ (${curValid})`],
                          ['raw', `📷 Gốc (${curRaw})`],
                        ].map(([key, label]) => (
                          <button
                            key={key}
                            className={`gal-filter-btn ${filter === key ? 'active' : ''} ${key}`}
                            onClick={() => setFilter(key)}
                          >
                            {label}
                          </button>
                        ))}
                      </div>
                    </div>

                    {/* Results info / Bulk Actions */}
                    <div className="gal-results-info" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <div>
                        Hiển thị {pageItems.length} / {filtered.length} ảnh
                        {filtered.length === 0 && ' — Không có ảnh loại này'}
                      </div>
                      
                      {filtered.length > 0 && (
                        <div className="gal-bulk-actions" style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                          <button 
                            className="btn btn-upload" 
                            style={{ padding: '6px 12px', fontSize: '12px' }}
                            onClick={toggleSelectAll}
                          >
                            <CheckSquare size={14} /> {isAllSelected ? "Bỏ chọn tất cả" : "Chọn tất cả"} ({selectedPaths.size})
                          </button>
                          
                          {selectedPaths.size > 0 && (
                            <>
                              <button 
                                className="btn btn-start" 
                                style={{ padding: '6px 12px', fontSize: '12px', background: 'var(--accent)', color: '#fff', borderColor: 'var(--accent)' }}
                                onClick={handleDownload}
                                disabled={isDownloading}
                              >
                                <Download size={14} /> {isDownloading ? 'Đang nén...' : 'Tải xuống ZIP'}
                              </button>
                              <button 
                                className="btn btn-stop" 
                                style={{ padding: '6px 12px', fontSize: '12px' }}
                                onClick={handleDelete}
                                disabled={isDeleting}
                              >
                                <Trash2 size={14} /> {isDeleting ? 'Đang xóa...' : 'Xóa ảnh'}
                              </button>
                            </>
                          )}
                        </div>
                      )}
                    </div>

                    {/* Image Grid */}
                    {pageItems.length > 0 && (
                      <div className="gal-grid">
                        {pageItems.map((img, i) => (
                          <div
                            key={`${img.type}-${img.file}`}
                            className={`gal-item ${img.type} ${selectedPaths.has(img.path) ? 'selected' : ''}`}
                            onClick={() => openLightbox(i)}
                          >
                            <div className="gal-item-checkbox" onClick={(e) => toggleSelection(e, img.path)}>
                              {selectedPaths.has(img.path) && <CheckSquare size={18} color="var(--accent)" />}
                              {!selectedPaths.has(img.path) && <Square size={18} color="rgba(255,255,255,0.7)" />}
                            </div>
                            <div className="gal-item-img-wrap">
                              {img.file.toLowerCase().endsWith('.pdf') ? (
                                <div className="gal-pdf-placeholder">
                                  <span style={{ fontSize: '40px' }}>📄</span>
                                  <span style={{ marginTop: '8px', fontSize: '12px', fontWeight: 'bold' }}>PDF DOC</span>
                                </div>
                              ) : (
                                <img
                                  src={img.url}
                                  alt={img.file}
                                  loading="lazy"
                                  onError={(e) => {
                                    e.target.src = '';
                                    e.target.parentElement.classList.add('gal-item-broken');
                                  }}
                                />
                              )}
                              <span className={`gal-item-badge ${img.type}`}>
                                {img.type === 'valid' ? 'Giấy Tờ Hợp Lệ' : 'Ảnh Gốc'}
                              </span>
                              <div className="gal-item-hover">
                                <span>🔍 Xem</span>
                              </div>
                            </div>
                            <div className="gal-item-info">
                              <div className="gal-item-file" title={img.file}>{img.file}</div>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}

                    {/* Pagination */}
                    {totalPages > 1 && (
                      <div className="gal-pagination">
                        <button className="gal-page-btn" disabled={safePage <= 1} onClick={() => setPage(safePage - 1)}>
                          ‹ Trước
                        </button>
                        <div className="gal-page-nums">
                          {buildPageRange().map((p, i) =>
                            p === '...' ? (
                              <span key={`dots-${i}`} className="gal-page-dots">…</span>
                            ) : (
                              <button
                                key={p}
                                className={`gal-page-num ${safePage === p ? 'active' : ''}`}
                                onClick={() => setPage(p)}
                              >
                                {p}
                              </button>
                            )
                          )}
                        </div>
                        <button className="gal-page-btn" disabled={safePage >= totalPages} onClick={() => setPage(safePage + 1)}>
                          Sau ›
                        </button>
                      </div>
                    )}
                  </>
                ) : (
                  <div className="gal-content-empty">
                    <span>👈 Chọn một email để xem ảnh</span>
                  </div>
                )}
              </div>
            </div>
          </>
        )}

      {/* Lightbox */}
      {lightbox && (
        <div className="lightbox-overlay" onClick={closeLightbox}>
          <div className="lightbox-container" onClick={(e) => e.stopPropagation()}>
            <button className="lightbox-close" onClick={closeLightbox}>✕</button>
            {lightbox.currentIndex > 0 && (
              <button className="lightbox-nav prev" onClick={() => navigateLightbox(-1)}>‹</button>
            )}
            {lightbox.currentIndex < lightbox.filteredList.length - 1 && (
              <button className="lightbox-nav next" onClick={() => navigateLightbox(1)}>›</button>
            )}
            <div className="lightbox-image-wrap">
              {lightbox.file.toLowerCase().endsWith('.pdf') ? (
                <iframe src={lightbox.url} title={lightbox.file} style={{ width: '100%', height: '100%', border: 'none' }} />
              ) : (
                <img src={lightbox.url} alt={lightbox.file} />
              )}
            </div>
            <div className="lightbox-info">
              <span className={`lightbox-type-badge ${lightbox.type}`}>
                {lightbox.type === 'valid' ? '✅ Giấy Tờ Hợp Lệ' : '📷 Ảnh Gốc'}
              </span>
              <span className="lightbox-filename" title={lightbox.file}>{lightbox.file}</span>
              <span className="lightbox-email">📧 {selectedEmail}</span>
              <span className="lightbox-counter">
                {lightbox.currentIndex + 1} / {lightbox.filteredList.length}
              </span>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

function capitalize(s) {
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : '';
}
