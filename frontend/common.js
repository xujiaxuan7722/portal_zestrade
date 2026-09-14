/* common.js — 门户首页与管理后台共用脚本：API 封装、HTML 转义、图标解析、
   顶栏用户区固定件。依赖两页同款的顶栏结构（#avatarBtn/#userMenu/#userArea/
   #logoutBtn/#menuName/#menuEmail/#menuRoles）；须在 icons.js 之后、页面脚本之前引入。 */

const API_BASE = '';

async function fetchJSON(url, options) {
  const resp = await fetch(API_BASE + url, {
    credentials: 'include',
    headers: options?.body ? { 'Content-Type': 'application/json' } : undefined,
    ...options,
  });
  if (!resp.ok) {
    let detail = null;
    try { detail = (await resp.json()).detail; } catch {}
    throw Object.assign(new Error('HTTP ' + resp.status), { status: resp.status, detail });
  }
  return resp.json();
}

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/* 门户后台管理所需权限码（与后端 main.py 的 PORTAL_MANAGE_PERMISSION 保持一致，
   改名需同步）。RBAC 登记该码前，靠 "*" 通配即可生效：现 admin 持 "*" 天然通过。 */
const PORTAL_MANAGE_PERMISSION = 'portal:manage:console';

/* 与后端 auth.has_permission 同款判定："*" 全通过，支持 <前缀>:* 通配。
   仅用于界面显隐（体验优化）；真正的安全边界在后端逐路由门禁。 */
function hasPerm(perms, required) {
  if (perms.includes('*') || perms.includes(required)) return true;
  return perms.includes(required.split(':')[0] + ':*');
}

/* 图标解析优先级：图片 URL > 内置图标名 > 旧数据 emoji/文本 > 模块名首字兜底 */
function iconContent(m, size) {
  const v = (m.icon || '').trim();
  if (/^https?:\/\//.test(v)) {
    return `<img src="${esc(v)}" alt="" onerror="this.style.display='none'">`;
  }
  const svg = window.portalIconSVG?.(v, size);
  if (svg) return svg;
  if (v) return esc(v);
  return esc((m.name || '?').slice(0, 1));
}

/* ── 图标底色调色板：门户磁贴与后台徽标共用，同分类同色 ──
   键名与后端 main.py 的 PALETTE_KEYS 一致（改名需同步）。 */
const PALETTE = {
  blue:   { name: '蓝', css: 'linear-gradient(140deg,#5793f8,#2e6ae8)' },
  green:  { name: '绿', css: 'linear-gradient(140deg,#43d492,#1fae6e)' },
  orange: { name: '橙', css: 'linear-gradient(140deg,#f9b04e,#f28c1c)' },
  purple: { name: '紫', css: 'linear-gradient(140deg,#a98cf8,#7e5ce8)' },
  amber:  { name: '黄', css: 'linear-gradient(140deg,#f7c24b,#eda01f)' },
  red:    { name: '红', css: 'linear-gradient(140deg,#f88a7a,#ee5a45)' },
  cyan:   { name: '青', css: 'linear-gradient(140deg,#3fd0d9,#18a7b3)' },
  indigo: { name: '靛', css: 'linear-gradient(140deg,#6f7ef5,#4653e0)' },
};
const PALETTE_KEYS = Object.keys(PALETTE);

/* 未配置的分类按名字哈希取默认色：同名永远同色，不随排序/可见范围变化 */
function defaultColorKey(cat) {
  let h = 0;
  for (const ch of String(cat || '未分类')) h = (h * 31 + ch.codePointAt(0)) >>> 0;
  return PALETTE_KEYS[h % PALETTE_KEYS.length];
}
/* 分类的色名：后台配置优先，否则默认色 */
function colorKeyOf(cat, colors) {
  const k = colors?.[cat || '未分类'];
  return PALETTE[k] ? k : defaultColorKey(cat);
}
/* 渐变徽标（门户 48px / 后台 40px 同一份），底色按模块所属分类 */
function badgeHTML(m, colors, size) {
  const css = PALETTE[colorKeyOf(m.category, colors)].css;
  return `<span class="ic" style="background:${css}">${iconContent(m, Math.round(size * 0.46))}</span>`;
}

/* ── 用户区固定件：头像 + 下拉菜单 + 退出登录（接入规范固定件）── */
/* 渲染公共部分（头像、菜单里的名字/邮箱/角色），返回展示名（未登录返回空串）；
   页面差异（首页的问候语与管理入口、后台的 whoami）由调用方基于 me 自行处理 */
function renderUserArea(me) {
  const btn = document.getElementById('avatarBtn');
  const user = me?.user || {};
  const name = user.display_name || user.displayName || user.email || '未登录';
  const avatarUrl = user.avatar || user.avatar_url || '';

  const setInitial = () => { btn.textContent = name === '未登录' ? '?' : name.slice(0, 1); };
  if (avatarUrl) {
    const img = new Image();
    img.src = avatarUrl;
    img.alt = name;
    img.onerror = setInitial;           // 图片加载失败兜底成文字头像
    img.onload = () => { btn.textContent = ''; btn.appendChild(img); };
  } else {
    setInitial();
  }

  document.getElementById('menuName').textContent = name;
  document.getElementById('menuEmail').textContent = user.email || '';
  document.getElementById('menuRoles').innerHTML =
    (me?.roles || []).map(r => `<span class="role-chip">${esc(r)}</span>`).join('');
  return name === '未登录' ? '' : name;
}

(function initUserMenu() {
  const menu = document.getElementById('userMenu');
  document.getElementById('avatarBtn').addEventListener('click', () => menu.classList.toggle('open'));
  // 点击菜单外部自动收起（规范要求：capture 阶段 + composedPath 判断）
  document.addEventListener('click', (e) => {
    if (!e.composedPath().includes(document.getElementById('userArea'))) {
      menu.classList.remove('open');
    }
  }, true);
  document.getElementById('logoutBtn').addEventListener('click', () => {
    window.location.assign(`${API_BASE}/logout?returnTo=${encodeURIComponent(window.location.href)}`);
  });
})();

/* 未登录（401）时的"重新登录"入口：门户不做登录页，/login 由后端跳到公司共享
   oauth2-proxy 的登录入口，登录完成后回到当前页。 */
function reloginUrl() {
  return `${API_BASE}/login?returnTo=${encodeURIComponent(window.location.pathname + window.location.search + window.location.hash)}`;
}

/* 演示模式（AUTH_BYPASS）下点"退出登录"会被后端送回 /?demo_logout=1：
   没有登录态可退，这里给个提示并清掉参数，免得用户以为退出坏了。 */
(function demoLogoutNotice() {
  const url = new URL(window.location.href);
  if (url.searchParams.get('demo_logout') !== '1') return;
  url.searchParams.delete('demo_logout');
  history.replaceState(null, '', url.pathname + url.search + url.hash);
  const bar = document.createElement('div');
  bar.id = 'demoNotice';
  bar.style.cssText = 'position:fixed;left:50%;top:14px;transform:translateX(-50%);z-index:9999;'
    + 'background:#1f2937;color:#fff;padding:10px 16px;border-radius:8px;font-size:13px;'
    + 'box-shadow:0 4px 16px rgba(0,0,0,.25);max-width:92vw;line-height:1.6';
  bar.textContent = '当前是演示模式（AUTH_BYPASS），没有登录态，退出登录与切换用户不生效；'
    + '接入 oauth2-proxy + Keycloak 后才可用。';
  document.body.appendChild(bar);
  setTimeout(() => bar.remove(), 8000);
})();
