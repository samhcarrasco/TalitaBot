## LLM Integration

```python
from src.llm.llm_manager import GPTAnswerer
from config.app_config import LLM_MODEL_TYPE, EASY_APPLY_MODEL

gpt_answerer = GPTAnswerer(api_key=api_key, model_type=LLM_MODEL_TYPE, model=EASY_APPLY_MODEL)
gpt_answerer.set_resume(structured_resume, resume_text)
```

- All prompts in [src/llm/prompts.py](src/llm/prompts.py) — never inline prompts elsewhere
- Validate all LLM responses before use
- Track costs via `LLMCall` model → `logs/llm_api_calls.yaml`
