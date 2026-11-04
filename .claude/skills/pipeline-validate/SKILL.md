---
name: pipeline-validate
description: Validate existing JSON outputs without re-running the pipeline
disable-model-invocation: true
---

# Pipeline Validate

Quick validation of existing generated JSON files. Does NOT fetch or regenerate — just checks what's already on disk.

## Workflow

1. **Validate JSON files** exist, are well-formed, and have expected structure:

   ```bash
   python3 -c "
   import json, sys, os, time
   sys.path.insert(0, 'scripts')
   import settings

   # With the menu feature off, the pipeline makes no menu or pairing files.
   menu_files = ('data/menu.json', 'site/pairing_suggestions.json')
   menu_on = settings.feature('menu').on

   files = {
       'data/plan.json': ['allWeeks', 'quarterInfo', 'changelog'],
       'data/inventory.json': None,
       'data/menu.json': None,
       'site/plan.json': ['allWeeks', 'quarterInfo', 'changelog'],
       'site/pairing_suggestions.json': None,
       'site/report.json': None,
   }
   errors = []
   for path, keys in files.items():
       if path in menu_files and not menu_on:
           print(f'  SKIP {path} (menu feature is off)')
           continue
       try:
           age_hours = (time.time() - os.path.getmtime(path)) / 3600
           with open(path) as f:
               data = json.load(f)
           size = len(data) if isinstance(data, list) else len(data.keys())
           status = f'{size} items' if isinstance(data, list) else f'{size} keys'
           if keys:
               missing = [k for k in keys if k not in data]
               if missing:
                   errors.append(f'{path}: missing keys {missing}')
                   status += f' MISSING {missing}'
           if isinstance(data, dict) and 'allWeeks' in data:
               n = len(data['allWeeks'])
               if n != 52:
                   errors.append(f'{path}: allWeeks has {n} entries, expected 52')
                   status += f' BAD WEEK COUNT ({n})'
           if isinstance(data, list) and len(data) == 0:
               errors.append(f'{path}: empty array')
               status += ' EMPTY'
           print(f'  OK  {path} ({status}, {age_hours:.1f}h old)')
       except FileNotFoundError:
           errors.append(f'{path}: FILE NOT FOUND')
           print(f'  MISS {path}')
       except json.JSONDecodeError as e:
           errors.append(f'{path}: INVALID JSON: {e}')
           print(f'  BAD  {path} ({e})')

   print()
   if errors:
       print('FAILURES:')
       for e in errors:
           print(f'  - {e}')
       sys.exit(1)
   else:
       print('All files valid.')
   "
   ```

2. **Spot-check plan content**:

   ```bash
   python3 -c "
   import json
   with open('site/plan.json') as f:
       plan = json.load(f)
   weeks = plan['allWeeks']
   print(f'Plan: {len(weeks)} weeks')
   print(f'First week: {weeks[0][\"weekLabel\"]}')
   print(f'Last week: {weeks[-1][\"weekLabel\"]}')
   empty_notes = sum(1 for w in weeks for wine in w.get('wines', []) if not wine.get('note'))
   total_wines = sum(len(w.get('wines', [])) for w in weeks)
   print(f'Wines: {total_wines} total, {empty_notes} missing notes')
   "
   ```

3. **Report**: PASS or FAIL with details. If any files are more than 24 hours old, note they may be stale.
