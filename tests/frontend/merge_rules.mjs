import assert from 'node:assert/strict';
import { beforeEach, test } from 'node:test';

let cards = [];
let bootListeners = 0;
globalThis.document = {
  readyState: 'loading',
  addEventListener(type) { if (type === 'DOMContentLoaded') bootListeners++; },
  querySelector() { return null; },
  querySelectorAll(selector) { return selector.includes('.merge-card') ? cards : []; },
};
const root = process.env.MERGE_ASSETS;
const { normalizeMergeRule, defaultMergeRule, collectMergeRules } = await import(root + '/js/ui_merge_rules.js');
const editorBootListeners = bootListeners;
const { store } = await import(root + '/js/store.js');
const { collectForms } = await import(root + '/app.js');

test('importing a UI module does not initialize the application entrypoint', () => {
  assert.equal(editorBootListeners, 0);
  assert.equal(bootListeners, 1);
});

function keywordRule(overrides = {}) {
  return {
    name: '提示规则',
    channel: 'example',
    rule_class: 'KeywordNextNMerge',
    params: { trigger_keywords: ['提示'], trigger_regex: '', next_count: 2, time_window_seconds: 60 },
    ...overrides,
  };
}

function card(index, rule, changes = {}) {
  const values = {
    name: rule.name || '',
    channel: rule.channel,
    rule_class: rule.rule_class,
    trigger_keywords: (rule.params.trigger_keywords || []).join('\n'),
    trigger_regex: rule.params.trigger_regex || '',
    next_count: String(rule.params.next_count ?? 2),
    time_window_seconds: String(rule.params.time_window_seconds),
    ...changes,
  };
  return {
    dataset: { mergeIndex: String(index) },
    querySelector(selector) {
      const name = selector.match(/data-merge-(?:field|param)="([^"]+)"/)?.[1];
      return name in values ? { value: values[name] } : null;
    },
  };
}

beforeEach(() => {
  cards = [];
  store.state.config = { merge_rules: [] };
  store.state.expandedMergeRules = new Set();
  store.state.mergeRuleKeys = [];
});

test('SomeACG round trips only its parameters and preserves extension settings', () => {
  const rule = {
    channel: 'SomeACG',
    rule_class: 'SomeACGPreviewPlusOriginal',
    extra: 'retain',
    params: { time_window_seconds: 10, custom: 'retain', next_count: 2, trigger_keywords: [] },
  };
  store.state.config.merge_rules = [rule];
  cards = [card(0, rule)];
  collectMergeRules({ validate: true });
  assert.deepEqual(store.state.config.merge_rules[0].params, { time_window_seconds: 10, custom: 'retain' });
  assert.equal(store.state.config.merge_rules[0].extra, 'retain');
});

test('legacy keyword and count aliases remain effective during normalization', () => {
  const rule = normalizeMergeRule(keywordRule({
    params: { trigger_keywords: [], keywords: ['legacy'], merge_count: 4, time_window_seconds: 25 },
  }));
  assert.deepEqual(rule.params, {
    trigger_keywords: ['legacy'], trigger_regex: '', next_count: 4, time_window_seconds: 25,
  });
});

test('unrecognized strategies retain their opaque parameters', () => {
  const params = { keywords: ['retain'], count: 4, custom: 'retain' };
  const rule = normalizeMergeRule({ channel: 'example', rule_class: 'CustomMerge', params });
  assert.deepEqual(rule.params, params);
});

test('clearing a legacy keyword cannot reactivate the old alias', () => {
  const rule = keywordRule({ params: { keywords: ['legacy'], next_count: 1, time_window_seconds: 15 } });
  store.state.config.merge_rules = [rule];
  cards = [card(0, rule, { trigger_keywords: '', trigger_regex: '(?i)valid' })];
  collectMergeRules({ validate: true });
  assert.deepEqual(store.state.config.merge_rules[0].params.trigger_keywords, []);
  assert.equal(store.state.config.merge_rules[0].params.trigger_regex, '(?i)valid');
  assert.equal('keywords' in store.state.config.merge_rules[0].params, false);
});

test('collecting a filtered list retains hidden rules and their settings', () => {
  const first = keywordRule();
  const hidden = keywordRule({ name: 'hidden', channel: 'another', extra: 'retain' });
  store.state.config.merge_rules = [first, hidden];
  cards = [card(0, first, { name: 'edited', next_count: '3' })];
  collectMergeRules({ validate: true });
  assert.equal(store.state.config.merge_rules.length, 2);
  assert.equal(store.state.config.merge_rules[0].name, 'edited');
  assert.equal(store.state.config.merge_rules[0].params.next_count, 3);
  assert.equal(store.state.config.merge_rules[1].name, 'hidden');
  assert.equal(store.state.config.merge_rules[1].extra, 'retain');
});

test('switching strategy uses its own defaults instead of old hidden fields', () => {
  const rule = { name: '', channel: 'SomeACG', rule_class: 'SomeACGPreviewPlusOriginal', params: { time_window_seconds: 10 } };
  store.state.config.merge_rules = [rule];
  cards = [card(0, rule, { rule_class: 'KeywordNextNMerge' })];
  collectMergeRules();
  assert.deepEqual(store.state.config.merge_rules[0].params, {
    trigger_keywords: [], trigger_regex: '', next_count: 2, time_window_seconds: 60,
  });
});

test('an incomplete new rule stays visible and rejects saving', () => {
  store.state.config.merge_rules = [defaultMergeRule()];
  assert.throws(() => collectMergeRules({ validate: true }), /来源频道/);
  assert.equal(store.state.config.merge_rules.length, 1);
});

test('invalid counts reject saving instead of silently reverting to a previous value', () => {
  const rule = keywordRule();
  store.state.config.merge_rules = [rule];
  cards = [card(0, rule, { next_count: '0' })];
  assert.throws(() => collectMergeRules({ validate: true }), /后续消息数/);
  assert.equal(store.state.config.merge_rules[0].params.next_count, 0);
});

test('ordered rules can share a display name and channel', () => {
  store.state.config.merge_rules = [keywordRule(), keywordRule()];
  collectMergeRules({ validate: true });
  assert.equal(store.state.config.merge_rules.length, 2);
  assert.equal(store.state.config.merge_rules[0].name, store.state.config.merge_rules[1].name);
});

test('collecting forms for another page retains incomplete merge drafts', () => {
  store.state.config.merge_rules = [defaultMergeRule()];
  assert.doesNotThrow(() => collectForms());
  assert.equal(store.state.config.merge_rules.length, 1);
  assert.equal(store.state.config.merge_rules[0].channel, '');
});
