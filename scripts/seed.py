"""Standart missiya va yutuqlarni bazaga yozadi. Ishlatish: python scripts/seed.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app import services  # noqa: E402
from backend.app.database import SessionLocal  # noqa: E402

if __name__ == "__main__":
    with SessionLocal() as db:
        services.seed_defaults(db)
    print("Standart ma'lumotlar yozildi.")
