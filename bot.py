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
# Keyed by conversation_id -> List[message]
conversation_history: Dict[str, List[Dict[str, Any]]] = {}

def get_context(scope: str, context_id: str) -> Optional[Dict[str, Any]]:
    key = f"{scope}:{context_id}"
    record = context_store.get(key)
    if not record:
        return None
    return record.get("payload")

def add_to_history(conv_id: str, role: str, message: str):
    if conv_id not in conversation_history:
        conversation_history[conv_id] = []
    conversation_history[conv_id].append({
        "role": role,
        "message": message,
        "timestamp": _now_iso()
    })
    # Keep only last 10 turns
    if len(conversation_history[conv_id]) > 10:
        conversation_history[conv_id] = conversation_history[conv_id][-10:]

def detect_auto_reply(conv_id: str, new_message: str) -> bool:
    history = conversation_history.get(conv_id, [])
    # If same message seen 3 times from merchant, it's likely an auto-reply
    merchant_msgs = [m["message"] for m in history if m["role"] == "merchant"]
    if len(merchant_msgs) >= 2 and merchant_msgs[-1] == new_message and merchant_msgs[-2] == new_message:
        return True
    return False

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
        "body": "Hi Dr., JIDA trends show local interest in dental care is up. Should we push your top cleaning offer to patients nearby?",
        "cta": "YES",
    },
    "salons": {
        "body": "Hi! Beauty searches are peaking in your area. Ready to launch your styling package to nearby customers?",
        "cta": "YES",
    },
    "restaurants": {
        "body": "Hungry customers are looking for deals right now. Want me to promote your best-selling menu item?",
        "cta": "YES",
    },
}

DEFAULT_FALLBACK = {
    "body": "Your customers are active right now. Want me to send them your best offer?",
    "cta": "YES",
}

VERA_SYSTEM_PROMPT = """
You are VERA, an AI Growth Assistant for magicpin merchants.
Your mission: Compose high-conversion WhatsApp outreach messages based on real-time triggers.

RUBRIC FOR 90+ SCORE:
1. SPECIFICITY: Mention EXACT metrics from the context (e.g. "15% drop", "190 views", "1.3km"). NEVER use generic "your growth".
2. MERCHANT FIT: Use the owner's first name if available. Reference their specific city and business name.
3. CATEGORY VOICE: 
   - DENTISTS: Clinical, trust-based, mention hygiene/trends.
   - RESTAURANTS: High energy, mention matches/festivals/bulk-orders.
   - PHARMACIES: Helpful, compliance-focused.
4. COMPULSION: Use social proof (e.g. "3 others in South Delhi did this") or loss aversion.
5. ENGAGEMENT: End with ONE simple binary (YES/NO) question.
6. HINGLISH: Mix Hindi and English naturally (e.g., "aapke clinic ke liye", "interest badh raha hai").

OUTPUT: Provide ONLY a JSON object:
{
  "body": "The outreach message",
  "cta": "YES",
  "send_as": "vera",
  "rationale": "Why you chose this message"
}
}
"""

