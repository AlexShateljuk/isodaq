"""Make the repo root importable so tests can `import core.*` regardless of CWD."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
