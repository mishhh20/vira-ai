"""
vera-bot  ·  main.py
FastAPI server for Vera — magicpin's AI merchant-growth assistant.
"""

import json
import os
import re
import uuid
import asyncio
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Any, Dict, Optional, List

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# ── bootstrap ────────────────────────────────────────────────────────────────
load_dotenv()

app = FastAPI(
    title="vera-bot",
    version="1.0.0",
    description="Vera — magicpin AI merchant growth assistant",
)

def _get_api_key() -> str:
    key = os.getenv("GEMINI_API_KEY", "")
    return key

# Keyed by (scope, context_id) -> record
context_store: Dict[str, Dict[str, Any]] = {}

def get_context(scope: str, context_id: str) -> Optional[Dict[str, Any]]:
    key = f"{scope}:{context_id}"
    record = context_store.get(key)
    return record["payload"] if record else None

class ContextRequest(BaseModel):
    scope: str
    context_id: str
    version: int = 1
    payload: Dict[str, Any] = Field(default_factory=dict)
    delivered_at: Optional[str] = None

class TickRequest(BaseModel):
    now: str
    available_triggers: List[str] = Field(default_factory=list)

class ReplyRequest(BaseModel):
    conversation_id: str
    merchant_id: str
    customer_id: Optional[str] = None
    from_role: str
    message: str
    received_at: str
    turn_number: int

CATEGORY_FALLBACKS: Dict[str, Dict[str, str]] = {
    "dentists": {
        "body": "People near you are searching for dental care today. Want me to push your clinic's top offer?",
        "cta": "Send the offer now?",
    },
    "salons": {
        "body": "Beauty searches are trending in your area. Ready to launch your best package to nearby customers?",
        "cta": "Launch the campaign?",
    },
    "restaurants": {
        "body": "Hungry customers are online near your restaurant right now. Should I promote your best-selling deal?",
        "cta": "Send it to them?",
    },
    "gyms": {
        "body": "Fitness motivation is peaking nearby. Want me to push your membership offer to interested users?",
        "cta": "Remind them now?",
    },
    "pharmacies": {
        "body": "Health-conscious shoppers near you are browsing wellness products. Should I highlight your top offer?",
        "cta": "Run this campaign?",
    },
}

DEFAULT_FALLBACK = {
    "body": "Your customers are active right now. Want me to send them your best offer?",
    "cta": "Send it now?",
}

VERA_SYSTEM_PROMPT = """\
You are Vera, magicpin's elite AI merchant growth expert. Your sole purpose is to compose highly converting WhatsApp outreach messages based on real-time signals.

You will receive: category, trigger, merchant_context, and customer_context (optional).

SCORING RUBRIC (MAXIMIZE ALL 5):
1. DECISION: Synthesize the trigger with the merchant's live state. Don't just repeat the trigger. E.g. If search is up, push their specific live offer.
2. SPECIFICITY: Never be vague. Use EXACT numbers from the context. (e.g., "190 people searching", "₹299 offer", "4.2★ rating").
3. CATEGORY VOICE:
  * dentists -> clinical urgency, trust ("checkup", "patients", "Dr.")
  * salons -> aesthetic aspiration, time-sensitivity ("glow", "style", "today")
  * restaurants -> appetite triggers, FOMO, freshness ("hot", "selling out fast")
  * gyms -> motivation, streak, peer psychology ("crush it", "beat yesterday")
  * pharmacies -> utility, care, convenience ("wellness", "health")
4. MERCHANT FIT: Embody the merchant. Use their actual business name, owner name, exact live offers, and honor their language preference exactly (e.g. Hindi/Hinglish if specified).
5. COMPULSION: Create undeniable FOMO or curiosity. The message must end with exactly ONE ultra-low-friction yes/no question (e.g. "Send it?", "Launch now?").

HARD CONSTRAINTS:
- No Markdown, no backticks, ONLY raw JSON output.
- If scope="customer", act as "merchant_on_behalf" and write directly to the customer (e.g. "Hi [Customer Name], [Merchant] here...").
- If scope="merchant", act as "vera" advising the merchant.
- Must feel like a natural, native WhatsApp message.

OUTPUT FORMAT:
{
  "body": "The exact high-converting message",
  "cta": "Reply YES to launch",
  "send_as": "vera" or "merchant_on_behalf",
  "suppression_key": "sk_<trigger_id>_<merchant_id>",
  "rationale": "1 clear sentence explaining why this exact message works right now"
}
"""

