# 5-Minute Video Walkthrough Script (AI / ML Track)

This guide provides an exact, step-by-step 5-minute walkthrough script for the candidate's video submission. Open your code editor and browser, and follow these timestamps.

---

### Timing Breakdown (5 Minutes Total)

| Timestamp | Section | Screen to Show | Key Talking Points |
|---|---|---|---|
| **0:00 – 0:45** | **Introduction & Architecture Overview** | Editor root (`README.md`, file tree) | State track (AI/ML), mention full implementation of Core, Assistant, Forecasting, and bonus Portal. |
| **0:45 – 2:30** | **Deep Dive: Assistant Tool-Calling Loop** | `core/services/assistant_service.py` | Step-by-step walkthrough of message arrival, Gemini function calling, database grounding, and prompt defense. |
| **2:30 – 3:45** | **Deep Dive: Demand Forecasting & Safety Stock** | `ml/train_forecast.py` & `core/views.py` | Explain time-series model (promo multipliers, Diwali calendar shift, Croston method, zero-leakage line). |
| **3:45 – 4:30** | **Live Demo: Dealer Portal & Smoke Test** | Browser `http://localhost:8000/` & Terminal | Show live parts search, AI assistant chatting with tool badges, reorder suggestions, and `smoke_test.py --all` 12/12 pass. |
| **4:30 – 5:00** | **Conclusion & Technical Rigor** | `DECISIONS.md` | Highlight strict relational schema, `models.PROTECT`, and concurrency row locking. |

---

### Step-by-Step Script

#### 1. Introduction (0:00 - 0:45)
> *"Hello! My name is [Your Name], and this is my submission for the VIKMO DealerDesk Take-Home Assignment for the AI/ML Intern track.*  
> *In this project, I built an end-to-end AI-native distribution system for spare parts dealers: our PostgreSQL Core with strict financial rounding and concurrency protection, our Dealer Assistant using Google Gemini with function calling, our 4-week Demand Forecasting and Reorder Engine, resilient Supplier Sync, and an interactive Dealer Portal frontend.*  
> *Let's jump straight into the code."*

