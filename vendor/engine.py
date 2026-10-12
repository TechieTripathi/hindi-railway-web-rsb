# engine.py — AI News Generation Engine (v6)
# Bilingual output, 3 social media post variants, fact-check score. Vendored from the webapp; see vendor/__init__.py.

import httpx
import json
import re
import time
import os
import tempfile
from datetime import datetime

from .config import (
    ANTHROPIC_API_KEY, ANTHROPIC_MODEL, ANTHROPIC_URL,
    MAX_INPUT_CHARS, MAX_OUTPUT_TOKENS, AI_CALL_DELAY,
    CRAWL_OUTPUT, AGENCY_OUTPUT, STATUS_FILE,
)

_SYSTEM_PROMPT = [{"type": "text", "text": (
    "You are an AI railway news editor for India. Rewrite articles into original paraphrased news stories.\n\n"
    "LANGUAGE RULES:\n"
    "- Detect the source language (Hindi or English ONLY — no other languages).\n"
    "- source_lang: 'hi' if Hindi/Devanagari, 'en' if English. If source is in any other language (Malayalam, Tamil, Telugu, etc.), treat it as 'en' and write story in English.\n"
    "- ai_news_story: Write the full news story in the SOURCE language (Hindi or English).\n"
    "- ai_news_story_translated: Write the SAME story translated to the OTHER language (Hindi→English or English→Hindi).\n"
    "- The translation MUST be natural, fluent, and human-readable — NOT word-by-word transliteration. Write as a native speaker would.\n"
    "- ai_headline: headline in source language.\n"
    "- ai_headline_translated: headline translated to other language.\n\n"
    "CONTENT RULES:\n"
    "1. PARAPHRASE completely — rewrite every sentence in your own words.\n"
    "2. FACT-CHECK — only include facts from the source. Do NOT invent details.\n"
    "3. Write 3-5 paragraphs in professional news style.\n\n"
    "SOCIAL MEDIA — generate 3 posts in source language AND 3 in translated language:\n"
    "- social_twitter: max 280 chars, crisp, with hashtags (source lang)\n"
    "- social_facebook: 2-3 sentences, engaging tone (source lang)\n"
    "- social_general: 1-2 sentences, neutral professional tone (source lang)\n"
    "- social_twitter_translated, social_facebook_translated, social_general_translated: same in other language\n\n"
    "FACT-CHECK SCORING:\n"
    "- fact_score: 0-100. Score based on how many facts in your story can be verified from the source body.\n"
    "  100=all facts directly from source, 80-99=most facts from source with minor additions, "
    "60-79=core facts from source with contextual additions, 40-59=partial facts from source, "
    "20-39=generated from headline with minimal source data, 0-19=no source body at all.\n"
    "- fact_details: brief note on what specific facts were verified (mention numbers, names, dates found in source).\n"
    "- IMPORTANT: If source body has actual article text (not just metadata), fact_score should be at least 30.\n\n"
    "IMPACT: High = safety/cancellations/policy/accidents; Medium = infrastructure/new services/tech; Low = routine/awards/HR\n\n"
    "TOPICS: assign 2-4 from: Safety, Infrastructure, Passenger-Service, Operations, Technology, "
    "Policy, Finance, Environment, HR, Tenders, Delays, New-Trains, Station-Development, Freight, Tourism\n\n"
    "Return ONLY valid JSON:\n"
    '{"source_lang":"hi|en","original_headline":"...","ai_headline":"...","ai_headline_translated":"...",'
    '"ai_news_story":"...","ai_news_story_translated":"...",'
    '"social_twitter":"...","social_facebook":"...","social_general":"...",'
    '"social_twitter_translated":"...","social_facebook_translated":"...","social_general_translated":"...",'
    '"impact_level":"High|Medium|Low","impact_reason":"...","topics":["..."],"themes":"...",'
    '"fact_score":85,"fact_details":"..."}'
)}]


def _sanitize_json_text(text):
    """Aggressive JSON sanitization for AI output."""
    # Remove markdown fences
    text = re.sub(r'^```(?:json)?\s*', '', text.strip())
    text = re.sub(r'\s*```\s*$', '', text)
    # Fix common AI JSON errors
    text = text.replace('\n', ' ')  # newlines inside strings
    text = re.sub(r',\s*}', '}', text)  # trailing commas
    text = re.sub(r',\s*]', ']', text)  # trailing commas in arrays
    # Fix unescaped quotes inside string values (common AI error)
    # Match "key": "value with "quotes" inside" and fix inner quotes
    text = re.sub(r'(?<=: ")(.*?)(?="\s*[,}])', lambda m: m.group(0).replace('"', '\\"') if m.group(0).count('"') > 0 else m.group(0), text)
    return text.strip()


