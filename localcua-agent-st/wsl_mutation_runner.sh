#!/usr/bin/env bash
# 🚀 Ultimate Mutation Testing Runner for WSL
set -e

# Dynamically find the project root based on the script's location
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

# Ensure Python loads your local code and NEVER uses cached .pyc files
export PATH="$HOME/.local/bin:$PATH"
export PYTHONPATH="$PROJECT_ROOT/localcua/backend:$PYTHONPATH"
export PYTHONDONTWRITEBYTECODE=1

echo "============================================="
echo "  Mutation Testing Runner (Clean Mode)"
echo "============================================="

# 0. Enforce Mutmut Version 2.4.4 (Required for HTML Report)
echo "[0/4] Enforcing mutmut version 2.4.4..."
python3 -m pip install mutmut==2.4.4 --break-system-packages > /dev/null 2>&1

# 1. Brutally clear out all caches and old mutants
echo "[1/4] Purging caches..."
rm -rf .mutmut-cache mutants/
find . -type d -name "__pycache__" -exec rm -rf {} +
find . -name "*.pyc" -delete

# 2. Verify baseline tests
echo "[2/4] Verifying baseline tests..."
python3 -B -m pytest tests/test_action_parser.py --ignore=tests/test_main_api.py -x -q

# 3. Run mutmut (reads configuration from setup.cfg)
echo "[3/4] Running mutmut..."
mutmut run

# 4. Generate the required HTML report
echo "============================================="
echo "  Generating HTML Report... [4/4]"
echo "============================================="
mutmut html

echo "Done! Open the 'html/' folder to view your results."
