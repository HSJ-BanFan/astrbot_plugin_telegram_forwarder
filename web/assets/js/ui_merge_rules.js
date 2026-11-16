import { store } from './store.js';
import { els } from './context.js';
import { escapeHtml, fieldsMatchSearch, bindLiveSearchInput } from './utils.js';
import { splitList, joinList } from './ui_selector.js';
import { MERGE_RULE_CLASSES } from './config.js';

const strategies = new Map(MERGE_RULE_CLASSES.map(strategy => [strategy.value, strategy]));
const parameterNames = new Set(MERGE_RULE_CLASSES.flatMap(strategy => Object.keys(strategy.defaults)));
let nextKey = 0;
let query = '';

export function defaultMergeRule() {
  const strategy = MERGE_RULE_CLASSES[0];
  return {
    __template_key: 'default',
    name: '',
    channel: '',
    rule_class: strategy.value,
    params: { ...strategy.defaults, trigger_keywords: [] },
  };
}

export function normalizeMergeRule(rule = {}) {
  const cfg = { ...defaultMergeRule(), ...rule };
  const strategy = strategies.get(cfg.rule_class);
  const supplied = rule.params || {};
  cfg.params = { ...(strategy?.defaults || {}), ...supplied };
  if (cfg.rule_class === 'KeywordNextNMerge') {
    const primary = supplied.trigger_keywords;
    const hasPrimary = Array.isArray(primary) ? primary.length > 0 : Boolean(primary);
    cfg.params.trigger_keywords = splitList(hasPrimary ? primary : supplied.keywords || supplied.keyword);
    cfg.params.next_count = supplied.next_count ?? supplied.merge_count ?? supplied.count ?? strategy.defaults.next_count;
  }
  if (strategy) {
    for (const name of parameterNames) {
      if (!(name in strategy.defaults)) delete cfg.params[name];
    }
    for (const alias of ['keywords', 'keyword', 'merge_count', 'count']) delete cfg.params[alias];
  }
  return cfg;
}

function ruleKey(index) {
  return store.state.mergeRuleKeys[index] ||= `merge-rule-${++nextKey}`;
}

function rulesList() {
  return Array.isArray(store.state.config?.merge_rules) ? store.state.config.merge_rules : [];
}

function ruleTitle(rule) {
  return rule.name || (rule.channel ? `@${rule.channel}` : '未命名规则');
}

function ruleError(rule) {
  if (!rule.channel) return ['channel', '请选择或填写来源频道。'];
  if (!strategies.has(rule.rule_class)) return ['rule_class', '请选择支持的合并策略。'];
  if (rule.rule_class === 'KeywordNextNMerge') {
    if (!rule.params.trigger_keywords.length && !String(rule.params.trigger_regex || '').trim()) {
      return ['trigger_keywords', '至少填写一个触发关键词或触发正则。'];
    }
    if (!Number.isSafeInteger(rule.params.next_count) || rule.params.next_count < 1) {
      return ['next_count', '后续消息数必须是大于 0 的整数。'];
    }
  }
  if (!Number.isSafeInteger(rule.params.time_window_seconds) || rule.params.time_window_seconds < 1) {
    return ['time_window_seconds', '时间窗口必须是大于 0 的整数秒。'];
  }
  return null;
}

function showRuleError(index, error) {
  query = '';
  if (els.mergeRuleSearch) els.mergeRuleSearch.value = '';
  store.state.expandedMergeRules.add(ruleKey(index));
  renderMergeRules();
  const card = els.mergeRuleList?.querySelector(`[data-merge-index="${index}"]`);
  if (!card) return;
  const input = card.querySelector(`[data-merge-field="${error[0]}"], [data-merge-param="${error[0]}"]`);
  const feedback = card.querySelector('.merge-validation');
  card.classList.add('has-error');
  feedback.textContent = error[1];
  feedback.hidden = false;
  input?.setAttribute('aria-invalid', 'true');
  input?.focus();
  card.scrollIntoView({ block: 'nearest' });
}