def _validate_ai_result(result):
    """Validate and fix AI output fields. Ensures no JSON artifacts shown to user."""
    if not isinstance(result, dict):
        result = {}
    required_str = [
        'source_lang', 'ai_headline', 'ai_headline_translated',
        'ai_news_story', 'ai_news_story_translated',
        'social_twitter', 'social_facebook', 'social_general',
        'social_twitter_translated', 'social_facebook_translated',
        'social_general_translated',
        'impact_level', 'impact_reason', 'themes', 'fact_details',
    ]
    for field in required_str:
        if field not in result or not isinstance(result[field], str):
            result[field] = str(result.get(field, '')) if result.get(field) else ''
        # Strip JSON/code artifacts from text fields
        if result[field].startswith('{') or result[field].startswith('['):
            result[field] = ''
    # Validate source_lang — ONLY Hindi or English allowed
    if result.get('source_lang') not in ('hi', 'en'):
        story = result.get('ai_news_story', '')
        result['source_lang'] = 'hi' if re.search(r'[\u0900-\u097F]', story[:200]) else 'en'
    # Check if story is in an unsupported language (Malayalam, Tamil, etc.)
    story = result.get('ai_news_story', '')
    if story and not re.search(r'[\u0900-\u097F]', story[:200]) and not re.search(r'[a-zA-Z]', story[:200]):
        # Non-Hindi, non-English script detected — swap with translated version if available
        translated = result.get('ai_news_story_translated', '')
        if translated and re.search(r'[a-zA-Z]', translated[:200]):
            result['ai_news_story'], result['ai_news_story_translated'] = translated, story
            result['ai_headline'], result['ai_headline_translated'] = result.get('ai_headline_translated', ''), result.get('ai_headline', '')
            result['source_lang'] = 'en'
    # Validate fact_score
    try:
        result['fact_score'] = max(0, min(100, int(result.get('fact_score', 0))))
    except (ValueError, TypeError):
        result['fact_score'] = 0
    # Validate topics
    if not isinstance(result.get('topics'), list):
        result['topics'] = []
    # Validate impact_level
    if result.get('impact_level') not in ('High', 'Medium', 'Low'):
        result['impact_level'] = 'Medium'
    return result


def _parse_ai_json(text):
    """Parse AI JSON output with aggressive error recovery."""
    text = _sanitize_json_text(text)
    # Try direct parse
    try:
        result = json.loads(text)
        return _validate_ai_result(result)
    except json.JSONDecodeError:
        pass
    # Try extracting JSON object
    start = text.find('{')
    if start == -1:
        raise ValueError("No JSON object found")
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == '\\' and in_string:
            escape = True
            continue
        if ch == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                try:
                    result = json.loads(text[start:i + 1])
                    return _validate_ai_result(result)
                except json.JSONDecodeError:
                    # Try sanitizing the extracted portion
                    cleaned = _sanitize_json_text(text[start:i + 1])
                    try:
                        result = json.loads(cleaned)
                        return _validate_ai_result(result)
                    except json.JSONDecodeError:
                        pass
    # Last resort: try to fix truncated JSON by closing it
    partial = text[start:] if start >= 0 else text
    # Close any open strings and braces
    if partial.count('"') % 2 != 0:
        partial += '"'
    open_braces = partial.count('{') - partial.count('}')
    partial += '}' * max(0, open_braces)
    try:
        result = json.loads(partial)
        return _validate_ai_result(result)
    except json.JSONDecodeError:
        raise ValueError("Could not parse AI JSON output")


def _update_status(message, progress=0, total=0, phase="ai_generate"):
    try:
        with open(STATUS_FILE, 'w') as f:
            json.dump({"phase": phase, "message": message,
                        "progress": progress, "total": total,
                        "updated_at": datetime.now().isoformat()}, f)
    except Exception:
        pass


def _empty_result(title, reason=""):
    return {
        "source_lang": "en", "original_headline": title,
        "ai_headline": title, "ai_headline_translated": "",
        "ai_news_story": reason or "[Not generated]",
        "ai_news_story_translated": "",
        "social_twitter": "", "social_facebook": "", "social_general": "",
        "social_twitter_translated": "", "social_facebook_translated": "",
        "social_general_translated": "",
        "impact_level": "Medium", "impact_reason": reason or "N/A",
        "topics": [], "themes": "", "fact_score": 0, "fact_details": reason,
    }


#: Minimum length below which a returned story is treated as a non-result.
MIN_STORY_CHARS = 30


