"""Allow ``python -m amplifier_module_pipeline_runner`` without install."""

from .cli import main

raise SystemExit(main())
