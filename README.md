# Vera AI — Merchant Growth Assistant

**Vera** is magicpin's elite AI merchant-growth assistant. This bot engages merchants on WhatsApp using real-time signals to drive business growth.

## Core Features

- **Gemini-Powered Composition**: Uses Gemini 1.5 Flash for high-quality, context-aware outreach.
- **Smart Heuristic Engine**: A robust fallback system that maintains specificity and personalization even when LLM quotas are exhausted.
- **Auto-Reply & Loop Detection**: Sophisticated detection of canned WhatsApp replies and self-looping patterns.
- **Multi-Turn Context**: Maintains conversation history for more natural interactions and intent transitions.
- **Hinglish Support**: Native-feeling Hindi-English code-mix for high engagement with Indian merchants.

## Approach

Vera uses a **4-layer context framework**:
1. **Category Context**: Deep vertical knowledge (e.g., Dentist vs. Restaurant).
2. **Merchant Context**: Real-time business performance, name, and offers.
3. **Trigger Context**: The "Why Now?" signal (e.g., Diwali, heatwave, performance dip).
4. **Customer Context**: Individual patient/customer data for highly targeted outreach.

## Technical Highlights

- **Heuristic-First Fallback**: If the LLM fails, Vera uses `smart_heuristic_compose` to synthesize triggers with merchant data, ensuring we never send generic "10% off" messages.
- **Intent Transitioning**: Detects affirmative merchant replies and switches from "pitching" to "actioning" mode immediately.
- **Strict Specificity**: Every message anchors on verifiable facts (exact search numbers, price points, or research citations).

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set up API Key
# Add GEMINI_API_KEY to your .env file

# 3. Run the server
uvicorn main:app --port 8000
```

## Evaluation Results
The bot is optimized for the 5-dimension rubric: Specificity, Category Fit, Merchant Fit, Trigger Relevance, and Engagement Compulsion.
