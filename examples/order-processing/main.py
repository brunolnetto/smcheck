"""main.py
=========
Entry-point for the ``order-processing`` example application.

Run with:
    python main.py               (from this directory)

What happens
------------
1. All simulation scenarios (A–G) are executed in order.
2. Full smcheck pipeline: graph analysis → validation → path analysis
   → test generation → LLM explanation.
"""
from __future__ import annotations
import sys
import os

from machine   import OrderProcessing
from scenarios import run_all
from analysis  import run_smcheck

# Make the smcheck package importable when running directly from this directory.
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


if __name__ == "__main__":
    # -- 1. SimPy scenarios (A through G) -----------------------------------
    run_all()

    # -- 2-6. smcheck: graph, validation, paths, testgen, LLM explanation ---
    run_smcheck(OrderProcessing)
