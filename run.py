"""
Vantage — Recruitment OS for MGIT
Entry point: creates and runs the Flask application.
"""
import os
from dotenv import load_dotenv

# Load environment variables from .env file before creating the app
load_dotenv()

from app import create_app

# Note: FLASK_ENV is deprecated in Flask 2.2+. Use FLASK_DEBUG or custom env var as fallback.
config_name = os.getenv("FLASK_ENV", "development")
app = create_app(config_name)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=app.config.get("DEBUG", False))
