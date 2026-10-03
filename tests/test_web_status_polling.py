"""Execute status polling with a deterministic clock, in both shipped frontends."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HARNESS = r"""
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync(process.argv[1], 'utf8')
  .replace(/^import .*;\r?\n/gm, '').replace(/^export /gm, '');
let now = 0, nextId = 0, calls = 0;
const timers = new Map();
const store = {
  state: {token: 'test', status: {telegram: {authorized: true}, runtime: {}}},
  updateState(changes) {Object.assign(this.state, changes);},
};
const sandbox = {
  store, console,
  apiRequest: async () => {calls++; return store.state.status;},
  window: {
    setTimeout(fn, delay) {timers.set(++nextId, {fn, at: now + delay}); return nextId;},
    clearTimeout(id) {timers.delete(id);},
  },
};
vm.createContext(sandbox);
vm.runInContext(source + '\nels.appShell = {hidden: false};', sandbox);
async function advance(ms) {
  const end = now + ms;
  while (true) {
    const due = [...timers].filter(([,t]) => t.at <= end).sort((a,b) => a[1].at-b[1].at)[0];
    if (!due) break;
    now = due[1].at;
    timers.delete(due[0]);
    await due[1].fn();
  }
  now = end;
}
(async () => {
  sandbox.syncRuntimeStatusRefresh();
  await advance(1000);
  store.state.status.runtime.send_busy = true;
  sandbox.syncRuntimeStatusRefresh();
  await advance(2000);
  const busyCalls = calls;
  store.state.status.runtime.send_busy = false;
  sandbox.syncRuntimeStatusRefresh();
  await advance(2000);
  const idleCalls = calls;
  // Repeated synchronization must not postpone an unchanged deadline.
  sandbox.syncRuntimeStatusRefresh();
  await advance(28000);
  const resumedIdleCalls = calls;
  store.state.status.telegram.authorized = false;
  sandbox.syncRuntimeStatusRefresh();
  await advance(60000);
  process.stdout.write(JSON.stringify({busyCalls, idleCalls, resumedIdleCalls, finalCalls: calls, timers: timers.size}));
})();
"""


@pytest.mark.parametrize("frontend", ["web", "pages/dashboard"])
def test_status_polling_reschedules_on_activity_changes(frontend):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    completed = subprocess.run(
        [node, "-e", HARNESS, str(ROOT / frontend / "assets/js/context.js")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=True,
    )
    assert json.loads(completed.stdout) == {
        "busyCalls": 1,
        "idleCalls": 1,
        "resumedIdleCalls": 2,
        "finalCalls": 2,
        "timers": 0,
    }
