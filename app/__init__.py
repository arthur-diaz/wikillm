import sys
from pathlib import Path

# Make scripts/wiki.py importable as `import wiki`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
