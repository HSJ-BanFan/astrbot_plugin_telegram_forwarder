# ADR-004: Keep UI modules independent of the entrypoint

Load `app.js` through the HTML entrypoint only. Selectors collect and render
forms through the callbacks registered in `context.js`. Importing the
unversioned entrypoint from `ui_selector.js` initialized the application again
alongside the versioned HTML script. A browser reproduction produced two
configuration writes from one Save click. Removing that dependency gives
bootstrap one owner without adding a global initialization flag.

Collect drafts during selector refreshes and validate them at the Save
boundary. The merge editor owns its ordered UI keys separately from persisted
rules, so renaming, searching, removing and reordering preserve unfinished
edits. Strategy metadata supplies the applicable parameters and defaults while
retaining extension fields on user-defined rules.

`tests/test_web_merge_rules.py` runs the same JavaScript behavior checks against
the standalone and generated dashboard assets.
