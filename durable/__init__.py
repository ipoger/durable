"""durable — a small, durable task orchestrator.

Public surface for M0 is intentionally narrow: the state machine
(:mod:`durable.model`) and the config parser (:mod:`durable.config`). The store
and the executor grow behind them without changing this file.
"""

from . import config, model, store

__all__ = ["config", "model", "store", "__version__"]

__version__ = "0.1.0"
