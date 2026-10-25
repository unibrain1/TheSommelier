---
name: pipeline-test
description: Run the full pipeline locally and validate all generated JSON outputs
disable-model-invocation: true
---

# Pipeline Test

Run the full pipeline end-to-end and validate all generated outputs.

## Workflow

1. **Run the pipeline**:

   ```bash
   bash fetch.sh
   ```

   Capture the exit code and full output.

2. **Validate generated JSON files** exist and are well-formed:

   For each of these files, verify it exists, is valid JSON, and has expected top-level keys:

   | File | Expected Keys |
   |------|---------------|
   | `data/plan.json` | `allWeeks` (array of 52), `quarterInfo`, `changelog` |
   | `data/inventory.json` | non-empty array of wine objects |
   | `data/menu.json` | array of meal objects |
   | `site/plan.json` | `allWeeks`, `quarterInfo`, `changelog` |
   | `site/pairing_suggestions.json` | array of suggestion objects |
   | `site/report.json` | object with inventory diff data |

   ```bash
   python3 -c "
   import json, sys
   files = {
       'data/plan.json': ['allWeeks', 'quarterInfo', 'changelog'],
       'site/plan.json': ['allWeeks', 'quarterInfo', 'changelog'],
       'site/pairing_suggestions.json': None,
       'site/report.json': None,
   }
   errors = []
   for path, keys in files.items():
       try:
           with open(path) as f:
               data = json.load(f)
           if keys:
               missing = [k for k in keys if k not in data]
               if missing:
                   errors.append(f'{path}: missing keys {missing}')
           if isinstance(data, dict) and 'allWeeks' in data:
               n = len(data['allWeeks'])
               if n != 52:
                   errors.append(f'{path}: allWeeks has {n} entries, expected 52')
       except FileNotFoundError:
           errors.append(f'{path}: FILE NOT FOUND')
       except json.JSONDecodeError as e:
           errors.append(f'{path}: INVALID JSON: {e}')
   if errors:
       print('VALIDATION FAILURES:')
       for e in errors:
           print(f'  - {e}')
       sys.exit(1)
   else:
       print('All JSON files valid.')
   "
   ```

3. **Spot-check plan content**:

   ```bash
   python3 -c "
   import json
   with open('site/plan.json') as f:
       plan = json.load(f)
   weeks = plan['allWeeks']
   print(f'Plan: {len(weeks)} weeks')
   empty_notes = sum(1 for w in weeks for wine in w.get('wines', []) if not wine.get('note'))
   print(f'Wines missing notes: {empty_notes}')
   print(f'First week: {weeks[0][\"weekLabel\"]}')
   print(f'Last week: {weeks[-1][\"weekLabel\"]}')
   "
   ```

4. **Launch Playwright** to verify the site renders (if Playwright MCP is available):

   - Navigate to `file:///Users/jimboone/Developer/Web/TheSommelier/site/index.html`
   - Take a snapshot and verify:
     - The plan table has rows
     - Tab navigation works (Plan, Pairings, Inventory)
     - No JavaScript errors in the console

5. **Report results**:

   - Pipeline exit code and any errors from stdout/stderr
   - JSON validation pass/fail for each file
   - Plan spot-check summary
   - Site rendering result (if Playwright was used)
   - Overall: PASS or FAIL with details
