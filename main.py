import os
import json
import datetime
from fastapi import FastAPI, Request, HTTPException
from supabase import create_client, Client
import google.generativeai as genai

app = FastAPI(title="Opricely AI Agent Backend")

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

@app.get("/")
def read_root():
    return {"status": "Opricely Backend Engine Running"}

@app.post("/webhooks/shopify")
async def shopify_webhook(request: Request):
    payload = await request.json()
    
    product_id = str(payload.get("id") or payload.get("product_id", ""))
    title = payload.get("title", "Sample Product")
    variants = payload.get("variants", [{}])
    variant = variants[0] if variants else {}
    current_price = float(variant.get("price", 0.0) or 10.0)
    cost_price = float(variant.get("cost", 0.0) or (current_price * 0.5))
    inventory_quantity = int(variant.get("inventory_quantity", 15))

    if not product_id:
        raise HTTPException(status_code=400, detail="Invalid product ID")

    # Safety Guardrail Limits
    min_margin = 0.20  # 20%
    max_increase = 0.15  # 15%

    prompt = f"""
    You are an e-commerce dynamic pricing AI.
    Product: {title}
    Current Price: ${current_price}
    Unit Cost: ${cost_price}
    Inventory Left: {inventory_quantity}

    Rules:
    - Minimum Profit Margin: {min_margin * 100}%
    - Max Single Price Increase: {max_increase * 100}%

    Respond STRICTLY in raw JSON format with these exact keys:
    {{
        "should_change_price": true,
        "proposed_price": 88.0,
        "reasoning": "Explanation here"
    }}
    """

    try:
        model = genai.GenerativeModel('gemini-2.5-flash')
        response = model.generate_content(
            prompt,
            generation_config={"response_mime_type": "application/json"}
        )
        rec = json.loads(response.text)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gemini Execution Error: {str(e)}")

    proposed_price = float(rec.get("proposed_price", current_price))
    should_change = bool(rec.get("should_change_price", False))
    reasoning = str(rec.get("reasoning", ""))

    # Hard Safety Guardrail Overrides
    min_allowable = round(cost_price * (1 + min_margin), 2)
    max_allowable = round(current_price * (1 + max_increase), 2)

    if proposed_price < min_allowable:
        proposed_price = min_allowable
    elif proposed_price > max_allowable:
        proposed_price = max_allowable

    # Log to Supabase
    if supabase and should_change:
        try:
            supabase.table("pricing_logs").insert({
                "product_id": product_id,
                "product_title": title,
                "old_price": current_price,
                "proposed_price": proposed_price,
                "ai_reasoning": reasoning,
                "status": "pending"
            }).execute()
        except Exception as log_err:
            print(f"Supabase Log Warning: {log_err}")

    return {
        "status": "success",
        "title": title,
        "current_price": current_price,
        "recommended_price": proposed_price,
        "should_change": should_change,
        "ai_reasoning": reasoning
    }
      
