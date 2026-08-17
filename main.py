import os
from fastapi import FastAPI, Request, HTTPException
from supabase import create_client, Client

app = FastAPI(title="Opricely AI Agent Backend")

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# Safely check environment variables
if SUPABASE_URL and SUPABASE_KEY:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

@app.get("/")
def read_root():
    return {"status": "Opricely Backend Engine Running"}

@app.post("/webhooks/shopify")
async def shopify_webhook(request: Request):
    payload = await request.json()
    product_id = payload.get("id") or payload.get("product_id")
    title = payload.get("title", "Unknown Product")
    current_price = float(payload.get("variants", [{}])[0].get("price", 0.0) or 0.0)
    
    if not product_id:
        raise HTTPException(status_code=400, detail="Invalid payload: product_id missing")
        
    print(f"Received webhook for Product: {title} (ID: {product_id}) @ ${current_price}")
    
    return {
        "status": "success",
        "message": f"Processed webhook for {title}",
        "product_id": str(product_id)
    }
