## Testing

```bash
uv run pytest tests/test_authenticator.py
uv run pytest tests/test_resume_anonymizer.py
uv run pytest tests/test_utils.py
uv run pytest  # run all tests
```

- Test files in `tests/` with `test_*.py` naming
- Mock LinkedIn, LLM APIs, Telegram, Playwright for unit tests
- Use `TEST_MODE=True` in app_config for integration testing without real applications
