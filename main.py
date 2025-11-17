import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
import requests

from database import create_document, get_documents, db
from schemas import Message as MessageSchema

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class MessageCreate(BaseModel):
    to: str = Field(..., description="Destination phone number in E.164 format, e.g., +15551234567")
    body: str = Field(..., min_length=1, max_length=1600, description="SMS message body")


TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_FROM = os.getenv("TWILIO_FROM")


def send_sms_via_twilio(to: str, body: str) -> Dict[str, Any]:
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_FROM):
        return {
            "provider": "twilio",
            "status": "queued",
            "sid": None,
            "error": "Twilio credentials not configured. Message queued (simulation).",
        }

    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"
    data = {
        "To": to,
        "From": TWILIO_FROM,
        "Body": body,
    }
    try:
        resp = requests.post(url, data=data, auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN), timeout=20)
        try:
            payload = resp.json()
        except Exception:
            payload = {"raw": resp.text}
        if 200 <= resp.status_code < 300:
            return {
                "provider": "twilio",
                "status": payload.get("status", "sent"),
                "sid": payload.get("sid"),
                "error": None,
            }
        else:
            return {
                "provider": "twilio",
                "status": "failed",
                "sid": payload.get("sid"),
                "error": payload.get("message") or f"HTTP {resp.status_code}",
            }
    except Exception as e:
        return {
            "provider": "twilio",
            "status": "failed",
            "sid": None,
            "error": str(e),
        }


@app.get("/")
def read_root():
    return {"message": "Free SMS Messaging API"}


@app.get("/api/hello")
def hello():
    return {"message": "Hello from the backend API!"}


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }

    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Configured"
            response["database_name"] = db.name if hasattr(db, 'name') else "✅ Connected"
            response["connection_status"] = "Connected"
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"

    import os as _os
    response["database_url"] = "✅ Set" if _os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if _os.getenv("DATABASE_NAME") else "❌ Not Set"
    return response


@app.get("/schema")
def get_schema():
    # Expose simple schema info for the database viewer
    return {
        "message": {
            "fields": {
                "to": {"type": "string", "description": "Destination phone"},
                "body": {"type": "string", "description": "Message body"},
                "status": {"type": "string", "description": "Delivery status"},
                "provider": {"type": "string", "description": "Provider used"},
                "sid": {"type": "string", "description": "Provider SID"},
                "error": {"type": "string", "description": "Error if any"},
            }
        }
    }


def serialize_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    d = dict(doc)
    if "_id" in d:
        d["id"] = str(d.pop("_id"))
    for k, v in list(d.items()):
        if isinstance(v, datetime):
            d[k] = v.isoformat()
    return d


@app.post("/api/messages")
def create_message(payload: MessageCreate):
    # Basic phone validation: starts with + and digits 8-15 total digits after +
    if not payload.to.startswith("+") or not payload.to[1:].isdigit() or len(payload.to) < 8 or len(payload.to) > 18:
        raise HTTPException(status_code=400, detail="Invalid phone number format. Use E.164 like +15551234567")

    # Attempt send via provider (Twilio if configured)
    provider_result = send_sms_via_twilio(payload.to, payload.body)

    message_doc = MessageSchema(
        to=payload.to,
        body=payload.body,
        status=provider_result.get("status", "queued"),
        provider=provider_result.get("provider"),
        sid=provider_result.get("sid"),
        error=provider_result.get("error"),
    )

    inserted_id = create_document("message", message_doc)

    # Build response
    response = message_doc.model_dump()
    response.update({
        "id": inserted_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return response


@app.get("/api/messages")
def list_messages(limit: int = 25):
    docs = get_documents("message", {}, limit=limit)
    return [serialize_doc(d) for d in docs]


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
