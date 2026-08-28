"""
Compensation benchmarking agent — Priority 2 of the Recruitment Agent
workflow doc. Genuinely different from every other agent in this system:
this one makes REAL, live web searches at request time, because market
compensation data changes and a cached/trained-in number would be
actively misleading for an actual pay decision.

Trust design, the part that actually matters here: the model is NEVER
trusted to self-report which sources it used. An LLM asked to cite URLs
from memory can hallucinate a plausible-looking source that was never
actually retrieved. Instead, this extracts the REAL URLs the web_search
tool actually returned (from the server_tool_use / web_search_tool_result
content blocks Anthropic's API returns), and those — not whatever the
model claims in its prose — are what gets stored as "sources". If the
model's summary references a site that never actually appeared in a real
search result, that's a real problem worth surfacing, not silently
trusting the model's word for it.

UNTESTABLE in the sandbox this was built in — no real ANTHROPIC_API_KEY
here, same limitation as every other AI agent in this codebase. The web
search tool call itself, and the exact shape of its result blocks, is
built to the documented Anthropic API format but has not been exercised
against a live response. First real test happens on the deployed server.
"""

import json
import os
from datetime import datetime, timezone
from anthropic import Anthropic

MODEL = "claude-sonnet-4-6"
client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

SYSTEM_PROMPT = """You are a compensation research assistant. Given a role title,
experience level, and location, search the web for current, publicly available
compensation data (salary survey sites, job postings showing pay ranges, published
compensation reports) and synthesize a recommended range for an Indian employer.

After searching, respond with ONLY a JSON object in this exact shape, and nothing
else — no prose before or after it:
{
  "low_range": "<lower bound, e.g. '12 LPA'>",
  "median_range": "<market median, e.g. '14 LPA'>",
  "high_range": "<upper bound, e.g. '18 LPA'>",
  "suggested_range": "<your actual recommendation, e.g. '13-15 LPA'>",
  "confidence": "Low" | "Medium" | "High",
  "reasoning": "1-2 sentences on how you arrived at this, and what drove the confidence level"
}

Confidence should be Low if search results were sparse, conflicting, or not
India/role-specific; High only if multiple consistent, recent, relevant sources
were found. Never fabricate a number if searches returned nothing useful — say so
honestly in reasoning and set confidence to Low."""


def _extract_real_search_urls(response) -> list[dict]:
    """
    Pulls the ACTUAL URLs the web_search tool retrieved, from the real
    tool-result content blocks — not from anything the model wrote in its
    own text. This is the trust boundary: these are the only "sources" this
    function will ever report, because they're the only ones we can verify
    were genuinely searched, not just mentioned.
    """
    sources = []
    for block in response.content:
        block_type = getattr(block, "type", None)
        if block_type == "web_search_tool_result":
            content = getattr(block, "content", None)
            if isinstance(content, list):
                for result in content:
                    url = getattr(result, "url", None)
                    title = getattr(result, "title", None)
                    if url:
                        sources.append({"url": url, "title": title})
    return sources


def research_compensation(role_title: str, experience_range: str, location: str) -> dict:
    user_prompt = (
        f"Role: {role_title}\n"
        f"Experience: {experience_range or 'not specified'}\n"
        f"Location: {location or 'India (unspecified city)'}\n\n"
        f"Research current compensation for this role and return the JSON as instructed."
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=1500,
        system=SYSTEM_PROMPT,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{"role": "user", "content": user_prompt}],
    )

    # With a tool-enabled call, the final text is typically the last text
    # block, after any search/tool-use blocks — not necessarily content[0].
    text_blocks = [b.text for b in response.content if getattr(b, "type", None) == "text"]
    final_text = text_blocks[-1].strip() if text_blocks else ""
    final_text = final_text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()

    try:
        result = json.loads(final_text)
    except json.JSONDecodeError:
        # A malformed response here is a real failure worth surfacing
        # honestly, not silently defaulting to a fabricated-looking answer.
        result = {
            "low_range": None, "median_range": None, "high_range": None,
            "suggested_range": None, "confidence": "Low",
            "reasoning": "Could not parse a structured response from the research call — "
                         "raw model output was not valid JSON.",
        }

    real_sources = _extract_real_search_urls(response)

    return {
        "low_range": result.get("low_range"),
        "median_range": result.get("median_range"),
        "high_range": result.get("high_range"),
        "suggested_range": result.get("suggested_range"),
        "confidence": result.get("confidence", "Low"),
        "reasoning": result.get("reasoning"),
        "sources": real_sources,  # genuinely retrieved, not model-claimed
        "researched_at": datetime.now(timezone.utc).isoformat(),
        "model_used": MODEL,
    }
