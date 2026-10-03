# Scale benchmark

`python3 -m bimseq bench --wings 15 12 --from-ifc` (4 CPUs, Python 3.11.15) writes `last_bench.json`; the work
files (IFC, elements parts, bundle) live in `work/` (git-ignored). Route: synth-ifc -> ifc-to-elements -> map (aggregated) -> schedule -> compressed split bundle.

| Stage | Seconds |
| --- | --- |
| synth-ifc (150,120 elements, 110 MB IFC) | 50.25 |
| ifc-to-elements (open, geometry iterator on 4 threads, relations, grid, zones, streaming parts) | 117.08 |
| load element parts | 5.8 |
| aggregate | 0.98 |
| map (aggregated, recipes with anchors) | 2.13 |
| schedule (CPM, gates, levelling, packaging) | 7.59 |
| write compressed split bundle | 6.18 |
| **total** | **190.5** (target 600) |

| Result | Value | Target |
| --- | --- | --- |
| elements in | 150,120 | |
| aggregated elements | 5,920 | |
| tasks | 12,080 (3,780 virtual) | <= 20,000 |
| links | 48,459 | |
| packages | 1,460 | <= 2,000 |
| baseline / contract weeks | 28 / 31 | |
| bundle size on disk | 16.5 MB | |

`python3 -m bimseq stress --wings 6 6` builds the ~30k-element version straight from the generator in about 6 s.
