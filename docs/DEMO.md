# Demo and F5 on the InnoBeweegLab laptop (3 December 2026)

Two things happen at the visit:

- **F5**: one complete report of the stage-1 winner (E2, seed 42, CPU-only) on the InnoBeweegLab laptop
  (HP ProBook, Core 5 120U, 16 GB). It takes about 1.5 to 2 hours, so it starts first.
- **The demo** (15 minutes) while F5 runs. It uses Alex's laptop and a finished E2 run, because the
  InnoBeweegLab laptop has no finished run until F5 is done. The same steps work there afterwards.

Nothing leaves either laptop. No hosted model is used. The package holds no research data: the
InnoBeweegLab laptop uses its own copy of the Oss data.

## Before the visit (Alex's laptop)

1. Copy the workspace for the demo, so the stage-1 workspace stays untouched:
   `robocopy workspace workspace-demo /E /XD synthetic` (from the repo root).
2. Charger, the package on a USB drive (`InnoBeweegLab_offline\`, about 6.4 GB), this file printed.

## At the visit: install and start F5 (about 20 minutes)

0. Written agreement first: the data stays on this laptop and is pseudonymised (see the plan).
1. Copy `InnoBeweegLab_offline\` from the USB drive to the Desktop.
2. Right-click `install_offline.ps1` > Run with PowerShell (or the command below). Windows asks once
   for admin approval for Git: click Yes. It ends with the test count and "smoke test OK".

   ```powershell
   powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\Desktop\InnoBeweegLab_offline\install_offline.ps1"
   ```

3. Charger in. Close Teams, Outlook, browsers (F5 needs at least 9 GB free memory; the script waits
   and lists what to close). Power mode "Best performance".
4. Start F5 (the exact command; fill in where the Oss folder is on this laptop):

   ```powershell
   powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\InnoBeweegLab\run_f5.ps1" -DataDir "<path>\Perspectieven Oss"
   ```

   This runs `experiments.cli run E2 --case euterpepark-oss --seed 42 --cold --cpu-only` from the
   freeze-2 code (44def27). Leave the window open and the lid open. It prints PASS or NOT MET at the end.

**If the visit ends before F5 is done:** with InnoBeweegLab's OK the run continues; collect
`InnoBeweegLab\app\workspace\runs\*.json` and the `f5-*.log` later (numbers and ids only). If it
cannot run to the end, run two themes as a partial result instead:
`... run_f5.ps1 -DataDir "<path>\Perspectieven Oss" -Partial` (Sporten and Actieve mobiliteit).

## The demo (15 minutes, Alex's laptop)

Start: double-click `scripts\start_app.bat` (review screen at http://127.0.0.1:8501, this laptop only).
Fill in your name at the top (it goes into the audit log).

| Min | Tab | What to show |
|---|---|---|
| 0-3 | 1 · Data | Werkmap `workspace-demo-ingest` (an empty folder). Map: the Oss folder. Inlezen. Show the inventory: files per perspective, checksums, duplicates skipped, the original report kept out of the inputs, 0 unknown survey questions. |
| 3-5 | 5 · Rapport per model | Werkmap back to `workspace-demo`. Pick the E2 seed 42 report (`20261126-001538-local_qwen7b-staged-s42-cpu`). Show claims, grounded and unsupported rates, time. Click "Gebruik als concept voor beoordeling". |
| 5-11 | 3 · Beoordelen | Pick a theme. The badges: red unsupported, orange weak, green grounded. Open a green claim: its evidence ids lead to the cell or row. Approve it. Open a red one: the verifier's flags. Approve with "override" (needs the confirm box, and it is logged), edit one, delete one. Show "Audit-log: intact". |
| 11-14 | 4 · Exporteren | Export is blocked while claims are open (FR11). Choose only the section you reviewed and export: Word and Markdown with the evidence map. Or show "Conceptversie (intern, met markering)". |
| 14-15 | – | Questions. Stop with `scripts\stop_app.bat`. |

## After the visit

- Copy back only the F5 run record and log (numbers and ids). Report F5 against the rule: no failed
  sections, peak model memory at most 9.6 GiB, CPU-only, code 44def27.
- Delete `workspace-demo` and `workspace-demo-ingest` on Alex's laptop when no longer needed.