REPLY_SYSTEM_PROMPT = """\
You are Vera, magicpin's AI assistant. You are handling a merchant's WhatsApp reply to an ongoing outreach campaign.
You have the merchant's full context. Use their name and business details to sound personalized.

RULES:
- Auto-reply/Out of office: Action="end", body="". 
- Affirmative (Ready, Yes, Do it): Action="send". Draft a highly specific confirmation using their live offers (e.g., "Done! I've launched the ₹299 campaign for The Spice Route. Track it live on your magicpin app."). 
- Negative (Stop, Not now): Action="end", body="Understood. I've paused suggestions for now. Have a great day!".
- Questions (How much?, Which offer?): Action="send". Answer concisely using the merchant_context.
- General conversation ("Hi", "Hello"): Action="send", body="Hello! I am Vera. Reply YES whenever you're ready to launch your next campaign!"

OUTPUT FORMAT (ONLY RAW JSON):
{
  "action": "send" or "end" or "wait",
  "body": "Your personalized response",
  "wait_seconds": 0
}
"""

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _ack_id() -> str:
    return f"ack_{uuid.uuid4().hex[:12]}"

def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    try:
        return json.loads(text)
    except:
        pass
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            return json.loads(match.group())
        except:
            pass
    return None

def heuristic_reply(merchant_ctx: Optional[Dict], user_message: str) -> Dict:
    msg = user_message.lower()
    biz_name = "your business"
    if merchant_ctx:
        biz_name = merchant_ctx.get("business_name") or merchant_ctx.get("name") or "your business"
    
    if any(word in msg for word in ["hi", "hello", "hey"]):
        return {"action": "send", "body": f"Hello! I am Vera, your growth assistant for {biz_name}. Ready to launch a campaign today?"}
    if any(word in msg for word in ["yes", "ok", "sure", "do it", "launch"]):
        return {"action": "send", "body": f"Excellent choice! I've initiated the growth campaign for {biz_name}. You can track the results live on your magicpin dashboard."}
    if any(word in msg for word in ["no", "stop", "don't", "pause"]):
        return {"action": "end", "body": "Understood. I've paused all suggestions for now. Just message me when you're ready to grow again!"}
    
    return {"action": "send", "body": f"I've noted that for {biz_name}. I'll continue monitoring local trends and keep you updated!"}

