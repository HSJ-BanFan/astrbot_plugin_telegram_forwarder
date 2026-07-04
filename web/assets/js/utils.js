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

export function bindLiveSearchInput(input, onSearch) {
  if (!input || typeof onSearch !== "function") return;
  let composing = false;
  input.addEventListener("compositionstart", () => {
    composing = true;
  });
  input.addEventListener("compositionend", (event) => {
    composing = false;
    onSearch(event.target.value);
  });
  input.addEventListener("input", (event) => {
    if (composing || event.isComposing) return;
    onSearch(event.target.value);
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
    }
  });
}

export function safeStorageGet(key, fallback = "") {
  try {
    return window.localStorage?.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
}

export function safeStorageSet(key, value) {
  try {
    window.localStorage?.setItem(key, value);
  } catch {
    // AstrBot Plugin Page iframes are sandboxed without allow-same-origin.
  }
}

export function safeStorageRemove(key) {
  try {
    window.localStorage?.removeItem(key);
  } catch {
    // AstrBot Plugin Page iframes are sandboxed without allow-same-origin.
  }
}

const REDUCED_MOTION_QUERY =
  typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-reduced-motion: reduce)")
    : null;

/* GSAP 动效总开关：库缺失（CDN 加载失败）或用户偏好减弱动效时统一降级 */
export function motionEnabled() {
  return Boolean(window.gsap) && !(REDUCED_MOTION_QUERY && REDUCED_MOTION_QUERY.matches);
}
