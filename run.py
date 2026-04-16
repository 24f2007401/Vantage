"""
Vantage — Recruitment OS for MGIT
Entry point: creates and runs the Flask application.

DEVELOPMENT ONLY — do not use this script in production.
In production, start the server with:
  gunicorn "app:create_app()" --config gunicorn.conf.py
"""
import os
import sys
from dotenv import load_dotenv

# Load environment variables from .env file before creating the app
load_dotenv()

# Hard block: refuse to run the dev server if FLASK_ENV=production.
# Production deployments must use gunicorn (see Procfile / gunicorn.conf.py).
if os.environ.get("FLASK_ENV") == "production":
    sys.exit(
        "ERROR: Do not use run.py in production.\n"
        "Use: gunicorn \"app:create_app()\" --config gunicorn.conf.py\n"
        "Or:  see Procfile for the exact command."
    )

from app import create_app

config_name = os.getenv("FLASK_ENV", "development")
app = create_app(config_name)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    # debug=True is intentional here — this script is for local development only.
    app.run(host="0.0.0.0", port=port, debug=True)