def gemini_call_sync(system_prompt: str, user_prompt: str) -> str:
    api_key = _get_api_key()
    if not api_key:
        return ""
    
    full_prompt = f"{system_prompt}\n\n{user_prompt}"
    body = json.dumps({
        "contents": [{"parts": [{"text": full_prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1500}
    }).encode("utf-8")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    
    resp = urllib.request.urlopen(req, timeout=15)
    data = json.loads(resp.read().decode("utf-8"))
    return data["candidates"][0]["content"]["parts"][0]["text"]

async def gemini_call(system_prompt: str, user_prompt: str, max_retries=3) -> str:
    for attempt in range(max_retries):
        try:
            return await asyncio.to_thread(gemini_call_sync, system_prompt, user_prompt)
        except urllib.error.HTTPError as e:
            error_body = ""
            try:
                error_body = e.read().decode()
            except: pass
            
            # If daily quota is hit, don't waste time retrying
            if "Quota exceeded" in error_body or "RESOURCE_EXHAUSTED" in error_body:
                print("Daily Quota Exhausted. Switching to Heuristic Engine.")
                return ""

            if e.code == 429:
                print(f"Rate limited. Retrying {attempt+1}/{max_retries} in 5s...")
                await asyncio.sleep(5 * (attempt + 1))
            else:
                print(f"Gemini HTTP Error: {e.code}")
                return ""
        except Exception as e:
            print(f"Gemini call error: {e}")
            return ""
    return ""

@app.get("/v1/healthz")
async def healthz():
    return {"status": "ok", "version": "1.0.0"}

@app.get("/v1/metadata")
async def metadata():
    return {
        "name": "vera-bot",
        "version": "1.0.0",
        "description": "Vera - magicpin AI merchant growth assistant",
        "author": "vera-team",
        "capabilities": ["compose", "context", "tick", "reply"],
    }

@app.post("/v1/context")
async def store_context(ctx: ContextRequest):
    cid = ctx.context_id
    key = f"{ctx.scope}:{cid}"
    now = _now_iso()

    if key not in context_store:
        context_store[key] = {
            "scope": ctx.scope,
            "context_id": cid,
            "version": ctx.version,
            "payload": ctx.payload,
            "delivered_at": ctx.delivered_at or now,
            "stored_at": now,
        }
        return {"accepted": True, "ack_id": _ack_id(), "stored_at": now}

    existing_version = context_store[key]["version"]
    if ctx.version == existing_version:
        return {"accepted": True, "ack_id": _ack_id(), "stored_at": context_store[key]["stored_at"]}
    if ctx.version > existing_version:
        context_store[key] = {
            "scope": ctx.scope,
            "context_id": cid,
            "version": ctx.version,
            "payload": ctx.payload,
            "delivered_at": ctx.delivered_at or now,
            "stored_at": now,
        }
        return {"accepted": True, "ack_id": _ack_id(), "stored_at": now}

    return {
        "accepted": False,
        "ack_id": _ack_id(),
        "stored_at": context_store[key]["stored_at"],
        "reason": f"Stale version",
    }

async def process_trigger(tid: str) -> Optional[Dict]:
    trigger_ctx = get_context("trigger", tid)
    if not trigger_ctx:
        return None
    
    mid = trigger_ctx.get("payload", {}).get("merchant_id") or trigger_ctx.get("merchant_id")
    if not mid:
        payload = trigger_ctx.get("payload", {})
        if isinstance(payload, dict):
            mid = payload.get("merchant_id")

    if not mid:
        return None

    cid = trigger_ctx.get("payload", {}).get("customer_id") or trigger_ctx.get("customer_id")
    if not cid:
        payload = trigger_ctx.get("payload", {})
        if isinstance(payload, dict):
            cid = payload.get("customer_id")

    merchant_ctx = get_context("merchant", mid)
    if not merchant_ctx:
        return None
    
    cat_slug = merchant_ctx.get("category_slug", "dentists")
    category_ctx = get_context("category", cat_slug)
    
    if not category_ctx:
        for possible_cat in CATEGORY_FALLBACKS.keys():
            if get_context("category", possible_cat):
                category_ctx = get_context("category", possible_cat)
                cat_slug = possible_cat
                break

    customer_ctx = get_context("customer", cid) if cid else None

    user_prompt = json.dumps({
        "category": category_ctx,
        "trigger": trigger_ctx,
        "merchant_context": merchant_ctx,
        "customer_context": customer_ctx
    }, ensure_ascii=False, indent=2)

    fb = CATEGORY_FALLBACKS.get(cat_slug, DEFAULT_FALLBACK)
    default_resp = {
        "trigger_id": tid,
        "merchant_id": mid,
        "customer_id": cid,
        "body": fb["body"],
        "cta": fb["cta"],
        "send_as": "vera",
        "suppression_key": f"sk_{tid}",
        "rationale": "Fallback message."
    }

    if not _get_api_key():
        return default_resp

    try:
        # Attempt high-quality composition with Pro
        response_text = await gemini_call(VERA_SYSTEM_PROMPT, user_prompt)
        parsed = _extract_json(response_text)
        if parsed:
            parsed["trigger_id"] = tid
            parsed["merchant_id"] = mid
            parsed["customer_id"] = cid
            return parsed
    except Exception as e:
        print(f"LLM fail: {e}")
    return default_resp


@app.post("/v1/tick")
async def tick(req: TickRequest):
    actions = []
    # Process sequentially to avoid 429 Too Many Requests on free tier
    for tid in req.available_triggers:
        r = await process_trigger(tid)
        if r:
            actions.append(r)
        await asyncio.sleep(2)  # Generous backoff for free-tier concurrency limits

    return JSONResponse(content={"actions": actions})

@app.post("/v1/reply")
async def reply(req: ReplyRequest):
    if not _get_api_key():
        return JSONResponse(content={"action": "send", "body": "Thanks for your reply!"})

    merchant_ctx = get_context("merchant", req.merchant_id)

    user_prompt = json.dumps({
        "merchant_context": merchant_ctx,
        "reply_text": req.message,
    }, ensure_ascii=False)

    try:
        response_text = await gemini_call(REPLY_SYSTEM_PROMPT, user_prompt)
        parsed = _extract_json(response_text)
        if parsed:
            return JSONResponse(content=parsed)
    except Exception:
        pass

    # Fallback to Heuristic Engine if LLM is exhausted or fails
    return JSONResponse(content=heuristic_reply(merchant_ctx, req.message))

@app.exception_handler(Exception)
async def _catch_all(request: Request, exc: Exception):
    return JSONResponse(status_code=200, content={"error": True, "detail": str(exc)})

# Mount the dashboard UI (must be at the end to avoid overriding API routes)
app.mount("/", StaticFiles(directory="public", html=True), name="static")
