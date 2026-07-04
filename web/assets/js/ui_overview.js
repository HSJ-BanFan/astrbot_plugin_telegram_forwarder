import { store } from './store.js';
import { apiRequest } from './api.js';
import { escapeHtml, motionEnabled } from './utils.js';
import { els, showToast, withAction, loadStatusOnly } from './context.js';

function runtimeStatusLabel(status) {
  if (status === "running") return "运行中";
  if (status === "success") return "完成";
  if (status === "failed") return "失败";
  if (status === "cancelled") return "已取消";
  return "未知";
}

function formatRuntimeTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (!Number.isNaN(date.getTime())) {
    return date.toLocaleTimeString("zh-CN", { hour12: false });
  }
  return String(value).replace("T", " ");
}

/* 数字滚动：数值变化时从旧值滚到新值，让指标卡"看得见变化" */
function animateMetricNumber(el, value) {
  const next = Number(value) || 0;
  const prevRaw = el.dataset.countValue;
  const prev = prevRaw == null ? null : Number(prevRaw);
  el.dataset.countValue = String(next);
  if (!motionEnabled() || prev == null || !Number.isFinite(prev) || prev === next) {
    el.textContent = String(next);
    return;
  }
  if (el._countTween) el._countTween.kill();
  const state = { value: prev };
  el._countTween = window.gsap.to(state, {
    value: next,
    duration: 0.7,
    ease: "power2.out",
    onUpdate: () => {
      el.textContent = String(Math.round(state.value));
    },
  });
}

export function renderRuntimeOperations(runtime) {
  const operations = Array.isArray(runtime.operations) ? runtime.operations : [];
  const active = operations.find((operation) => operation.status === "running");
  const busyNotes = [];
  if (runtime.capture_busy) busyNotes.push("有频道正在抓取");
  if (runtime.send_busy || runtime.global_send_busy) busyNotes.push("发送任务正在执行，定时发送会自动跳过本轮");
  if (runtime.active_web_operations) busyNotes.push(`${runtime.active_web_operations} 个 Web 操作运行中`);

  if (els.runtimeMessage) {
    if (active) {
      els.runtimeMessage.textContent = `${active.label}：${active.message || "正在执行。"}`;
    } else if (busyNotes.length) {
      els.runtimeMessage.textContent = busyNotes.join("，");
    } else {
      els.runtimeMessage.textContent = "手动触发、暂停或恢复转发任务。";
    }
  }

  if (els.runtimeState) {
    els.runtimeState.innerHTML = busyNotes.length
      ? busyNotes.map((note) => `<span class="runtime-chip active">${escapeHtml(note)}</span>`).join("")
      : '<span class="runtime-chip">当前没有 Web 运行任务</span>';
  }

  if (els.runtimeLog) {
    els.runtimeLog.innerHTML = operations.length
      ? operations
          .map((operation) => {
            const duration = Number.isFinite(operation.duration_ms)
              ? `${Math.max(1, Math.round(operation.duration_ms / 1000))}s`
              : "";
            const meta = [
              runtimeStatusLabel(operation.status),
              formatRuntimeTime(operation.finished_at || operation.started_at),
              duration,
            ].filter(Boolean).join(" · ");
            return `
              <div class="runtime-log-item ${escapeHtml(operation.status || "")}">
                <div>
                  <strong>${escapeHtml(operation.label || "运行任务")}</strong>
                  <span>${escapeHtml(operation.message || "")}</span>
                </div>
                <small>${escapeHtml(meta)}</small>
              </div>
            `;
          })
          .join("")
      : '<div class="list-empty"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10" /><polyline points="12 6 12 12 16 14" /></svg><span>暂无运行记录，Web 操作会显示在这里</span></div>';
  }
}

export function renderStatus() {
  const status = store.state.status || {};
  const telegram = status.telegram || {};
  const runtime = status.runtime || {};
  const queue = status.queue || {};
  const me = telegram.me;

  if (els.telegramStatus) {
    let dotClass = "danger";
    let text = "未连接";
    if (telegram.authorized) {
      dotClass = "success";
      text = `已授权${me?.username ? ` @${me.username}` : ""}`;
    } else if (telegram.connected) {
      dotClass = "warning";
      text = "已连接，未授权";
    }
    els.telegramStatus.innerHTML = `<span class="status-badge"><span class="status-dot ${dotClass}"></span>${escapeHtml(text)}</span>`;
  }
  if (els.schedulerStatus) {
    let dotClass = "danger";
    let text = "未启动";
    if (runtime.scheduler_running) {
      if (runtime.paused) {
        dotClass = "warning";
        text = "已暂停";
      } else {
        dotClass = "success";
        text = "运行中";
      }
    }
    els.schedulerStatus.innerHTML = `<span class="status-badge"><span class="status-dot ${dotClass}"></span>${escapeHtml(text)}</span>`;
  }
  if (els.channelCount) {
    animateMetricNumber(els.channelCount, status.channels?.count ?? 0);
  }
  if (els.queueCount) {
    animateMetricNumber(els.queueCount, queue.total ?? 0);
  }

  renderRuntimeOperations(runtime);

  if (els.queueList) {
    const entries = Object.entries(queue.by_channel || {});
    els.queueList.innerHTML = entries.length
      ? entries
          .map(([channel, count]) => `<div class="queue-item"><span>${escapeHtml(channel)}</span><strong>${count}</strong></div>`)
          .join("")
      : '<div class="list-empty"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" /><polyline points="22 4 12 14.01 9 11.01" /></svg><span>队列已清空，暂无待发送消息</span></div>';
  }

  if (motionEnabled()) {
    window.gsap.killTweensOf(".status-dot");
    window.gsap.to(".status-dot.success", {
      scale: 1.25,
      opacity: 0.7,
      duration: 1.2,
      repeat: -1,
      yoyo: true,
      ease: "power1.inOut"
    });
    window.gsap.to(".status-dot.warning", {
      scale: 1.25,
      opacity: 0.7,
      duration: 1.2,
      repeat: -1,
      yoyo: true,
      ease: "power1.inOut"
    });
  }
}

export function initOverview() {
  if (els.runCheckBtn) {
    els.runCheckBtn.addEventListener("click", () =>
      withAction(() => apiRequest("/api/runtime/check", "POST"), "已开始后台执行。", { refresh: "status" })
    );
  }
  if (els.pauseBtn) {
    els.pauseBtn.addEventListener("click", () =>
      withAction(() => apiRequest("/api/runtime/pause", "POST"), "已暂停。", { refresh: "status" })
    );
  }
  if (els.resumeBtn) {
    els.resumeBtn.addEventListener("click", () =>
      withAction(() => apiRequest("/api/runtime/resume", "POST"), "已恢复。", { refresh: "status" })
    );
  }
  if (els.clearQueueBtn) {
    els.clearQueueBtn.addEventListener("click", () => {
      if (!window.confirm("确认清空全部待发送队列？")) return;
      withAction(() => apiRequest("/api/runtime/clear-queue", "POST", { target: "all" }), "队列已清空。", {
        refresh: "status",
      });
    });
  }

  // subscribe to store changes to re-render status
  store.subscribe(renderStatus);
}
