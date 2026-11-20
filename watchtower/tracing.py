"""Optional MLflow tracing of every LLM and Jev call.

Off unless MLFLOW_TRACKING_URI is set and the `mlflow` package is installed (`pip install -e '.[tracing]'`).
The Docker image does not install it, so the hand-in build stays small and fast.
"""

import logging
import os
from contextlib import contextmanager

log = logging.getLogger("watchtower.tracing")
_mlflow = None
_uri: str | None = None
_experiment_id: str | None = None


def setup(settings=None) -> bool:
    """MLFLOW_TRACKING_URI comes from the environment or from .env via Settings."""
    global _mlflow, _uri, _experiment_id
    uri = os.environ.get("MLFLOW_TRACKING_URI") or getattr(settings, "mlflow_tracking_uri", None)
    if not uri:
        return False
    try:
        import mlflow
    except ImportError:
        log.warning("MLFLOW_TRACKING_URI is set but mlflow is not installed; tracing stays off")
        return False
    mlflow.set_tracking_uri(uri)
    exp = mlflow.set_experiment(os.environ.get("MLFLOW_EXPERIMENT_NAME")
                                or getattr(settings, "mlflow_experiment_name", None) or "watchtower")
    _uri, _experiment_id = uri.rstrip("/"), str(exp.experiment_id)
    for provider in ("openai", "anthropic"):
        try:
            getattr(mlflow, provider).autolog()
        except Exception as e:  # provider flavor missing in this mlflow version
            log.warning("mlflow.%s.autolog failed: %s", provider, e)
    _mlflow = mlflow
    log.info("MLflow tracing on: %s", uri)
    return True


def reset() -> None:
    global _mlflow, _uri, _experiment_id
    _mlflow = _uri = _experiment_id = None


def ui_url() -> str | None:
    """The MLflow page that lists this experiment's traces, or None when tracing is off."""
    if _mlflow is None or not _uri:
        return None
    return f"{_uri}/#/experiments/{_experiment_id}/traces"


def _row(t) -> dict | None:
    root = next((s for s in t.data.spans if s.parent_id is None), None)
    if root is None or not (root.name == "jev" or root.name.startswith("llm.")):
        return None
    a, o = root.attributes or {}, root.outputs if isinstance(root.outputs, dict) else {}
    kind = "jev" if root.name == "jev" else "llm"
    return {"id": t.info.trace_id, "kind": kind, "name": root.name, "portal_id": a.get("portal_id"),
            "purpose": a.get("purpose") if kind == "llm" else a.get("questions"),
            "model": a.get("model") if kind == "llm" else "jev", "tokens_in": o.get("tokens_in"),
            "tokens_out": o.get("tokens_out"), "usd": o.get("usd"), "ms": t.info.execution_duration,
            "at": t.info.request_time}


def recent(limit: int = 30) -> list[dict]:
    """The newest Watchtower traces as plain rows for the dashboard. Blocking: call it from a thread."""
    if _mlflow is None:
        return []
    try:
        found = _mlflow.search_traces(locations=[_experiment_id], max_results=limit, return_type="list",
                                      order_by=["timestamp_ms DESC"])
    except Exception as e:
        log.warning("could not read traces from MLflow: %s", e)
        return []
    return [r for r in map(_row, found) if r]


@contextmanager
def span(name: str, attributes: dict | None = None):
    """A trace span around one judgment; yields None when tracing is off."""
    if _mlflow is None:
        yield None
        return
    attrs = {k: v for k, v in (attributes or {}).items() if v is not None}
    with _mlflow.start_span(name=name, attributes=attrs) as s:
        yield s
