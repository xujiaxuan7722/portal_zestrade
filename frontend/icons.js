/*
 * icons.js — 门户内置图标库（index.html 与 admin.html 共用）
 *
 * 模块的 icon 字段存这里的 key（如 "cart"）；SVG 线条图标随页面渲染，
 * 不依赖客户端字体，任何设备显示一致。兼容旧数据：icon 为图片 URL 或
 * emoji 时前端按原样显示（见各页面的 iconContent()）。
 */
window.PORTAL_ICONS = {
  cart:      { name: '购物车', svg: '<path d="M2 3h2l1.6 7.5h6.9L14 5H4.6"/><circle cx="6.6" cy="13" r="1.1"/><circle cx="11.4" cy="13" r="1.1"/>' },
  package:   { name: '包裹',   svg: '<path d="M8 2l6 3v6l-6 3-6-3V5z"/><path d="M2 5l6 3 6-3M8 8v6"/>' },
  truck:     { name: '物流',   svg: '<rect x="1.5" y="4" width="8" height="7" rx="0.8"/><path d="M9.5 6.5h2.8L14 8.8V11h-4.5"/><circle cx="4.5" cy="12.3" r="1.3"/><circle cx="11.3" cy="12.3" r="1.3"/>' },
  factory:   { name: '工厂',   svg: '<path d="M2 13.5V6l4 2.2V6l4 2.2V6l4 2.2v5.3z"/><path d="M3.2 6V2.8h2V6"/>' },
  clipboard: { name: '表单',   svg: '<rect x="3.5" y="3" width="9" height="11" rx="1.5"/><path d="M6 3V1.8h4V3M6 6.8h4M6 9.3h4M6 11.8h2.5"/>' },
  rocket:    { name: '火箭',   svg: '<path d="M8 1.5c2.2 1.6 3.2 4.2 3.2 6.8L9.7 11H6.3L4.8 8.3c0-2.6 1-5.2 3.2-6.8z"/><circle cx="8" cy="6" r="1.2"/><path d="M6.3 11l-.8 2.7M9.7 11l.8 2.7"/>' },
  paw:       { name: '爪印',   svg: '<ellipse cx="8" cy="10.2" rx="2.9" ry="2.2"/><circle cx="4.4" cy="6.8" r="1.2"/><circle cx="6.8" cy="5.2" r="1.2"/><circle cx="9.2" cy="5.2" r="1.2"/><circle cx="11.6" cy="6.8" r="1.2"/>' },
  palette:   { name: '调色盘', svg: '<path d="M8 2a6 6 0 1 0 0 12c1.1 0 1.4-.8 1.2-1.5-.3-1 .3-1.8 1.4-1.5 1.5.4 3.4-.5 3.4-3C14 4.5 11.3 2 8 2z"/><circle cx="5.2" cy="6.2" r="0.9"/><circle cx="8" cy="4.8" r="0.9"/><circle cx="10.8" cy="6.2" r="0.9"/>' },
  pencil:    { name: '画笔',   svg: '<path d="M11.3 2.7l2 2L5.6 12.4l-2.8.8.8-2.8zM10 4l2 2"/>' },
  users:     { name: '客户',   svg: '<circle cx="6" cy="5.8" r="2.3"/><path d="M2 13.4c0-2.1 1.8-3.4 4-3.4s4 1.3 4 3.4"/><circle cx="11.6" cy="6.4" r="1.7"/><path d="M11.2 10.1c1.7.3 2.8 1.4 2.8 3"/>' },
  chat:      { name: '沟通',   svg: '<path d="M2.5 3h11v7.5H7.5L4 13.5v-3H2.5z"/>' },
  shield:    { name: '盾牌',   svg: '<path d="M8 1.8l5 1.8v4c0 3-2 5.6-5 6.6-3-1-5-3.6-5-6.6v-4z"/><path d="M5.8 8l1.6 1.6L10.5 6.5"/>' },
  chart:     { name: '柱状图', svg: '<path d="M2 13.5h12"/><path d="M4.5 13.5V8.5M8 13.5V4M11.5 13.5V6.5"/>' },
  trend:     { name: '趋势',   svg: '<path d="M2 12l4-4 2.5 2.5L14 5"/><path d="M10.5 5H14v3.5"/>' },
  doc:       { name: '文档',   svg: '<path d="M4 1.5h5.5L13 5v9.5H4z"/><path d="M9.5 1.5V5H13M6.2 8h3.6M6.2 10.5h3.6"/>' },
  folder:    { name: '文件夹', svg: '<path d="M2 4.5A1.5 1.5 0 0 1 3.5 3h3L8 5h4.5A1.5 1.5 0 0 1 14 6.5v5a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 2 11.5z"/>' },
  gear:      { name: '设置',   svg: '<circle cx="8" cy="8" r="2.2"/><path d="M13 8c0-.4 0-.8-.1-1.1l1.4-1-1.2-2.1-1.6.6c-.6-.5-1.2-.9-2-1.1L9.2 1.6H6.8l-.3 1.7c-.8.2-1.4.6-2 1.1l-1.6-.6-1.2 2.1 1.4 1c-.1.3-.1.7-.1 1.1s0 .8.1 1.1l-1.4 1 1.2 2.1 1.6-.6c.6.5 1.2.9 2 1.1l.3 1.7h2.4l.3-1.7c.8-.2 1.4-.6 2-1.1l1.6.6 1.2-2.1-1.4-1c.1-.3.1-.7.1-1.1z"/>' },
  calendar:  { name: '日历',   svg: '<rect x="2" y="3" width="12" height="11" rx="1.5"/><path d="M2 6.5h12M5.5 1.5v3M10.5 1.5v3"/>' },
  mail:      { name: '邮件',   svg: '<rect x="2" y="3.5" width="12" height="9" rx="1.5"/><path d="M2.5 4.5L8 9l5.5-4.5"/>' },
  globe:     { name: '全球',   svg: '<circle cx="8" cy="8" r="6"/><path d="M2 8h12M8 2c-3.8 3.8-3.8 8.2 0 12M8 2c3.8 3.8 3.8 8.2 0 12"/>' },
  card:      { name: '卡券',   svg: '<rect x="2" y="3.5" width="12" height="9" rx="1.5"/><path d="M2 6.5h12M4.5 10h3"/>' },
  database:  { name: '数据库', svg: '<ellipse cx="8" cy="3.6" rx="5.5" ry="1.9"/><path d="M2.5 3.6v8.8c0 1 2.5 1.9 5.5 1.9s5.5-.9 5.5-1.9V3.6"/><path d="M2.5 8c0 1 2.5 1.9 5.5 1.9S13.5 9 13.5 8"/>' },
  monitor:   { name: '屏幕',   svg: '<rect x="2" y="3" width="12" height="8" rx="1.5"/><path d="M6 14h4M8 11v3"/>' },
  book:      { name: '知识库', svg: '<path d="M8 3.4C6.5 2.3 4.3 2 2 2v10.6c2.3 0 4.5.3 6 1.4 1.5-1.1 3.7-1.4 6-1.4V2c-2.3 0-4.5.3-6 1.4z"/><path d="M8 3.4V14"/>' },
  tag:       { name: '标签',   svg: '<path d="M2 2h5.5L14 8.5 8.5 14 2 7.5z"/><circle cx="5" cy="5" r="1.2"/>' },
  star:      { name: '星标',   svg: '<path d="M8 1.8l1.9 3.9 4.3.6-3.1 3 .7 4.3L8 11.6l-3.8 2 .7-4.3-3.1-3 4.3-.6z"/>' },
  home:      { name: '首页',   svg: '<path d="M2 7.5 8 2l6 5.5V14H2z"/><path d="M6 14v-4h4v4"/>' },
  bell:      { name: '通知',   svg: '<path d="M8 2a4 4 0 0 1 4 4c0 3 .8 4 1.5 4.5h-11C3.2 10 4 9 4 6a4 4 0 0 1 4-4z"/><path d="M6.5 12.5a1.5 1.5 0 0 0 3 0"/>' },
};

window.portalIconSVG = function (key, size) {
  const def = window.PORTAL_ICONS[key];
  if (!def) return null;
  return `<svg width="${size}" height="${size}" viewBox="0 0 16 16" fill="none"` +
    ` stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">${def.svg}</svg>`;
};