def _is_real_story(story):
    """True if `story` is a genuine generation, not a failure placeholder.

    Three places used to test this independently — the resume scan, the retry
    loop and the final filter — and the resume scan was the weak one: it only
    rejected stories starting with "[", so a "Generation failed: ..." row left
    behind by `_save_partial` after a crash counted as DONE and that article was
    never retried. One predicate keeps the three in step.
    """
    if not story or len(story) <= MIN_STORY_CHARS:
        return False
    return not story.startswith("Generation failed") and not story.startswith("[")


def _compute_plagiarism(source_text, ai_text, ngram_size=4):
    """Compute plagiarism score using n-gram overlap.
    
    Extracts all n-word sequences from both texts and measures overlap.
    Lower score = better paraphrasing.
    
    Args:
        source_text: Original article body
        ai_text: AI-generated story
        ngram_size: Word sequence length to compare (4 = four-word phrases)
    
    Returns:
        dict: {
            "plagiarism_score": int (0-100, lower is better),
            "plagiarism_detail": str (explanation),
            "matching_phrases": int,
            "total_phrases": int,
        }
    """
    if not source_text or not ai_text or len(source_text) < 30 or len(ai_text) < 30:
        return {
            "plagiarism_score": 0,
            "plagiarism_detail": "Insufficient text for comparison.",
            "matching_phrases": 0,
            "total_phrases": 0,
        }

    def _get_ngrams(text, n):
        """Extract n-grams from text, normalized to lowercase."""
        words = re.findall(r'[a-zA-Z\u0900-\u097F]{2,}', text.lower())
        if len(words) < n:
            return set()
        return set(tuple(words[i:i + n]) for i in range(len(words) - n + 1))

    source_ngrams = _get_ngrams(source_text, ngram_size)
    ai_ngrams = _get_ngrams(ai_text, ngram_size)

    if not ai_ngrams:
        return {
            "plagiarism_score": 0,
            "plagiarism_detail": "AI text too short for n-gram analysis.",
            "matching_phrases": 0,
            "total_phrases": 0,
        }

    matching = ai_ngrams & source_ngrams
    score = round((len(matching) / len(ai_ngrams)) * 100)

    # Generate detail message
    if score <= 10:
        level = "Excellent"
        detail = "Highly original. {} of {} phrases are unique.".format(
            len(ai_ngrams) - len(matching), len(ai_ngrams))
    elif score <= 20:
        level = "Good"
        detail = "Well paraphrased. Minor phrase overlaps ({} of {}).".format(
            len(matching), len(ai_ngrams))
    elif score <= 35:
        level = "Acceptable"
        detail = "Moderate overlap. {} of {} phrases match source. Consider more paraphrasing.".format(
            len(matching), len(ai_ngrams))
    else:
        level = "High"
        detail = "Significant overlap. {} of {} phrases copied from source. Needs rewriting.".format(
            len(matching), len(ai_ngrams))

    return {
        "plagiarism_score": score,
        "plagiarism_detail": "{}: {}".format(level, detail),
        "matching_phrases": len(matching),
        "total_phrases": len(ai_ngrams),
    }


def generate_news(title, body):
    if not ANTHROPIC_API_KEY:
        print("  SKIP: ANTHROPIC_API_KEY not set")
        return _empty_result(title, "API key not set")

    truncated = body[:MAX_INPUT_CHARS] if body else ""
    user_msg = "Title: {}\n\nBody:\n{}".format(title, truncated)

    payload = {
        "model": ANTHROPIC_MODEL, "max_tokens": MAX_OUTPUT_TOKENS,
        "system": _SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_msg}],
    }
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    try:
        resp = httpx.post(ANTHROPIC_URL, headers=headers, json=payload, timeout=90.0)
        if resp.status_code != 200:
            print("API HTTP {}: {}".format(resp.status_code, resp.text[:300]))
            resp.raise_for_status()
        data = resp.json()
        text = data.get("content", [{}])[0].get("text", "")
        return _parse_ai_json(text)
    except Exception as e:
        print("AI error for '{}': {}".format(title[:50], str(e)[:200]))
        return _empty_result(title, "Generation failed: {}".format(str(e)[:100]))


