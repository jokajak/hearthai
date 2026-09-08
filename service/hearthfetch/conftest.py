import sys
from pathlib import Path

# The corpus module lives beside the tests and is imported by name.
sys.path.insert(0, str(Path(__file__).parent / "tests"))
