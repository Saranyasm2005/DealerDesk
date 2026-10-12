# VIKMO DealerDesk

**Track:** AI / ML Intern Track (with complete Order Engine, Supplier Sync, and Dealer Portal frontend bonus components)  
**LLM Provider & Model:** Google Gemini (`gemini-2.5-flash` / `gemini-1.5-flash`) via `GEMINI_API_KEY`  
**Video Walkthrough Link:** *(Insert unlisted YouTube/Loom video link here)*  
**Video Walkthrough Script:** See [`WALKTHROUGH_SCRIPT.md`](file:///D:/vikmo/WALKTHROUGH_SCRIPT.md) for full 5-minute timed talking points.  

---

## 1. Overview

VIKMO DealerDesk is an AI-native distribution operating system designed for spare parts distributors. It solves inventory overselling, automates catalog synchronization with suppliers, provides a grounded conversational AI assistant with tool calling for dealers, and forecasts weekly demand to generate inventory reorder suggestions.

### Key Implemented Capabilities:
- **Part A1 & A2 (Backend Core & Order Engine)**:
  - Products catalog with multi-attribute filtering (search, category, vehicle fitment).
  - Dealer management and inventory adjustment.
  - Multi-item DRAFT order creation with live catalog pricing.
  - Full order lifecycle: `DRAFT` → `CONFIRMED` → `DISPATCHED` → `DELIVERED`, with `CANCELLED`.
  - Concurrency & stock protection: row-level locking (`select_for_update`) prevents race condition overselling under simultaneous requests; atomic all-or-nothing stock deduction.
  - Exact 18% GST calculation per line rounded with `ROUND_HALF_UP` to the paisa.
  - Thread-safe unique order numbers (`ORD-YYYYMMDD-NNNN`).
  - Request idempotency via `Idempotency-Key` header with SHA256 body hash conflict detection.
- **Part B (Supplier Sync)**:
  - Resilient catalogue sync: paginates via cursor, retries on `429` (`Retry-After`) and `5xx` backoff (max 5 attempts).
  - Resolves duplicate SKUs across pages using latest `updated_at`.
  - Rejects malformed items without crashing.
  - Atomic bulk creation/updates without modifying distributor stock.
- **Part C (Dealer Assistant - GenAI)**:
  - Conversational agent powered by Google Gemini tool calling.
  - Grounded in real-time database state using tools: `search_parts`, `check_stock`, `create_draft_order`.
  - Multi-turn conversational memory via `session_id`.
  - Prompt injection defense: untrusted catalog descriptions are treated strictly as data.
  - Ambiguity detection: asks clarifying questions when vehicle fitment is omitted.
- **Part D (Forecasting & Reorder - ML)**:
  - Demand forecasting engine in `ml/train_forecast.py` modeling trend, promo multipliers, Diwali festival timing shift, intermittent demand (Croston's method), and newly launched SKU ramp-up.
  - Committed 4-week forecast for 40 SKUs in `ml/forecast.csv` (160 rows).
  - Reorder suggestions endpoint `/api/reorder-suggestions/` implementing exact safety stock formula with live warehouse stock.

---

## 2. Setup Instructions

### Prerequisites
- Python 3.11+
- PostgreSQL database (e.g. Neon, Supabase, or local PostgreSQL)

### Installation

```bash
# 1. Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate       # On Windows: .venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment variables in .env (copy from .env.example)
cp .env.example .env
# Edit .env with your DATABASE_URL and GEMINI_API_KEY
```

### Database Migration & Seeding

```bash
python manage.py migrate --noinput

# Run seed_data (safe to run multiple times; idempotent)
python manage.py seed_data
python manage.py seed_data
```

### Start Server

```bash
python manage.py runserver 0.0.0.0:8000 --noreload
```

---

## 3. Running Verification & Smoke Tests

Verify system health:
```bash
curl http://localhost:8000/api/health/
# {"status": "ok"}
```

Run test suite:
```bash
python manage.py test
```

Run official smoke tests from bundle:
```bash
# AI/ML Track
python smoke_test.py --base-url http://localhost:8000 --track aiml

# Django Track / All checks
python smoke_test.py --base-url http://localhost:8000 --all
```

---

## 4. Key Architectural Decisions

All detailed answers to the 8 evaluation questions are documented in [`DECISIONS.md`](file:///D:/vikmo/DECISIONS.md) with line-by-line citations to source code.
