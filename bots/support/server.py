# server.py - Support Bot API Server
"""
Standalone FastAPI server for Emerald Support Bot.
Can be run via: uvicorn bots.support.server:app --port 8001
Or integrated into the main bot.py.
"""

import logging
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Create FastAPI app
app = FastAPI(
    title="Emerald Support Bot API",
    description="Support ticket system with multi-tenancy",
    version="1.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Import and include router
try:
    from .support_api import router as support_router
    app.include_router(support_router)
    logging.info("✅ Support Bot API router included")
except ImportError as e:
    logging.error(f"❌ Failed to import support_api router: {e}")

@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "ok", "service": "emerald-support"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
