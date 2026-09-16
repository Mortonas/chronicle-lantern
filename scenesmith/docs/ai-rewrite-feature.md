# AI Rewrite Feature Recon
- Stack: Desktop Python app built with PySide6 (`main.py`, `src/app.py`).
- UI entry point: `src/app.py` (`MainWindow`) drives the generator layout; TODO markers earmark where the upcoming `QTabWidget` and rewrite controls will mount.
- Dialog integration: no existing pickers yet; the rewrite tab will introduce `QFileDialog` usage from within `MainWindow`.
- Filesystem helpers: `src/selection_pipeline.py` and `src/search_ripgrep.py` handle markdown discovery and reads that the rewrite flow can reuse; new write logic will live in a dedicated helper (planned `src/ai_rewrite.py`).
- Model adapters: `src/model_adapters.py` already abstracts providers; the rewrite feature will call into `get_adapter` for generation.
- Planned touchpoints: augment `src/app.py`, extend reuse in `src/selection_pipeline.py`, add a focused `src/ai_rewrite.py`, and wire prompt templates under `templates/prompts/` as needed.