def _save_partial(articles, target):
    try:
        with open(target, 'w', encoding='utf-8') as f:
            json.dump(articles, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def run_generate(input_file=None, output_file=None):
    src = input_file or CRAWL_OUTPUT
    dst = output_file or AGENCY_OUTPUT
    if not os.path.exists(src):
        _update_status("Error: No crawl data.", 0, 0)
        return []
    with open(src, 'r', encoding='utf-8') as f:
        articles = json.load(f)
    if not articles:
        return []

    existing = []
    if os.path.exists(dst):
        try:
            with open(dst, 'r', encoding='utf-8') as f:
                existing = json.load(f)
        except (json.JSONDecodeError, IOError):
            existing = []

    done_keys = set()
    for a in existing:
        if _is_real_story(a.get('ai_news_story')):
            key = re.sub(r'\s+', ' ', a.get('title', '').lower().strip())
            done_keys.add(key)

    to_generate = []
    # Load article status to skip deleted articles
    _status_map = {}
    try:
        status_file = os.path.join(os.path.dirname(src), 'article_status.json')
        if os.path.exists(status_file):
            with open(status_file, 'r') as f:
                _status_map = json.load(f)
    except Exception:
        pass

    for art in articles:
        key = re.sub(r'\s+', ' ', art.get('title', '').lower().strip())
        # Skip already generated
        if key in done_keys:
            continue
        # Skip deleted articles (Issue 5)
        import hashlib
        status_key = hashlib.md5(
            re.sub(r'\s+', ' ', (art.get('title', '') + art.get('date', '')).lower().strip()).encode('utf-8')
        ).hexdigest()
        if _status_map.get(status_key) == 'deleted':
            continue
        to_generate.append(art)

    if not to_generate:
        _update_status("All {} articles already generated.".format(len(articles)), len(articles), len(articles))
        return existing

    total = len(to_generate)
    _FIELDS = [
        'source_lang', 'ai_headline', 'ai_headline_translated',
        'ai_news_story', 'ai_news_story_translated',
        'social_twitter', 'social_facebook', 'social_general',
        'social_twitter_translated', 'social_facebook_translated', 'social_general_translated',
        'impact_level', 'impact_reason', 'topics', 'themes', 'fact_score', 'fact_details',
    ]

    for i, art in enumerate(to_generate):
        msg = "AI generating {}/{}: {}".format(i + 1, total, art['title'][:50])
        print(msg)
        _update_status(msg, i + 1, total)
        
        # Try generation with retry on failure
        result = None
        for attempt in range(2):
            result = generate_news(art['title'], art.get('body', ''))
            story = result.get('ai_news_story', '')
            # Check if generation actually succeeded
            if _is_real_story(story):
                break
            if attempt == 0:
                print("  Retrying generation (attempt {} failed)...".format(attempt + 1))
                time.sleep(3)
        
        for field in _FIELDS:
            art[field] = result.get(field, '' if isinstance(result.get(field), str) else result.get(field, []) if field == 'topics' else result.get(field, 0) if field == 'fact_score' else result.get(field, ''))
        # Backwards compat
        art.setdefault('social_post', art.get('social_twitter', ''))
        # Plagiarism check: compare source body vs AI story
        plag = _compute_plagiarism(art.get('body', ''), art.get('ai_news_story', ''))
        art['plagiarism_score'] = plag['plagiarism_score']
        art['plagiarism_detail'] = plag['plagiarism_detail']
        
        # Ensure fact_score consistency with fact_details and body
        fs = art.get('fact_score', 0)
        fd = art.get('fact_details', '').lower()
        has_body = len(art.get('body', '')) > 100
        story_ok = art.get('ai_news_story', '') and not art['ai_news_story'].startswith('Generation')
        
        if fs == 0 and story_ok:
            if 'verified' in fd or 'confirmed' in fd or 'all major' in fd:
                art['fact_score'] = 70
            elif has_body and ('from source' in fd or 'based on' in fd):
                art['fact_score'] = 50
            elif has_body:
                art['fact_score'] = 30  # Has body but AI said 0 — give baseline
            else:
                art['fact_score'] = 15  # Headline only
        if i < total - 1:
            time.sleep(AI_CALL_DELAY)
        _save_partial(existing + to_generate[:i + 1], dst)

    # Filter out articles with failed generation before saving
    successful = []
    for art in to_generate:
        story = art.get('ai_news_story', '')
        if _is_real_story(story):
            successful.append(art)
        else:
            print("  Filtered out failed article: {}".format(art.get('title', '')[:50]))

    _update_status("AI generation complete. {} new + {} existing.".format(len(successful), len(existing)), total, total)

    final = existing + successful
    fd, tmp = tempfile.mkstemp(suffix='.json', dir=os.path.dirname(dst) or '.')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(final, f, indent=2, ensure_ascii=False)
        os.replace(tmp, dst)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    print("Done. {} new, {} total.".format(len(to_generate), len(final)))
    return final
