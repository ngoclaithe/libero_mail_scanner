import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { apiGetState, apiStart, apiStop, apiUploadAccounts, apiUploadProxies, apiGetGallery, getMediaUrl, apiDeleteGallery, apiDownloadGallery } from '../api';
import {
  Menu, Mail, Image as ImageIcon, Activity, Globe,
  Play, Square, LogOut, Settings, User as UserIcon,
  Trash2, Download, CheckSquare, X, ChevronLeft, ChevronRight
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
  const [file, setFile] = useState(null);
  const [uploadStatus, setUploadStatus] = useState(null);
  const [uploading, setUploading] = useState(false);
  const prevStateRef = useRef({});
  const fileInputRef = useRef(null);
  const proxyFileInputRef = useRef(null);

  const [proxyFile, setProxyFile] = useState(null);
  const [uploadProxyStatus, setUploadProxyStatus] = useState(null);
  const [uploadingProxy, setUploadingProxy] = useState(false);

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

  const handleFileChange = (e) => {
    const f = e.target.files[0];
    setFile(f || null);
    setUploadStatus(null);
  };

  const handleUpload = async () => {
    if (!file) return;
    setUploading(true);
    try {
      const data = await apiUploadAccounts(file);
      if (data.ok) {
        setUploadStatus({ type: 'ok', msg: `✓ ${data.msg}` });
        addLog(`📂 Uploaded: ${data.msg} (${(data.preview || []).slice(0, 3).join(', ')})`);
        refreshUser();
      } else {
        setUploadStatus({ type: 'err', msg: `✗ ${data.msg}` });
      }
    } catch (e) {
      addLog('⚠ Lỗi tải lên: ' + e.message);
    }
    setUploading(false);
  };

  const handleProxyFileChange = (e) => {
    const f = e.target.files[0];
    setProxyFile(f || null);
    setUploadProxyStatus(null);
  };

  const handleProxyUpload = async () => {
    if (!proxyFile) return;
    setUploadingProxy(true);
    try {
      const data = await apiUploadProxies(proxyFile);
      if (data.ok) {
        setUploadProxyStatus({ type: 'ok', msg: `✓ ${data.msg} (${data.count} proxies)` });
        addLog(`🌐 Uploaded Proxies: ${data.msg} (${data.count} proxies)`);
      } else {
        setUploadProxyStatus({ type: 'err', msg: `✗ ${data.msg}` });
      }
    } catch (e) {
      addLog('⚠ Lỗi tải proxy: ' + e.message);
    }
    setUploadingProxy(false);
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
                <label>📂 Danh sách Accounts (.csv/.txt):</label>
                <input
                  type="file"
                  ref={fileInputRef}
                  accept=".csv,.txt"
                  onChange={handleFileChange}
                  style={{ display: 'none' }}
                />
                <button className="btn btn-upload" onClick={() => fileInputRef.current?.click()}>
                  Chọn File
                </button>
                <span className="file-label">{file ? file.name : 'Chưa chọn tệp'}</span>
                <button className="btn btn-upload" onClick={handleUpload} disabled={!file || uploading}>
                  {uploading ? 'Đang nạp...' : '⬆ Tải lên'}
                </button>
                {uploadStatus && (
                  <span className={`upload-status show ${uploadStatus.type}`}>
                    {uploadStatus.msg}
                  </span>
                )}
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
              <label>🌐 Danh sách Proxy (.txt):</label>
              <input
                type="file"
                ref={proxyFileInputRef}
                accept=".txt"
                onChange={handleProxyFileChange}
                style={{ display: 'none' }}
              />
              <button className="btn btn-upload" onClick={() => proxyFileInputRef.current?.click()}>
                Chọn File Proxy
              </button>
              <span className="file-label">{proxyFile ? proxyFile.name : 'Mặc định (SAR97653.txt)'}</span>
              <button className="btn btn-upload" onClick={handleProxyUpload} disabled={!proxyFile || uploadingProxy}>
                {uploadingProxy ? 'Đang nạp...' : '⬆ Tải lên Proxy'}
              </button>
              {uploadProxyStatus && (
                <span className={`upload-status show ${uploadProxyStatus.type}`}>
                  {uploadProxyStatus.msg}
                </span>
              )}
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
                <div className="gal-sidebar-header">📧 Tài khoản Email</div>
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
