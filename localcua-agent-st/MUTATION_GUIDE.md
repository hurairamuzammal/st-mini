# 🛠️ Mutation Testing & Reproducibility Guide
**Project:** LocalCUA Agent Backend  
**Student:** Muhammad Abu Huraira (22F-3853)

This guide explains how to reproduce the 100% mutation score achieved in the Software Testing Mini Project.

---

## 1. Environment Requirements
> [!IMPORTANT]
> All mutation commands **MUST** be run inside **WSL (Ubuntu)**. 
> Windows native Python will fail due to `PermissionError` when `mutmut` attempts to swap files.

### 1.1 How to Start WSL
1.  **Open PowerShell or Command Prompt** on your Windows machine.
2.  Type `wsl` and press Enter. This will log you into your Ubuntu/Linux distribution.
3.  **Navigate to your project folder**: WSL maps your Windows C: drive to `/mnt/c/`.
    ```bash
    cd "/mnt/c/Users/Muhammad Abu Huraira/Desktop/st/localcua-agent-st"
    ```

### 1.2 Prerequisites
```bash
# Install specific version of mutmut that supports HTML reports!
pip install mutmut==2.4.4 pytest pytest-cov --break-system-packages
```

---

## 2. Configuration (`setup.cfg`)
The tool is pre-configured to target only the core logic file to save execution time.
```ini
[mutmut]
paths_to_mutate=localcua/backend/agent/action_parser.py
tests_dir=tests/
runner=pytest -x tests/test_action_parser.py
```

---

## 3. How to Run the Testing Lifecycle

### Step A: Baseline Run (Task 2)
To see the initial survived mutants:
```bash
# Run mutations (this will now ignore the problematic API tests)
mutmut run

# Generate the baseline HTML report
# NOTE: If 'mutmut html' says "No such command", you are on version 3+.
# You MUST downgrade: pip install mutmut==2.4.4 --break-system-packages
mutmut html
```
> [!TIP]
> If `mutmut html` still fails, verify your version with `mutmut --version`. It must be 2.4.4 for the HTML reporter to exist.

### Step B: Inspecting a Specific Mutant
To see exactly what code `mutmut` changed for a specific ID (e.g., Mutant #17):
```bash
mutmut show 17
```

### Step C: Killing Mutants (The WSL Runner)
I have created an automated script `wsl_mutation_runner.sh` that cleans the environment and runs the "Killer" suite:
```bash
chmod +x wsl_mutation_runner.sh
./wsl_mutation_runner.sh
```

---

## 4. How to Verify Results
After the runner completes, you can verify the status of the mutants:

1. **Terminal Summary:**
   ```bash
   mutmut results
   ```
   *You should see `Killed: 147, Survived: 0`.*

2. **Final HTML Report:**
   ```bash
   mutmut html
   # The report will be updated in the 'html' folder.
   ```

---

## 5. Deliverables Locations
- **Final Report:** `documentation.tex` (Export to PDF).
- **Killer Tests:** `tests/test_action_parser.py`.
- **Target Logic:** `localcua/backend/agent/action_parser.py`.
- **Raw Reports:** Committed under `reports/mutation_final/`.