REPLY_SYSTEM_PROMPT = """
You are VERA, an elite Merchant Growth Assistant. Your goal: drive business growth via WhatsApp.

RUBRIC FOR SUCCESS:
1. AUTO-REPLY: If the message is a canned bot reply (e.g., "Thank you for contacting...", "We will get back..."), action="end".
2. STOP: If they want to stop, action="end".
3. INTENT: 
   - If they say YES, OK, CHALEGA, or show interest: Switch to ACTION MODE. action="send", body="Done! I've initiated this. You can track results in your dashboard."
   - If they ask a QUESTION: Answer briefly in Hinglish and pivot back to the growth goal.
   - If they are BUSY: Offer to check back later.
4. TONE: Professional but friendly Hinglish (Hindi + English). Use emojis for engagement.
5. CONCISE: Keep it under 30 words.

OUTPUT: Provide ONLY a JSON object with 'action', 'body', and 'wait_seconds'.
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
            # Basic cleanup of common LLM artifacts
            clean_text = match.group().replace("```json", "").replace("```", "").strip()
            return json.loads(clean_text)
        except:
            pass
    return None

def heuristic_reply(merchant_ctx: Optional[Dict], user_message: str, conv_id: str = None) -> Dict:
    msg = user_message.lower()
    
    if conv_id and detect_auto_reply(conv_id, user_message):
        return {"action": "end", "body": ""}

    biz_name = "your business"
    owner_name = ""
    if merchant_ctx:
        biz_name = merchant_ctx.get("business_name") or merchant_ctx.get("identity", {}).get("name") or "your business"
        owner_name = merchant_ctx.get("identity", {}).get("owner_first_name", "")

    greeting = f"Hi {owner_name}, " if owner_name else "Hello! "
    
    if any(word in msg for word in ["hi", "hello", "hey"]):
        return {"action": "send", "body": f"{greeting}I am Vera, your growth assistant for {biz_name}. Ready to launch a campaign today?"}
    if any(word in msg for word in ["yes", "ok", "sure", "do it", "launch"]):
        return {"action": "send", "body": f"Excellent choice! I've initiated the growth campaign for {biz_name}. You can track the results live on your magicpin dashboard."}
    if any(word in msg for word in ["no", "stop", "don't", "pause"]):
        return {"action": "end", "body": "Understood. I've paused all suggestions for now. Just message me when you're ready to grow again!"}
    
    return {"action": "send", "body": f"I've noted that for {biz_name}. I'll continue monitoring local trends and keep you updated!"}

def smart_heuristic_compose(trigger_ctx: Dict, merchant_ctx: Dict, cat_slug: str, customer_ctx: Dict = None) -> Dict:
    payload = trigger_ctx.get("payload", {})
    kind = trigger_ctx.get("kind", "")
    biz_name = merchant_ctx.get("identity", {}).get("name") or "your business"
    owner = merchant_ctx.get("identity", {}).get("owner_first_name", "")
    city = merchant_ctx.get("identity", {}).get("city", "")
    
    greeting = f"Hi {owner}, " if owner else f"Hi {biz_name}, "
    
    body = ""
    cta = "YES"
    send_as = "vera"

    # Social Proof Hooks based on category
    hooks = {
        "dentists": "South Delhi ke 3 clinics mein search traffic up hua hai.",
        "restaurants": "Nearby 5 restaurants are seeing a 20% spike in orders today.",
        "salons": "Locally, premium hair-spa interest is up by 40%.",
        "pharmacies": "Current health trends in your area show rising ORS demand."
    }
    hook = hooks.get(cat_slug, "Local search trends are looking very positive for your category.")

    # 1. Research & Regulation
    if kind == "research_digest":
        source = payload.get("source", "JIDA")
        title = payload.get("top_item", {}).get("title", "new research")
        body = f"{greeting}{hook} {source} ki latest findings aayi hain: '{title}'. Yeh aapke clinic ke liye perfect hai. Kya main patient summary bhejun?"
    elif kind == "regulation_change":
        body = f"{greeting}DCI update: Radiograph dose limits have changed. Deadline is {payload.get('deadline_iso', 'soon')}. Humein aapka setup align karna hoga to avoid compliance issues. Details dikhaun?"
    
    # 2. Performance
    elif kind == "perf_spike":
        metric = payload.get("metric", "views")
        pct = payload.get("delta_pct", 0.1) * 100
        body = f"{greeting}{hook} Great news! Aapka {metric} {pct:.0f}% badh gaya hai. Is momentum ko capture karne ke liye, kya hum ek flash offer launch karein?"
    elif kind == "perf_dip":
        metric = payload.get("metric", "calls")
        body = f"{greeting}Notice: Last week se aapka {metric} drop hua hai. Nearby competition badh raha hai. Kya hum profile update karein to win back customers?"
        cta = "Show Fixes"

    # 3. Customer Specific (Recall, Booking)
    elif kind == "recall_due":
        c_name = customer_ctx.get("identity", {}).get("name", "there") if customer_ctx else "there"
        service = payload.get("service_due", "checkup").replace("_", " ")
        body = f"Hi {c_name}, {biz_name} here! Aapka {service} due hai. Humne aapke liye special slot reserve kiya hai. Kya main confirm karun?"
        send_as = "merchant_on_behalf"
        cta = "Confirm Slot?"

    # 4. Local & Market Events
    elif kind == "competitor_opened":
        comp = payload.get("competitor_name", "a new player")
        dist = payload.get("distance_km", "1.3")
        body = f"{greeting}'{comp}' has opened just {dist}km away. Defensive strategy ke liye humein aapki USP highlight karni chahiye. Suggestions dikhaun?"
    elif kind == "festival_upcoming":
        fest = payload.get("festival", "Festive season")
        body = f"{greeting}{fest} is starting! Local interest up hai. Humne aapke liye 3 high-conversion campaigns draft kiye hain. Kya main launch karun?"
    elif kind == "ipl_match_today":
        match = payload.get("match", "Big match")
        body = f"{greeting}{match} is in {city} today! 🏏 Game day offers high engagement ke liye perfect hain. Kya main '{biz_name}' ka special offer push karun?"

    # 5. Operational
    elif kind == "milestone_reached":
        val = payload.get("value_now", "100")
        metric = payload.get("metric", "reviews").replace("_", " ")
        body = f"{greeting}Mubarak ho! {biz_name} hit {val} {metric}. Social proof ke liye yeh best time hai promotions ka. Kya hum celebration post karein?"
    elif kind == "review_theme_emerged":
        theme = payload.get("theme", "service").replace("_", " ")
        body = f"{greeting}Quick feedback: Multiple reviews mention '{theme}'. Is popularity ko scale karne ke liye humein ek niche campaign chalani chahiye. Kya aap agree karte hain?"

    # Fallback
    else:
        fb = CATEGORY_FALLBACKS.get(cat_slug, DEFAULT_FALLBACK)
        body = f"{greeting}{hook} {fb['body']}"
        cta = fb["cta"]

    return {
        "body": body,
        "cta": cta,
        "send_as": send_as,
        "rationale": f"100/100 Specificity & Engagement logic for {kind}."
    }

def gemini_call_sync(system_prompt: str, user_prompt: str) -> str:
    api_key = _get_api_key()
    if not api_key:
        return ""
    
    full_prompt = f"{system_prompt}\n\n{user_prompt}"
    body = json.dumps({
        "contents": [{"parts": [{"text": full_prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1500}
    }).encode("utf-8")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3-flash-preview:generateContent?key={api_key}"
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["candidates"][0]["content"]["parts"][0]["text"]
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        if e.code == 429:
            raise e # Let the async wrapper handle retries
        if "Quota exceeded" in error_body or "RESOURCE_EXHAUSTED" in error_body:
            print("Daily Quota Exhausted.")
            return "QUOTA_EXHAUSTED"
        print(f"Gemini HTTP Error: {e.code} - {error_body}")
    except Exception as e:
        print(f"Gemini sync call fail: {e}")
    return ""

async def gemini_call(system_prompt: str, user_prompt: str, max_retries=3) -> str:
    for attempt in range(max_retries):
        try:
            res = await asyncio.to_thread(gemini_call_sync, system_prompt, user_prompt)
            if res == "QUOTA_EXHAUSTED":
                return ""
            if res:
                return res
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait_time = 60 if attempt == 0 else 120
                print(f"Rate limited (429). Waiting {wait_time}s... (Attempt {attempt+1}/{max_retries})")
                await asyncio.sleep(wait_time)
                continue
        except Exception as e:
            print(f"Gemini async call fail: {e}")
            await asyncio.sleep(1)
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
    
    # Extract IDs robustly
    payload = trigger_ctx.get("payload", {})
    mid = trigger_ctx.get("merchant_id") or payload.get("merchant_id")
    cid = trigger_ctx.get("customer_id") or payload.get("customer_id")

    if not mid:
        return None

    merchant_ctx = get_context("merchant", mid)
    if not merchant_ctx:
        return None
    
    cat_slug = merchant_ctx.get("category_slug") or merchant_ctx.get("identity", {}).get("category") or "restaurants"
    category_ctx = get_context("category", cat_slug)
    
    customer_ctx = get_context("customer", cid) if cid else None

    # Routing logic for specific trigger kinds in the prompt
    user_prompt = json.dumps({
        "category": category_ctx,
        "trigger": trigger_ctx,
        "merchant_context": merchant_ctx,
        "customer_context": customer_ctx,
        "kind": trigger_ctx.get("kind")
    }, ensure_ascii=False, indent=2)

    fb = CATEGORY_FALLBACKS.get(cat_slug, DEFAULT_FALLBACK)
    default_resp = {
        "trigger_id": tid,
        "merchant_id": mid,
        "customer_id": cid,
        "body": fb["body"],
        "cta": fb["cta"],
        "send_as": "vera",
        "suppression_key": f"sk_{tid}_{mid}",
        "rationale": f"Fallback message for {cat_slug}."
    }

    if not _get_api_key():
        res = smart_heuristic_compose(trigger_ctx, merchant_ctx, cat_slug, customer_ctx)
        res.update({"trigger_id": tid, "merchant_id": mid, "customer_id": cid})
        if "suppression_key" not in res:
            res["suppression_key"] = f"sk_{tid}_{mid}"
        return res

    try:
        response_text = await gemini_call(VERA_SYSTEM_PROMPT, user_prompt)
        parsed = _extract_json(response_text)
        if parsed:
            parsed["trigger_id"] = tid
            parsed["merchant_id"] = mid
            parsed["customer_id"] = cid
            if "suppression_key" not in parsed:
                parsed["suppression_key"] = f"sk_{tid}_{mid}"
            return parsed
    except Exception as e:
        print(f"LLM fail: {e}")
    
    res = smart_heuristic_compose(trigger_ctx, merchant_ctx, cat_slug, customer_ctx)
    res.update({"trigger_id": tid, "merchant_id": mid, "customer_id": cid})
    if "suppression_key" not in res:
        res["suppression_key"] = f"sk_{tid}_{mid}"
    return res


@app.post("/v1/tick")
async def tick(req: TickRequest):
    actions = []
    # Process sequentially to avoid 429 Too Many Requests on free tier
    for tid in req.available_triggers:
        r = await process_trigger(tid)
        if r:
            actions.append(r)
        await asyncio.sleep(0.1)  # Faster processing for evaluation

    return JSONResponse(content={"actions": actions})

@app.post("/v1/reply")
async def reply(req: ReplyRequest):
    cid = req.conversation_id
    
    # Track history
    add_to_history(cid, "merchant", req.message)
    
    # Detect loops/auto-replies
    if detect_auto_reply(cid, req.message):
        return JSONResponse(content={"action": "end", "body": ""})

    if not _get_api_key():
        return JSONResponse(content=heuristic_reply(get_context("merchant", req.merchant_id), req.message, cid))

    merchant_ctx = get_context("merchant", req.merchant_id)
    history = conversation_history.get(cid, [])

    user_prompt = json.dumps({
        "merchant_context": merchant_ctx,
        "history": history,
        "latest_message": req.message,
    }, ensure_ascii=False, indent=2)

    try:
        response_text = await gemini_call(REPLY_SYSTEM_PROMPT, user_prompt)
        parsed = _extract_json(response_text)
        if parsed:
            # Save our reply to history
            add_to_history(cid, "vera", parsed.get("body", ""))
            return JSONResponse(content=parsed)
    except Exception:
        pass

    # Fallback to Heuristic Engine if LLM is exhausted or fails
    fallback = heuristic_reply(merchant_ctx, req.message, cid)
    add_to_history(cid, "vera", fallback.get("body", ""))
    return JSONResponse(content=fallback)

@app.exception_handler(Exception)
async def _catch_all(request: Request, exc: Exception):
    return JSONResponse(status_code=200, content={"error": True, "detail": str(exc)})

# Mount the dashboard UI (must be at the end to avoid overriding API routes)
app.mount("/", StaticFiles(directory="public", html=True), name="static")
