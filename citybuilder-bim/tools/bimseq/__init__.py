"""bimseq: BIM element -> construction step mapping, scheduling and sample generation.

Pipeline: IFC/synthetic -> elements.json -> (mapper) element_step_map.json
-> (scheduler) sequence.json.  See docs/02-sequencing-model.md.
"""

__version__ = "0.1.0"
GENERATOR = f"bimseq {__version__}"