#### 2. The Assistant Tool-Calling Loop (0:45 - 2:30) *(Mandatory for AI/ML Track)*
*(Open [`core/services/assistant_service.py`](file:///d:/vikmo/core/services/assistant_service.py))*

> *"Here in `core/services/assistant_service.py`, our GenAI assistant is built around a rigorous tool-calling loop using Google Gemini (`gemini-1.5-flash`):*
> 1. *When a dealer sends a message to `POST /api/assistant/chat/`, we hydrate conversation history in `chat_turn` (lines 465–479) using `AssistantMessage` filtered by `session_id`, ensuring strict session-level memory isolation.*
> 2. *We expose three typed tool declarations to the model (lines 18–85):*
>    - *`search_parts`: searches the catalogue by query keyword, category, and vehicle fitment.*
>    - *`check_stock`: performs live inventory and price lookups for an exact SKU.*
>    - *`create_draft_order`: creates a DRAFT order in the database without deducting warehouse stock.*
> 3. *Our system instructions (lines 87–99) enforce crucial security and business rules:*
>    - *Untrusted Data Defense: catalogue descriptions from third-party suppliers are treated strictly as passive data, preventing indirect prompt injection.*
>    - *Zero Hallucination: the model is explicitly forbidden from generating prices or quantities from memory; all figures must come from tool executions.*
>    - *Ambiguity Handling: if a dealer asks for a part without mentioning their vehicle make/model, the assistant asks a clarifying question before recommending.*
> 4. *In `run_gemini_turn` (lines 246–324), we execute up to 5 tool-calling turns. When Gemini returns function calls, we intercept them (lines 284–293), execute the queries against PostgreSQL locally, and return structured `functionResponse` blocks.*
> 5. *Notice line 215 in `execute_check_stock`: stock and pricing are queried live from PostgreSQL at that exact millisecond.*
> 6. *When the model finishes calling tools, we extract all cited SKUs, validate them against the database (line 438), and persist the turn for session memory."*

#### 3. Forecasting & Safety Stock Engine (2:30 - 3:45)
*(Open [`ml/train_forecast.py`](file:///d:/vikmo/ml/train_forecast.py) and [`core/views.py`](file:///d:/vikmo/core/views.py#L402))*

> *"Next, let's explore our Demand Forecasting and Inventory Reorder Engine:*
> 1. *In `ml/train_forecast.py`, we forecast weekly sales for all 40 catalogue SKUs across the 4 future forecast weeks starting September 28, 2026. The generated file `ml/forecast.csv` contains exactly 160 non-negative predictions.*
> 2. *Our model handles the complex distribution dynamics described in the assignment:*
>    - *Promotions: we calculate SKU-specific promo multipliers from historical uplift (lines 118–129).*
>    - *Moving Diwali Festival Dates: Diwali shifts annually (Nov 1 in 2024, Oct 21 in 2025, Nov 8 in 2026). In lines 46–54 and 130–143, we index sales by relative week distance to Diwali, capturing the authentic pre-Diwali demand ramp.*
>    - *Intermittent Demand: for low-volume SKUs like `BRK-1088` with over 50 zero-demand weeks, we implement Croston's method (lines 57–80) separating demand interval from demand size.*
>    - *New SKUs: for SKUs with only 6 weeks of history like `BRK-1105`, we apply a recency-weighted ramp without assuming false 52-week seasonality.*
> 3. *Crucially, line 233 prevents any future data leakage by strictly filtering training history before the holdout test date (`week_start < test_weeks[0]`).*
> 4. *Evaluating on historical holdout data, our baseline WAPEs are: Naive Last Value at 29.96%, 4-Week Moving Average at 24.68%, and Seasonal Naive at 32.31%.*
> 5. *In `core/views.py` (lines 402–488), the `GET /api/reorder-suggestions/` endpoint implements the exact safety stock formula from the PDF:*
>    - *`safety_stock = ceil(1.65 * sigma * sqrt(L))`, where sigma is the sample standard deviation ($n-1$) of the last 12 weeks of sales,*
>    - *`reorder_point = lead_time_demand + safety_stock`, and*
>    - *`reorder_qty = max(0, ceil(reorder_point - current_stock))`, querying current live inventory from PostgreSQL."*

#### 4. Live Demo & Verification (3:45 - 4:30)
*(Switch to browser at `http://localhost:8000/` and terminal)*

> *"Let's see the application in action:*
> - *Here is our interactive DealerDesk Portal: we can browse 600 catalogue items, filter by category or vehicle (e.g., 'KTM Duke 390'), and check stock in real time.*
> - *In the AI Assistant tab, when I ask 'Do you have brake pads?', it asks a clarifying question for vehicle model. When I specify 'KTM Duke 390', it executes `search_parts`, displays `BRK-1001`, shows live stock and pricing, and tags the cited SKU badge.*
> - *In the Demand & Reorder tab, all 40 forecast SKUs display calculated lead-time demand, safety stock, and live reorder quantities.*
> - *Now let's switch to the terminal and execute the test suites:*
>   - *First: `python manage.py test` — 13 out of 13 unit tests pass.*
>   - *Second: `python smoke_test.py --base-url http://localhost:8000 --all` — all 12 checks pass across Core, Order Engine, Supplier Sync, Dealer Assistant, and Forecasting."*

#### 5. Conclusion (4:30 - 5:00)
*(Show [`DECISIONS.md`](file:///d:/vikmo/DECISIONS.md))*

> *"To summarize: our system enforces PostgreSQL in production, protects against overselling via `select_for_update()` row locking, calculates 18% GST with `ROUND_HALF_UP` to the paisa, and provides a grounded, safe AI assistant and statistical demand forecasting engine.*  
> *All 8 evaluation questions with code citations, trade-offs, and future improvements are detailed in `DECISIONS.md`.*  
> *Thank you!"*
