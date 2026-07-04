export function escapeHtml(str) {
  if (typeof str !== "string") return str;
  return str.replace(/[&<>"']/g, m => {
    switch (m) {
      case '&': return '&amp;';
      case '<': return '&lt;';
      case '>': return '&gt;';
      case '"': return '&quot;';
      case "'": return '&#39;';
      default: return m;
    }
  });
}

export function channelKey(cfg, index) {
  return cfg.channel_username ? `@${cfg.channel_username}` : `index_${index}`;
}

export function channelTitle(username) {
  return `@${username.replace(/^@/, "")}`;
}

const REDUCED_MOTION_QUERY =
  typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-reduced-motion: reduce)")
    : null;

/* GSAP 动效总开关：库缺失（CDN 加载失败）或用户偏好减弱动效时统一降级 */
export function motionEnabled() {
  return Boolean(window.gsap) && !(REDUCED_MOTION_QUERY && REDUCED_MOTION_QUERY.matches);
}
