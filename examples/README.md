# Examples

Minimal working examples demonstrating the tool in use.

- Sample `.pvtproject` configs for different project shapes
- Sample JSON dump files (for looking at the format without running Cadence)
- Example query scripts showing common operations (TT worst-case, cross-slice diff, artifact retrieval)

Filled in incrementally as Phase 1 components land.

## `beacon_pvt.cornermodel.json` — preset corner template

A starter Corner Model with the standard Beacon PVT corner set already
authored: three dimensions (`Process` TT/SS/FF/FS/SF, `Temperature`
55/-40/125, `Voltage` Vnom/Vhigh/Vlow) and ten corners — `Beacon_TT`,
`Beacon_SS_1..3`, `Beacon_FF_1..3`, `Beacon_NT_PVT` (15 points),
`Beacon_PVT_16` (16), `Beacon_PVT_45` (45).

Open it from the GUI via **File ▸ Open Corner Model**, then use it as a
reference or a starting point. It ships with placeholder values you must
replace for your project:

- `project` / `testbench_id` — your `.pvtproject` project and Maestro testbench.
- `Process.model_file` — the path to your PDK model file; the per-level
  `section` names (`tt`/`ss`/…) if your library uses different ones.
- `Voltage` — the member variable `vdd` and the values `1.0`/`1.1`/`0.9`
  → your real supply variable and the nominal / high / low numbers.
- `modes.Beacon.vars` — `d_en_dummy` is a placeholder register; swap in
  the mode's real register settings.

`Temperature` (55 / -40 / 125) needs no change; only the level *labels*
are ASCII-encoded (`Tn40` for -40) because labels disallow `-`.
