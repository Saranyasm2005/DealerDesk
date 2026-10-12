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
> *In this project, I built the complete AI-native operating system for spare parts distribution: the Backend Core, the full Order Engine with concurrency and GST rounding, the Dealer Assistant using Gemini with tool calling, the 4-week Demand Forecasting model with reorder recommendations, resilient Supplier Sync, and an interactive Dealer Portal frontend.*  
> *Let's jump straight into the code."*

#### 2. The Assistant Tool-Calling Loop (0:45 - 2:30) *(Mandatory for AI/ML Track)*
*(Open [`core/services/assistant_service.py`](file:///D:/vikmo/core/services/assistant_service.py))*

> *"Here in `assistant_service.py`, lines 180 to 260 contain our tool-calling loop:*
> 1. *When a dealer sends a message to `POST /api/assistant/chat/`, we first hydrate the conversation history from `AssistantMessage` using `session_id` (line 384).*
> 2. *We declare three explicit tools to Gemini:*
>    - *`search_parts` for searching the catalogue by vehicle and query,*
>    - *`check_stock` for live price and quantity lookups, and*
>    - *`create_draft_order` for placing items into draft status.*
> 3. *In `run_gemini_turn` (line 180), we pass the messages alongside `SYSTEM_INSTRUCTION` (line 87). Here, we establish our prompt injection defense: untrusted supplier catalogue descriptions are treated as passive data, never as commands.*
> 4. *When Gemini responds with function calls, our loop intercepts them (lines 224–245), executes the corresponding SQL queries locally against PostgreSQL, and appends the structured results into a `functionResponse` message.*
> 5. *Notice line 151 in `execute_check_stock`: stock and pricing are never memorized by the LLM. They are queried live from the database at that exact moment.*
> 6. *When the model finishes calling tools, it generates a final grounded response. We parse and validate cited SKUs against the database (line 356) and record the turn in `AssistantMessage`."*

#### 3. Forecasting & Safety Stock Engine (2:30 - 3:45)
*(Open [`ml/train_forecast.py`](file:///D:/vikmo/ml/train_forecast.py) and [`core/views.py`](file:///D:/vikmo/core/views.py#L300))*

> *"Next, let's look at demand forecasting in `ml/train_forecast.py`:*
> 1. *We forecast weekly sales for all 40 SKUs across the 4 forecast weeks leading into October 2026.*
> 2. *Our model handles real-world distribution phenomena:*
>    - *Promotions: we compute SKU-specific promo multipliers from history (lines 90–101).*
>    - *Diwali Shift: Diwali moves each year (Nov 1 in 2024, Oct 21 in 2025, Nov 8 in 2026). We align history by relative week distance to Diwali (lines 103–113) to capture the pre-Diwali demand ramp.*
>    - *Intermittent SKUs: for SKUs like `BRK-1088` with over 50 zero-sales weeks, we use Croston's method (line 52) to avoid over-predicting.*
>    - *New SKUs: for products with only 6 weeks of data, we apply a weighted recency ramp without false 52-week seasonality.*
> 3. *Line 192 prevents any future data leakage by strictly filtering training data before the holdout start date.*
> 4. *In `core/views.py` (line 330), `/api/reorder-suggestions/` implements the exact formula: `safety_stock = ceil(1.65 * sigma * sqrt(L))`, where sigma is the sample standard deviation over the last 12 weeks, and reorder point subtracts live warehouse stock."*

#### 4. Live Demo & Verification (3:45 - 4:30)
*(Switch to browser at `http://localhost:8000/` and terminal)*

> *"Let's see it live:*
> - *Here is the DealerDesk Portal: we can browse the 600 catalogue items, filter by vehicle (e.g., 'Duke 390'), and see live stock.*
> - *In the AI Assistant tab, if I ask 'Do you have brake pads for a Duke 390?', it calls `search_parts`, finds `BRK-1001`, displays the live stock and price, and tags the cited SKU.*
> - *In the Demand & Reorder tab, we see all 40 SKUs with their lead-time demand, calculated safety stock, and real-time reorder quantities.*
> - *And in the terminal, let's run the smoke test suite:*
>   `python smoke_test.py --base-url http://localhost:8000 --all`
>   *As you can see: 12 out of 12 checks pass across Core, Order Engine, Supplier Sync, Assistant, and Forecasting."*

#### 5. Conclusion (4:30 - 5:00)
> *"All design rationales, including our schema constraints, `models.PROTECT` foreign key choices, and baseline evaluation numbers (beating naive and seasonal baselines on WAPE) are fully documented in `DECISIONS.md`.*  
> *Thank you, and I look forward to the live technical round!"*
