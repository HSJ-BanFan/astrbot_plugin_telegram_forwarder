import { safeStorageGet } from './utils.js';

export const store = {
  state: {
    token: safeStorageGet("telegram_forwarder_token"),
    config: null,
    status: null,
    section: "overview",
    forwardGroup: "schedule",
    expandedChannels: new Set(),
    channelGroups: {},
    expandedMergeRules: new Set(),
    qqGroups: [],
    qqGroupsAvailable: false,
    qqGroupsMessage: "",
    tgChannels: [],
    tgChannelsAvailable: false,
    tgChannelsPartial: false,
    tgChannelsMessage: "",
  },
  listeners: [],
  subscribe(fn) {
    this.listeners.push(fn);
  },
  updateState(changes) {
    this.state = { ...this.state, ...changes };
    this.listeners.forEach(fn => fn(this.state));
  }
};
