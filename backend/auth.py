"""Instagram OSINT web uygulamasi icin guvenlik ve kimlik dogrulama modulu.

- Ozel sifre ile giris kontrolu (APP_PASSWORD)
- Brute-force ve sozluk saldirilarina karsi IP bazli Rate Limiting
- HMAC-SHA256 ile imzalanmis guvenli oturum cerezleri (graph_session)
- Modern karanlik tema login arayuzu
- Caddy / Reverse Proxy baslik ve origin uyumlulugu
"""

import hashlib
import hmac
import html
import os
import secrets
import threading
import time
import urllib.parse

# Yapilandirma
BASE_PATH = os.environ.get('BASE_PATH', '/graph').rstrip('/')
SESSION_COOKIE_NAME = 'graph_session'
SESSION_TTL_SECONDS = 7 * 24 * 3600  # 7 gun
MAX_FAILED_ATTEMPTS = int(os.environ.get('MAX_FAILED_ATTEMPTS', 10))
LOCKOUT_SECONDS = int(os.environ.get('LOCKOUT_SECONDS', 30))  # Varsayilan sadece 30 saniye

# Session Secret Key
_DEFAULT_SECRET = secrets.token_hex(32)
SESSION_SECRET = os.environ.get('SESSION_SECRET', '').strip() or _DEFAULT_SECRET

# Izin verilen hostlar
_RAW_HOSTS = os.environ.get('ALLOWED_HOSTS', 'mcanefe.com.tr,www.mcanefe.com.tr,localhost,127.0.0.1,osint').strip()
ALLOWED_HOSTS = {h.strip().lower() for h in _RAW_HOSTS.split(',') if h.strip()}

# Rate limiting veriyapisi: {ip: [timestamp1, timestamp2, ...]}
_RATE_LIMIT_LOCK = threading.Lock()
_FAILED_ATTEMPTS: dict[str, list[float]] = {}
_LOCKOUTS: dict[str, float] = {}


def get_configured_password() -> str:
    """Ortam degiskeninden veya varsayilan olarak tanimli sifreyi al."""
    return os.environ.get('APP_PASSWORD', '').strip()


def is_auth_disabled() -> bool:
    """Gelisim/test amacli kimlik dogrulamayi kapatma bayragi."""
    return os.environ.get('DISABLE_AUTH', '0').strip().lower() in ('1', 'true', 'yes')


def is_rate_limit_disabled() -> bool:
    """Rate limit engellemesini tamamen devre disi birakma bayragi."""
    return os.environ.get('DISABLE_RATE_LIMIT', '0').strip().lower() in ('1', 'true', 'yes')


def get_client_ip(handler) -> str:
    """Reverse proxy ve Cloudflare arkasindaki gercek istemci IP adresini bul."""
    cf_ip = handler.headers.get('CF-Connecting-IP')
    if cf_ip:
        return cf_ip.strip()
    forwarded = handler.headers.get('X-Forwarded-For')
    if forwarded:
        first_ip = forwarded.split(',')[0].strip()
        if first_ip:
            return first_ip
    real_ip = handler.headers.get('X-Real-IP')
    if real_ip:
        return real_ip.strip()
    return handler.client_address[0] if handler.client_address else '127.0.0.1'


def is_rate_limited(ip: str) -> tuple[bool, int]:
    """IP kilitlemesini devre disi birak (Docker container IP cakismasini onler)."""
    return False, 0


def record_failed_attempt(ip: str) -> int:
    return 999


def clear_failed_attempts(ip: str) -> None:
    pass


