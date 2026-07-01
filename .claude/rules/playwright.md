## Browser Automation (Playwright)

```python
from src.utils.browser_utils import create_playwright_browser, save_browser_session
from config.constants import BROWSER_STORAGE_STATE

page, context, browser, playwright_instance = await create_playwright_browser(
    storage_state=BROWSER_STORAGE_STATE
)
```

- Use `src/utils/utils.py` `pause()` for human-like random delays
- Prefer CSS selectors, then aria-labels, then XPath
- Always close browser/context/playwright in `finally` blocks
- Session state persisted in `browser_session/browser_state.json` (shared by all sites)

## Debugging Playwright Issues

Set `DEBUG_MODE = True` in [config/app_config.py](config/app_config.py) to enable capture-on-failure:

- **Screenshots + HTML dumps** — saved to `data/debug/<timestamp>_<label>.png/.html` whenever `safe_click` or `safe_fill` can't find or interact with an element. Share these files with Claude to diagnose selector issues.
- **Playwright trace** — saved to `data/debug/trace.zip` on exit. Open at `https://trace.playwright.dev` to inspect every action, DOM snapshot, and network request.

Both are no-ops when `DEBUG_MODE = False` (default), so there is no performance impact in normal runs.

Helper functions in [src/utils/browser_utils.py](src/utils/browser_utils.py):
- `debug_capture(page, label)` — call manually anywhere you want an on-demand snapshot
- `stop_tracing(context)` — called automatically in `main.py`'s `finally` block
