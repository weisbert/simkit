# SKILL code

Runs inside Cadence Virtuoso. Phase 1 modules (to be written):

- `pvtproject.il` — `.pvtproject` discovery and parser (SKILL side). Walks up from cwd, returns project context.
- `collector.il` — `PvtSave` entry point. Dumps the active Maestro history to JSON, captures netlist, optional screenshots.

Load order: `pvtproject.il` before `collector.il`.

## Reference docs
Consult `../../SKILL_file/` (the 44-PDF Cadence corpus) before writing SKILL. Relevant subdirs:
- `03_仿真与分析自动化/` — ADE-XL, MAE, OCEAN (main references for this module)
- `01_核心语言与数据库/` — SKILL language, DFII database
- `05_其他参考/` — IPC sockets (for the socket bridge, later)
