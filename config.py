import os
from dotenv import load_dotenv

load_dotenv()

PROVIDER = os.getenv("LLM_PROVIDER", "anthropic")  # anthropic | openai_compat
MODEL = os.getenv("LLM_MODEL", "claude-sonnet-5-5")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL") or None

MAX_PAGES = int(os.getenv("MAX_PAGES", "5"))
MAX_CHARS_PER_PAGE = int(os.getenv("MAX_CHARS_PER_PAGE", "4000"))
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "15"))
USER_AGENT = "Mozilla/5.0 (compatible; LeadResearchBot/0.1)"
ROBOTS_AGENT = "LeadResearchBot"

PRICE_INPUT = float(os.getenv("PRICE_INPUT_PER_MTOK", "0"))
PRICE_OUTPUT = float(os.getenv("PRICE_OUTPUT_PER_MTOK", "0"))


def estimate_cost(input_tokens: int, output_tokens: int) -> float:
    return round((input_tokens * PRICE_INPUT + output_tokens * PRICE_OUTPUT) / 1_000_000, 6)
