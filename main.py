import os
import datetime
from fastapi import FastAPI, Request, HTTPException
from supabase import create_client, Client
from google import genai
from google.genai import types
from pydantic import BaseModel

app = FastAPI(title="Opricely AI Agent Backend")

# Initialize Clients
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None
ai_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# Pydantic Schema for Gemini Structured Output
class PricingRecommendation(BaseModel):
    should_change_price: bool
    proposed_price: float
    reasoning: str

@app.get("/")
def read_root():
    return {"status": "Opricely Backend Engine Running"}

@app.post("/webhooks/shopify")
async def shopify_webhook(request: Request):
    payload = await request.json()
    
    product_id = str(payload.get("id") or payload.get("product_id", ""))
    title = payload.get("title", "Sample Product")
    variant = payload.get("variants", [{}])[0]
    current_price = float(variant.get("price", 0.0) or 10.0)
    cost_price = float(variant.get("cost", 0.0) or (current_price * 0.5)) # Default 50% cost baseline
    inventory_quantity = int(variant.get("inventory_quantity", 15))

    if not product_id:
        raise HTTPException(status_code=400, detail="Invalid product ID")

    # 1. Fetch Store Rules from Supabase (or fallback to defaults)
    store_rules = {"min_margin_percent": 20.0, "max_price_increase_percent": 15.0, "cooldown_hours": 6}
    if supabase:
        rules_res = supabase.table("pricing_rules").select("*").limit(1).execute()
        if rules_res.data:
            store_rules = rules_res.data[0]

    cooldown_hours = store_rules.get("cooldown_hours", 6)
    min_margin = store_rules.get("min_margin_percent", 20.0) / 100.0
    max_increase = store_rules.get("max_price_increase_percent", 15.0) / 100.0

    # 2. Check 6-Hour Cooldown Guardrail
    if supabase:
        recent_logs = supabase.table("pricing_logs") \
            .select("created_at") \
            .eq("product_id", product_id) \
            .order("created_at", desc=True) \
            .limit(1) \
            .execute()

        if recent_logs.data:
            last_change = datetime.datetime.fromisoformat(recent_logs.data[0]["created_at"].replace("Z", "+00:00"))
            hours_passed = (datetime.datetime.now(datetime.timezone.utc) - last_change).total_seconds() / 3600.0
            if hours_passed < cooldown_hours:
                return {
                    "status": "skipped",
                    "reason": f"Cooldown active. Last change was {hours_passed:.1f} hours ago (Minimum interval: {cooldown_hours} hours)."
                }

    # 3. Call Gemini AI Agent with Structured Output
    system_prompt = f"""
    You are an expert dynamic pricing AI for an e-commerce store.
    Analyze product inventory, current price, and unit cost to decide if a price update is warranted.
    
    Rules:
    - Minimum Profit Margin Allowed: {min_margin * 100}%
    - Maximum Allowed Single Price Increase: {max_increase * 100}%
    - Product Unit Cost: ${cost_price}
    - Current Retail Price: ${current_price}
    - Units in Stock: {inventory_quantity}
    """

    user_prompt = f"Evaluate inventory levels ({inventory_quantity} units left) and current price (${current_price}) for '{title}' and recommend an optimal price adjustment."

    try:
        response = ai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                response_schema=PricingRecommendation,
                temperature=0.2
            ),
        )
        recommendation = PricingRecommendation.model_validate_json(response.text)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gemini API Error: {str(e)}")

    # 4. Hard Code Safety Guardrail Validation
    min_allowable_price = round(cost_price * (1 + min_margin), 2)
    max_allowable_price = round(current_price * (1 + max_increase), 2)

    final_price = recommendation.proposed_price
    
    # Enforce minimum profit ceiling/floor regardless of AI output
    if final_price < min_allowable_price:
        final_price = min_allowable_price
    elif final_price > max_allowable_price:
        final_price = max_allowable_price

    # 5. Save Decision to Supabase Audit Log
    if supabase and recommendation.should_change_price:
        supabase.table("pricing_logs").insert({
            "product_id": product_id,
            "product_title": title,
            "old_price": current_price,
            "proposed_price": final_price,
            "ai_reasoning": recommendation.reasoning,
            "status": "pending"
        }).execute()

    return {
        "status": "success",
        "title": title,
        "current_price": current_price,
        "recommended_price": final_price,
        "should_change": recommendation.should_change_price,
        "ai_reasoning": recommendation.reasoning
    }
      
