"""Optional MLflow tracing of every LLM and Jev call.

Off unless MLFLOW_TRACKING_URI is set and the `mlflow` package is installed (`pip install -e '.[tracing]'`).
The Docker image does not install it, so the hand-in build stays small and fast.
"""

import logging
import os
from contextlib import contextmanager

log = logging.getLogger("watchtower.tracing")
_mlflow = None


def setup(settings=None) -> bool:
    """MLFLOW_TRACKING_URI comes from the environment or from .env via Settings."""
    global _mlflow
    uri = os.environ.get("MLFLOW_TRACKING_URI") or getattr(settings, "mlflow_tracking_uri", None)
    if not uri:
        return False
    try:
        import mlflow
    except ImportError:
        log.warning("MLFLOW_TRACKING_URI is set but mlflow is not installed; tracing stays off")
        return False
    mlflow.set_tracking_uri(uri)
    mlflow.set_experiment(os.environ.get("MLFLOW_EXPERIMENT_NAME")
                          or getattr(settings, "mlflow_experiment_name", None) or "watchtower")
    for provider in ("openai", "anthropic"):
        try:
            getattr(mlflow, provider).autolog()
        except Exception as e:  # provider flavor missing in this mlflow version
            log.warning("mlflow.%s.autolog failed: %s", provider, e)
    _mlflow = mlflow
    log.info("MLflow tracing on: %s", uri)
    return True


def reset() -> None:
    global _mlflow
    _mlflow = None


@contextmanager
def span(name: str, attributes: dict | None = None):
    """A trace span around one judgment; yields None when tracing is off."""
    if _mlflow is None:
        yield None
        return
    attrs = {k: v for k, v in (attributes or {}).items() if v is not None}
    with _mlflow.start_span(name=name, attributes=attrs) as s:
        yield s
