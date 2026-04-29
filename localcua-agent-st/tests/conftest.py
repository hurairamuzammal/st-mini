import sys
import os
from pathlib import Path

# Add backend root to sys.path
# In mutmut sandbox, the current working directory is the project root
project_root = Path(os.getcwd())
backend_root = project_root / "localcua" / "backend"
sys.path.insert(0, str(backend_root))