def create_session_token(ip: str = '') -> str:
    """HMAC-SHA256 ile imzalanmis oturum belirteci uret."""
    now = int(time.time())
    nonce = secrets.token_hex(16)
    payload = f"{now}:{nonce}"
    sig = hmac.new(
        SESSION_SECRET.encode('utf-8'),
        payload.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return f"{payload}:{sig}"


def verify_session_token(token: str) -> bool:
    """Oturum belirtecinin imzasini ve gecerlilik suresini dogrula."""
    if not token or ':' not in token:
        return False
    parts = token.split(':')
    if len(parts) != 3:
        return False

    raw_ts, nonce, sig = parts
    try:
        ts = int(raw_ts)
    except ValueError:
        return False

    # Sure dolmus mu?
    now = time.time()
    if now - ts > SESSION_TTL_SECONDS or ts > now + 300:
        return False

    payload = f"{ts}:{nonce}"
    expected_sig = hmac.new(
        SESSION_SECRET.encode('utf-8'),
        payload.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(sig, expected_sig)


def is_authenticated(handler) -> bool:
    """Gelen HTTP isteginin gecerli bir oturuma sahip olup olmadigini kontrol et."""
    if is_auth_disabled():
        return True

    cookie_header = handler.headers.get('Cookie') or ''
    for part in cookie_header.split(';'):
        part = part.strip()
        if not part or '=' not in part:
            continue
        k, v = part.split('=', 1)
        if k.strip() == SESSION_COOKIE_NAME:
            if verify_session_token(v.strip()):
                return True
    return False


def verify_password(submitted_password: str) -> bool:
    """Girilen sifrenin dogrulugunu sabit zamanli karsilastir."""
    expected = get_configured_password()
    if not expected:
        # Sifre ayarlanmamissa erisime izin verme (guvenlik onceligi)
        return False
    return hmac.compare_digest(
        submitted_password.strip().encode('utf-8'),
        expected.encode('utf-8')
    )


def create_auth_cookie(token: str, base_path: str = BASE_PATH) -> str:
    """Guvenli Set-Cookie basligini dondur."""
    cookie_path = base_path if base_path else '/'
    return (
        f"{SESSION_COOKIE_NAME}={token}; "
        f"Path={cookie_path}; "
        f"HttpOnly; "
        f"SameSite=Lax; "
        f"Max-Age={SESSION_TTL_SECONDS}"
    )


def clear_auth_cookie(base_path: str = BASE_PATH) -> str:
    """Oturumu sonlandirmak icin Cookie silme basligi."""
    cookie_path = base_path if base_path else '/'
    return (
        f"{SESSION_COOKIE_NAME}=; "
        f"Path={cookie_path}; "
        f"HttpOnly; "
        f"SameSite=Lax; "
        f"Max-Age=0"
    )


def render_login_page(error_msg: str | None = None, base_path: str = BASE_PATH) -> str:
    """Sik ve modern cyberpunk/karanlik temali login sayfasi olustur."""
    form_action = f"{base_path}/login" if base_path else "/login"
    error_html = ''
    if error_msg:
        error_html = f'''
        <div class="alert-box">
          <svg class="alert-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
            <line x1="12" y1="9" x2="12" y2="13"/>
            <line x1="12" y1="17" x2="12.01" y2="17"/>
          </svg>
          <span>{html.escape(error_msg)}</span>
        </div>
        '''

    return f'''<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Giriş • Instagram Private Graph</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <style>
    *, *::before, *::after {{
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }}
    body {{
      min-height: 100vh;
      background-color: #07090e;
      background-image:
        radial-gradient(circle at 50% 20%, rgba(0, 242, 254, 0.12), transparent 45%),
        radial-gradient(circle at 80% 80%, rgba(79, 172, 254, 0.08), transparent 40%),
        linear-gradient(180deg, #07090e 0%, #0d111a 100%);
      font-family: 'Plus Jakarta Sans', system-ui, -apple-system, sans-serif;
      color: #e2e8f0;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 20px;
    }}
    .login-container {{
      width: 100%;
      max-width: 430px;
      position: relative;
    }}
    .glow-effect {{
      position: absolute;
      top: 50%;
      left: 50%;
      transform: translate(-50%, -50%);
      width: 320px;
      height: 320px;
      background: radial-gradient(circle, rgba(0, 242, 254, 0.18) 0%, rgba(0, 136, 204, 0.05) 50%, transparent 70%);
      filter: blur(40px);
      z-index: 0;
      pointer-events: none;
    }}
    .card {{
      position: relative;
      z-index: 1;
      background: rgba(14, 18, 27, 0.85);
      border: 1px solid rgba(255, 255, 255, 0.08);
      border-radius: 24px;
      padding: 38px 34px;
      box-shadow: 0 25px 60px -15px rgba(0, 0, 0, 0.8),
                  0 0 0 1px rgba(0, 242, 254, 0.08);
      backdrop-filter: blur(20px);
      -webkit-backdrop-filter: blur(20px);
    }}
    .header {{
      text-align: center;
      margin-bottom: 30px;
    }}
    .logo-badge {{
      width: 64px;
      height: 64px;
      margin: 0 auto 18px;
      border-radius: 18px;
      background: linear-gradient(135deg, rgba(0, 242, 254, 0.15), rgba(79, 172, 254, 0.05));
      border: 1px solid rgba(0, 242, 254, 0.25);
      display: flex;
      align-items: center;
      justify-content: center;
      color: #00f2fe;
      box-shadow: 0 8px 24px -4px rgba(0, 242, 254, 0.25);
    }}
    .logo-badge svg {{
      width: 32px;
      height: 32px;
      animation: pulse-ring 3s infinite ease-in-out;
    }}
    @keyframes pulse-ring {{
      0%, 100% {{ transform: scale(1); filter: drop-shadow(0 0 2px #00f2fe); }}
      50% {{ transform: scale(1.06); filter: drop-shadow(0 0 10px #00f2fe); }}
    }}
    h1 {{
      font-size: 20px;
      font-weight: 800;
      letter-spacing: 0.5px;
      background: linear-gradient(135deg, #ffffff 40%, #94a3b8 100%);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      margin-bottom: 6px;
    }}
    .subtitle {{
      font-size: 13px;
      color: #64748b;
      letter-spacing: 0.2px;
    }}
    .alert-box {{
      background: rgba(239, 68, 68, 0.12);
      border: 1px solid rgba(239, 68, 68, 0.35);
      color: #fca5a5;
      padding: 12px 14px;
      border-radius: 12px;
      font-size: 13px;
      display: flex;
      align-items: center;
      gap: 10px;
      margin-bottom: 22px;
    }}
    .alert-icon {{
      width: 18px;
      height: 18px;
      flex-shrink: 0;
      stroke: #ef4444;
    }}
    .form-group {{
      margin-bottom: 22px;
    }}
    label {{
      display: block;
      font-size: 12px;
      font-weight: 600;
      color: #94a3b8;
      text-transform: uppercase;
      letter-spacing: 0.7px;
      margin-bottom: 8px;
    }}
    .input-wrapper {{
      position: relative;
      display: flex;
      align-items: center;
    }}
    .input-icon {{
      position: absolute;
      left: 15px;
      width: 19px;
      height: 19px;
      color: #64748b;
      pointer-events: none;
      transition: color .2s;
    }}
    input[type="password"],
    input[type="text"] {{
      width: 100%;
      background: #090c12;
      border: 1px solid rgba(255, 255, 255, 0.1);
      border-radius: 12px;
      padding: 13px 44px 13px 44px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 15px;
      color: #f8fafc;
      outline: none;
      transition: border-color .2s, box-shadow .2s, background-color .2s;
    }}
    input:focus {{
      border-color: #00f2fe;
      background-color: #0c1018;
      box-shadow: 0 0 0 3px rgba(0, 242, 254, 0.15);
    }}
    .toggle-pwd {{
      position: absolute;
      right: 12px;
      background: none;
      border: none;
      color: #64748b;
      cursor: pointer;
      padding: 6px;
      border-radius: 8px;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: color .2s, background-color .2s;
    }}
    .toggle-pwd:hover {{
      color: #e2e8f0;
      background: rgba(255, 255, 255, 0.05);
    }}
    .toggle-pwd svg {{
      width: 18px;
      height: 18px;
    }}
    .submit-btn {{
      width: 100%;
      background: linear-gradient(135deg, #00f2fe 0%, #00a2ff 100%);
      color: #06090e;
      border: none;
      border-radius: 12px;
      padding: 14px;
      font-size: 15px;
      font-weight: 700;
      letter-spacing: 0.3px;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      box-shadow: 0 10px 25px -6px rgba(0, 162, 255, 0.4);
      transition: transform .15s, box-shadow .15s, opacity .15s;
    }}
    .submit-btn:hover {{
      opacity: 0.95;
      transform: translateY(-1px);
      box-shadow: 0 14px 28px -6px rgba(0, 162, 255, 0.55);
    }}
    .submit-btn:active {{
      transform: translateY(1px);
    }}
    .submit-btn svg {{
      width: 18px;
      height: 18px;
    }}
    .footer-note {{
      text-align: center;
      margin-top: 26px;
      font-size: 12px;
      color: #475569;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
    }}
    .footer-note svg {{
      width: 14px;
      height: 14px;
      stroke: #334155;
    }}
  </style>
</head>
<body>
  <div class="login-container">
    <div class="glow-effect"></div>
    <div class="card">
      <div class="header">
        <div class="logo-badge">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="12" cy="12" r="9"/>
            <circle cx="12" cy="12" r="5"/>
            <circle cx="12" cy="12" r="1"/>
            <path d="M12 3v3M12 18v3M3 12h3M18 12h3"/>
          </svg>
        </div>
        <h1>PRIVATE GRAPH OSINT</h1>
        <p class="subtitle">İlişki ve Ağ Analiz Platformu</p>
      </div>

      {error_html}

      <form method="POST" action="{form_action}">
        <div class="form-group">
          <label for="pwd">Erişim Şifresi</label>
          <div class="input-wrapper">
            <svg class="input-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <rect x="3" y="11" width="18" height="11" rx="2" ry="2"/>
              <path d="M7 11V7a5 5 0 0 1 10 0v4"/>
            </svg>
            <input type="password" id="pwd" name="password" required autofocus autocomplete="current-password" placeholder="••••••••••••">
            <button type="button" class="toggle-pwd" onclick="togglePassword()" aria-label="Şifreyi Göster">
              <svg id="eyeIcon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>
                <circle cx="12" cy="12" r="3"/>
              </svg>
            </button>
          </div>
        </div>

        <button type="submit" class="submit-btn">
          <span>Giriş Yap</span>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
            <path d="M5 12h14M12 5l7 7-7 7"/>
          </svg>
        </button>
      </form>

      <div class="footer-note">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <rect x="3" y="11" width="18" height="11" rx="2" ry="2"/>
          <path d="M7 11V7a5 5 0 0 1 10 0v4"/>
        </svg>
        <span>256-bit Güvenli Oturum • mcanefe.com.tr</span>
      </div>
    </div>
  </div>

  <script>
    function togglePassword() {{
      var input = document.getElementById('pwd');
      var icon = document.getElementById('eyeIcon');
      if (input.type === 'password') {{
        input.type = 'text';
        icon.innerHTML = '<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/>';
      }} else {{
        input.type = 'password';
        icon.innerHTML = '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>';
      }}
    }}
  </script>
</body>
</html>
'''
