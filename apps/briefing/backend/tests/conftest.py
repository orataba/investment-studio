import os
from pathlib import Path
import sys

os.environ.setdefault("INVESTMENT_STUDIO_BRIEFING_DATABASE_URL", "sqlite+pysqlite:///:memory:")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
