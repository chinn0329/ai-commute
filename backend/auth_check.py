"""
Run this locally ONCE before deploying to Render.
Encodes your token.json to base64 for safe env variable storage.
"""
import json
import base64
from pathlib import Path

TOKEN_PATH = Path(__file__).parent / "token.json"

if TOKEN_PATH.exists():
    with open(TOKEN_PATH, "r") as f:
        token_data = f.read()

    encoded = base64.b64encode(token_data.encode()).decode()

    print("✅ token.json encoded successfully!")
    print("\nCopy this ENTIRE string into Render as GOOGLE_TOKEN_B64:")
    print("\n" + "="*60)
    print(encoded)
    print("="*60)
    print(f"\nLength: {len(encoded)} characters")

else:
    print("❌ token.json not found in backend/ folder.")
    print("Run this first: python calendar_service.py")