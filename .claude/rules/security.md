## Privacy & Security

- Never log passwords, API keys, or personal information
- Resume anonymization replaces personal data before LLM calls (except resume parsing and Non-Easy Apply)
- All secrets in `.env` (never committed); use `.env_example` as template
- Only validate at system boundaries (user input, external APIs)
