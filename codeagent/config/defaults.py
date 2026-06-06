"""Default configuration values."""

DEFAULT_MODEL_PROVIDER = "openai_compatible"
DEFAULT_MODEL_NAME = "anthropic/claude-sonnet-4.6"
WIZARD_MODEL_CHOICES = (
    "anthropic/claude-opus-4.8",
    "anthropic/claude-sonnet-4.6",
    "openai/gpt-5.5",
    "google/gemini-3.5-flash",
    "deepseek/deepseek-v4-pro",
    "minimax/minimax-m3",
    "qwen/qwen3.7-max",
)
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_API_KEY_ENV = "OPENROUTER_API_KEY"
DEFAULT_MODEL_MAX_TOKENS = 16384
DEFAULT_TEST_COMMAND = "pytest -q"
DEFAULT_TEST_FRAMEWORK = "pytest"
DEFAULT_LANGUAGE = "python"
DEFAULT_OUTPUT_DIR = "codeagent_runs"
DEFAULT_BENCHMARK_OUTPUT_DIR = "codeagent_runs/benchmarks"
DEFAULT_REPAIR_ATTEMPTS = 3
DEFAULT_COMMAND_TIMEOUT_SECONDS = 120
DEFAULT_LOG_TRUNCATION_CHARS = 12000