export function collectMergeRules({ validate = false } = {}) {
  const cards = new Map([...document.querySelectorAll('.merge-card[data-merge-index]')]
    .map(card => [Number(card.dataset.mergeIndex), card]));
  const rules = rulesList().map((rule, index) => {
    const current = normalizeMergeRule(rule);
    const card = cards.get(index);
    if (!card) return current;
    const field = name => card.querySelector(`[data-merge-field="${name}"]`);
    const param = name => card.querySelector(`[data-merge-param="${name}"]`);
    const ruleClass = field('rule_class').value;
    const strategy = strategies.get(ruleClass);
    const changedStrategy = current.rule_class !== ruleClass;
    const params = changedStrategy ? { ...(strategy?.defaults || {}) } : { ...current.params };
    if (strategy) {
      for (const name of parameterNames) {
        if (!(name in strategy.defaults)) delete params[name];
      }
      if (!changedStrategy) {
        for (const name of Object.keys(strategy.defaults)) {
          const input = param(name);
          if (!input) continue;
          params[name] = name === 'trigger_keywords' ? splitList(input.value)
            : typeof strategy.defaults[name] === 'number' ? Number(input.value)
              : input.value.trim();
        }
      }
    }
    const channel = field('channel').value.trim().replace(/^[@#]/, '');
    return {
      ...current,
      name: field('name').value.trim(),
      channel,
      rule_class: ruleClass,
      params,
    };
  });
  store.state.config.merge_rules = rules;
  if (validate) {
    for (const [index, rule] of rules.entries()) {
      const error = ruleError(rule);
      if (error) {
        showRuleError(index, error);
        throw new Error(`第 ${index + 1} 条合并规则：${error[1]}`);
      }
    }
  }
}

function matchesQuery(rule) {
  return fieldsMatchSearch([
    rule.name, rule.channel, strategies.get(rule.rule_class)?.label,
    joinList(rule.params.trigger_keywords), rule.params.trigger_regex,
  ], query);
}

function triggerSummary(rule) {
  if (rule.rule_class !== 'KeywordNextNMerge') return '待选择合并策略';
  const words = rule.params.trigger_keywords || [];
  const parts = [];
  if (words.length) parts.push(words.slice(0, 2).join('、') + (words.length > 2 ? ` 等 ${words.length} 个关键词` : ''));
  if (rule.params.trigger_regex) parts.push('正则触发');
  return parts.join(' · ') || '尚未设置触发条件';
}

function renderParameters(rule) {
  if (!strategies.has(rule.rule_class)) return '';
  const params = rule.params;
  return `
    <div class="merge-params-grid">
    <label class="field merge-keywords">触发关键词
      <textarea data-merge-param="trigger_keywords" rows="3" placeholder="每行一个关键词">${escapeHtml(joinList(params.trigger_keywords))}</textarea>
      <small>多个关键词任意一个命中即可触发。</small>
    </label>
    <label class="field merge-regex"><span class="field-label">触发正则 <span class="field-optional">可选</span></span>
      <input data-merge-param="trigger_regex" value="${escapeHtml(params.trigger_regex)}" placeholder="例如：内容提示|前方高能" spellcheck="false" />
      <small>使用 Python 正则；关键词与正则任意一个命中即可触发。</small>
    </label>
    <label class="field">后续消息数
      <div class="input-with-unit"><input data-merge-param="next_count" type="number" min="1" step="1" value="${escapeHtml(params.next_count)}" /><span>条</span></div>
      <small>触发消息之外，再合并多少条消息。</small>
    </label>
      <label class="field">等待窗口
        <div class="input-with-unit"><input data-merge-param="time_window_seconds" type="number" min="1" step="1" value="${escapeHtml(params.time_window_seconds)}" /><span>秒</span></div>
        <small>未凑齐时等待；超时后处理已收到的消息。</small>
      </label>
    </div>`;
}

export function renderMergeRules() {
  if (!els.mergeRuleList) return;
  const rules = rulesList().map(normalizeMergeRule);
  const visible = rules.map((rule, index) => ({ rule, index })).filter(({ rule }) => matchesQuery(rule));
  const channels = new Set(rules.map(rule => rule.channel).filter(Boolean));
  if (els.mergeRuleStats) {
    els.mergeRuleStats.textContent = query
      ? `找到 ${visible.length} / ${rules.length} 条规则`
      : `${rules.length} 条规则 · ${channels.size} 个频道`;
  }
  const allExpanded = visible.length > 0 && visible.every(({ index }) => store.state.expandedMergeRules.has(ruleKey(index)));
  if (els.expandMergeRulesBtn) {
    els.expandMergeRulesBtn.textContent = allExpanded ? '全部收起' : '全部展开';
    els.expandMergeRulesBtn.disabled = !visible.length;
  }
  const sourceChannels = (store.state.config.source_channels || []).map(channel => channel.channel_username).filter(Boolean);
  els.mergeRuleList.innerHTML = `<datalist id="mergeSourceChannels">${sourceChannels.map(channel => `<option value="${escapeHtml(channel)}"></option>`).join('')}</datalist>` +
    (visible.length ? visible.map(({ rule, index }) => {
      const key = ruleKey(index);
      const collapsed = !store.state.expandedMergeRules.has(key);
      const strategy = strategies.get(rule.rule_class);
      const peers = rules.map((item, position) => ({ item, position })).filter(({ item }) => rule.channel && item.channel.toLowerCase() === rule.channel.toLowerCase());
      const peerOrder = peers.findIndex(({ position }) => position === index) + 1;
      const error = ruleError(rule);
      const windowSummary = rule.params.time_window_seconds == null ? '待配置时间窗口' : `${rule.params.time_window_seconds} 秒窗口`;
      return `
        <article class="channel-card merge-card ${collapsed ? 'collapsed' : ''}" data-merge-index="${index}" data-merge-key="${key}">
          <button class="channel-summary merge-summary" type="button" data-toggle-merge="${index}" aria-expanded="${!collapsed}" aria-controls="${key}-body">
            <span class="merge-order" aria-label="列表第 ${index + 1} 条">${String(index + 1).padStart(2, '0')}</span>
            <span class="merge-summary-content">
              <span class="merge-title-row"><span class="channel-title-text">${escapeHtml(ruleTitle(rule))}</span><span class="merge-strategy">${escapeHtml(strategy?.label || '未选择策略')}</span>${error ? '<span class="merge-draft">待完善</span>' : ''}</span>
              <span class="channel-meta merge-meta"><span class="merge-channel">${rule.channel ? `@${escapeHtml(rule.channel)}` : '未绑定频道'}</span><span class="merge-trigger" title="${escapeHtml(triggerSummary(rule))}">${escapeHtml(triggerSummary(rule))}</span>${rule.rule_class === 'KeywordNextNMerge' ? `<span>后续 ${escapeHtml(rule.params.next_count)} 条</span>` : ''}<span>${escapeHtml(windowSummary)}</span></span>
            </span>
            <span class="chevron" aria-hidden="true">⌄</span>
          </button>
          <div class="channel-body merge-body" id="${key}-body">
            ${peers.length > 1 ? `<div class="merge-priority-note">@${escapeHtml(rule.channel)} 共 ${peers.length} 条规则，当前为第 ${peerOrder} 条。先能组成完整分组的规则生效；消息不会被重复占用。</div>` : ''}
            <div class="merge-basics-grid">
              <label class="field"><span class="field-label">规则名称 <span class="field-optional">可选</span></span><input data-merge-field="name" value="${escapeHtml(rule.name)}" placeholder="为这条规则起一个便于识别的名字" /></label>
              <label class="field">来源频道<input data-merge-field="channel" value="${escapeHtml(rule.channel)}" list="mergeSourceChannels" placeholder="频道用户名，不带 @" autocapitalize="none" spellcheck="false" /></label>
              <label class="field">合并策略<select data-merge-field="rule_class">${!strategy ? `<option value="${escapeHtml(rule.rule_class)}" selected>请选择合并策略</option>` : ''}${MERGE_RULE_CLASSES.map(item => `<option value="${item.value}" ${rule.rule_class === item.value ? 'selected' : ''}>${item.label}</option>`).join('')}</select></label>
            </div>
            <div class="merge-strategy-description">${escapeHtml(strategy?.description || '选择策略后配置触发条件。')}</div>
            ${renderParameters(rule)}
            <p class="merge-validation" role="alert" hidden></p>
            <div class="merge-rule-footer">
              <span class="field-hint">${escapeHtml(strategy?.hint || '请选择合并策略。')}</span>
              <div class="merge-rule-actions">
                <button class="btn btn-soft" type="button" data-move-merge="${index}" data-move-direction="-1" ${index === 0 ? 'disabled' : ''} aria-label="上移第 ${index + 1} 条规则">上移</button>
                <button class="btn btn-soft" type="button" data-move-merge="${index}" data-move-direction="1" ${index === rules.length - 1 ? 'disabled' : ''} aria-label="下移第 ${index + 1} 条规则">下移</button>
                <button class="btn btn-soft danger" type="button" data-remove-merge="${index}">删除规则</button>
              </div>
            </div>
          </div>
        </article>`;
    }).join('') : `<div class="merge-empty"><strong>${query ? '没有匹配的规则' : '还没有合并规则'}</strong><p>${query ? '换个名称、频道或关键词试试。' : '选择一个频道，配置消息如何组成一组。'}</p>${query ? '<button class="btn btn-soft" type="button" data-clear-merge-search>清空搜索</button>' : '<button class="btn btn-primary" type="button" data-add-first-merge>新增第一条规则</button>'}</div>`);

  els.mergeRuleList.querySelectorAll('[data-toggle-merge]').forEach(button => button.addEventListener('click', () => {
    const index = Number(button.dataset.toggleMerge);
    const key = ruleKey(index);
    collectMergeRules();
    if (store.state.expandedMergeRules.has(key)) store.state.expandedMergeRules.delete(key);
    else store.state.expandedMergeRules.add(key);
    renderMergeRules();
    els.mergeRuleList.querySelector(`[data-toggle-merge="${index}"]`)?.focus({ preventScroll: true });
  }));
  els.mergeRuleList.querySelectorAll('[data-remove-merge]').forEach(button => button.addEventListener('click', () => {
    const index = Number(button.dataset.removeMerge);
    collectMergeRules();
    store.state.expandedMergeRules.delete(ruleKey(index));
    store.state.config.merge_rules.splice(index, 1);
    store.state.mergeRuleKeys.splice(index, 1);
    renderMergeRules();
  }));
  els.mergeRuleList.querySelectorAll('[data-move-merge]').forEach(button => button.addEventListener('click', () => {
    const index = Number(button.dataset.moveMerge);
    const destination = index + Number(button.dataset.moveDirection);
    collectMergeRules();
    ruleKey(index);
    ruleKey(destination);
    const [rule] = store.state.config.merge_rules.splice(index, 1);
    const [key] = store.state.mergeRuleKeys.splice(index, 1);
    store.state.config.merge_rules.splice(destination, 0, rule);
    store.state.mergeRuleKeys.splice(destination, 0, key);
    renderMergeRules();
    els.mergeRuleList.querySelector(`[data-toggle-merge="${destination}"]`)?.focus({ preventScroll: true });
  }));
  els.mergeRuleList.querySelectorAll('[data-merge-field="rule_class"]').forEach(select => select.addEventListener('change', () => {
    const index = Number(select.closest('.merge-card').dataset.mergeIndex);
    collectMergeRules();
    renderMergeRules();
    els.mergeRuleList.querySelector(`[data-merge-index="${index}"] [data-merge-field="rule_class"]`)?.focus({ preventScroll: true });
  }));
  els.mergeRuleList.querySelectorAll('input, textarea').forEach(input => input.addEventListener('input', () => {
    const card = input.closest('.merge-card');
    if (!card) return;
    input.removeAttribute('aria-invalid');
    card.classList.remove('has-error');
    card.querySelector('.merge-validation').hidden = true;
  }));
  els.mergeRuleList.querySelector('[data-add-first-merge]')?.addEventListener('click', addRule);
  els.mergeRuleList.querySelector('[data-clear-merge-search]')?.addEventListener('click', () => {
    query = '';
    els.mergeRuleSearch.value = '';
    renderMergeRules();
    els.mergeRuleSearch.focus();
  });
}

function addRule() {
  collectMergeRules();
  query = '';
  if (els.mergeRuleSearch) els.mergeRuleSearch.value = '';
  const rules = store.state.config.merge_rules;
  rules.push(defaultMergeRule());
  const index = rules.length - 1;
  store.state.expandedMergeRules.add(ruleKey(index));
  renderMergeRules();
  const input = els.mergeRuleList.querySelector(`[data-merge-index="${index}"] [data-merge-field="channel"]`);
  input?.focus();
  input?.closest('.merge-card').scrollIntoView({ block: 'nearest' });
}

export function initMergeRules() {
  els.addMergeRuleBtn?.addEventListener('click', addRule);
  if (els.mergeRuleSearch) bindLiveSearchInput(els.mergeRuleSearch, value => {
    collectMergeRules();
    query = value.trim();
    renderMergeRules();
  });
  els.expandMergeRulesBtn?.addEventListener('click', () => {
    collectMergeRules();
    const visible = rulesList().map((rule, index) => ({ rule: normalizeMergeRule(rule), index })).filter(({ rule }) => matchesQuery(rule));
    const collapse = visible.every(({ index }) => store.state.expandedMergeRules.has(ruleKey(index)));
    for (const { index } of visible) {
      if (collapse) store.state.expandedMergeRules.delete(ruleKey(index));
      else store.state.expandedMergeRules.add(ruleKey(index));
    }
    renderMergeRules();
  });
}
