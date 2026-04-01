import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { apiGetAdminUsers, apiGetAdminLogs, apiCreateUser, apiUpdateCredits, apiGetCaptchaKey, apiSetCaptchaKey } from '../api';

export default function Admin() {
  const { logout } = useAuth();
  const [users, setUsers] = useState([]);
  const [logs, setLogs] = useState([]);
  const [createForm, setCreateForm] = useState({ username: '', password: '', credits: 0 });
  const [createMsg, setCreateMsg] = useState(null);
  const [createErr, setCreateErr] = useState(null);

  const [captchaKey, setCaptchaKey] = useState('');
  const [captchaStatus, setCaptchaStatus] = useState({ configured: false, key_preview: '' });
  const [captchaMsg, setCaptchaMsg] = useState(null);
  const [captchaErr, setCaptchaErr] = useState(null);

  const loadData = async () => {
    try {
      const [u, l, cap] = await Promise.all([apiGetAdminUsers(), apiGetAdminLogs(), apiGetCaptchaKey()]);
      setUsers(u);
      setLogs(l);
      setCaptchaStatus(cap);
    } catch (e) {
      console.error('Admin data load failed', e);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const handleCreateUser = async (e) => {
    e.preventDefault();
    setCreateMsg(null);
    setCreateErr(null);
    try {
      const data = await apiCreateUser(createForm.username, createForm.password, createForm.credits);
      if (data.ok) {
        setCreateMsg(data.msg);
        setCreateForm({ username: '', password: '', credits: 0 });
        loadData();
      } else {
        setCreateErr(data.detail || data.msg || 'Lỗi tạo người dùng');
      }
    } catch (e) {
      setCreateErr(e.message);
    }
  };

  const handleSaveCaptcha = async (e) => {
    e.preventDefault();
    setCaptchaMsg(null);
    setCaptchaErr(null);
    try {
      const data = await apiSetCaptchaKey(captchaKey);
      if (data.ok) {
        setCaptchaMsg(data.msg);
        setCaptchaKey('');
        loadData();
      } else {
        setCaptchaErr(data.msg || 'Lỗi lưu API Key');
      }
    } catch (e) {
      setCaptchaErr(e.message);
    }
  };

  const handleUpdateCredits = async (userId, amount, action) => {
    try {
      await apiUpdateCredits(userId, amount, action);
      loadData();
    } catch (e) {
      console.error('Update credits failed', e);
    }
  };

  return (
    <div className="admin-page fade-in">
            <div className="admin-header">
        <h1>👑 Admin Panel</h1>
        <div className="admin-header-nav">
          <Link to="/">← Quay lại Dashboard</Link>
          <a onClick={logout} style={{ color: 'var(--red)', cursor: 'pointer' }}>Đăng xuất</a>
        </div>
      </div>

            <div className="admin-card">
        <h2>Tạo Người Dùng Mới</h2>
        <form onSubmit={handleCreateUser} className="admin-form">
          <div className="admin-form-group">
            <label>Tài khoản</label>
            <input
              type="text"
              value={createForm.username}
              onChange={(e) => setCreateForm(f => ({ ...f, username: e.target.value }))}
              required
              style={{ width: 200 }}
            />
          </div>
          <div className="admin-form-group">
            <label>Mật khẩu</label>
            <input
              type="text"
              value={createForm.password}
              onChange={(e) => setCreateForm(f => ({ ...f, password: e.target.value }))}
              required
              style={{ width: 200 }}
            />
          </div>
          <div className="admin-form-group">
            <label>Credits Khởi tạo</label>
            <input
              type="number"
              value={createForm.credits}
              onChange={(e) => setCreateForm(f => ({ ...f, credits: parseInt(e.target.value) || 0 }))}
              style={{ width: 120 }}
            />
          </div>
          <button type="submit" className="btn btn-green" style={{ padding: '8px 16px', height: 38 }}>
            + Thêm User
          </button>
        </form>
        {createErr && <div className="msg-err">⚠ {createErr}</div>}
        {createMsg && <div className="msg-ok">✅ {createMsg}</div>}
      </div>

            <div className="admin-card">
        <h2>Cấu hình Giải mã Captcha (Capsolver)</h2>
        <div style={{ marginBottom: 15 }}>
          Trạng thái: {captchaStatus?.configured ? <span style={{color: 'var(--green)', fontWeight: 600}}>Đã cấu hình ({captchaStatus?.key_preview})</span> : <span style={{color: 'var(--red)', fontWeight: 600}}>Chưa cấu hình</span>}
        </div>
        <form onSubmit={handleSaveCaptcha} className="admin-form">
          <div className="admin-form-group">
            <label>Capsolver API Key</label>
            <input
              type="text"
              placeholder="CAI-..."
              value={captchaKey}
              onChange={(e) => setCaptchaKey(e.target.value)}
              required
              style={{ width: 350 }}
            />
          </div>
          <button type="submit" className="btn btn-green" style={{ padding: '8px 16px', height: 38 }}>
            Lưu & Kiểm tra số dư
          </button>
        </form>
        {captchaErr && <div className="msg-err">⚠ {captchaErr}</div>}
        {captchaMsg && <div className="msg-ok">✅ {captchaMsg}</div>}
      </div>

            <div className="admin-card">
        <h2>Quản lý Credit & Người dùng</h2>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Username</th>
                <th>Role</th>
                <th>Credits (Mails)</th>
                <th>Thao tác</th>
              </tr>
            </thead>
            <tbody>
              {users.map(u => (
                <UserRow key={u.id} user={u} onUpdate={handleUpdateCredits} />
              ))}
            </tbody>
          </table>
        </div>
      </div>

            <div className="admin-card">
        <h2>Nhật ký truy cập IP (IP Logs)</h2>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>ID Log</th>
                <th>Thời gian</th>
                <th>Người dùng</th>
                <th>IP Address</th>
                <th>Endpoint</th>
              </tr>
            </thead>
            <tbody>
              {logs.map(log => (
                <tr key={log.id}>
                  <td className="muted">#{log.id}</td>
                  <td className="muted">{log.created_at}</td>
                  <td style={{ fontWeight: 600 }}>{log.username || 'Khách chưa đăng nhập'}</td>
                  <td className="mono">{log.ip}</td>
                  <td className="mono" style={{ color: 'var(--accent)' }}>{log.endpoint}</td>
                </tr>
              ))}
              {logs.length === 0 && (
                <tr><td colSpan={5} className="muted" style={{ textAlign: 'center', padding: 20 }}>Chưa có log</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function UserRow({ user, onUpdate }) {
  const [amount, setAmount] = useState(1000);
  const [action, setAction] = useState('add');

  const handleSubmit = (e) => {
    e.preventDefault();
    onUpdate(user.id, amount, action);
  };

  return (
    <tr>
      <td>{user.id}</td>
      <td>{user.username}</td>
      <td><span className={`badge badge-${user.role}`}>{user.role}</span></td>
      <td style={{ fontWeight: 600, fontSize: 16, color: 'var(--green)' }}>{user.credits}</td>
      <td>
        <form onSubmit={handleSubmit} className="inline-form">
          <select value={action} onChange={(e) => setAction(e.target.value)}>
            <option value="add">Cộng thêm (+)</option>
            <option value="set">Cài đặt thành (=)</option>
          </select>
          <input
            type="number"
            value={amount}
            onChange={(e) => setAmount(parseInt(e.target.value) || 0)}
            min={-999999}
            max={9999999}
            required
          />
          <button type="submit" className="btn btn-green">Cập nhật</button>
        </form>
      </td>
    </tr>
  );
}
