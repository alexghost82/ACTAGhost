"""Runtime observability wiring with optional dependencies."""

from __future__ import annotations

from contextlib import contextmanager
import functools
import time
from typing import Any, Callable

from acta.config import Settings
from acta.logging_config import get_logger

log = get_logger("observability")


class ObservabilityRuntime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._metrics_ready = False
        self._metrics_error: str | None = None
        self._prom_content_type = "text/plain; charset=utf-8"
        self._prom_generate: Callable[[], bytes] | None = None
        # Optional prometheus/otel objects; typed as Any so the no-dependency
        # path (None) and the instrumented path share one attribute.
        self._request_counter: Any = None
        self._request_latency: Any = None
        self._pipeline_step_latency: Any = None
        self._provider_counter: Any = None
        self._provider_latency: Any = None
        self._tracer: Any = None
        self._init_metrics()
        self._init_tracing()

    def _init_metrics(self) -> None:
        if not self.settings.metrics_enabled:
            return
        try:
            from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Histogram, generate_latest
        except Exception as exc:  # pragma: no cover - optional import
            self._metrics_error = str(exc)
            return
        registry = CollectorRegistry(auto_describe=True)
        self._request_counter = Counter(
            "acta_http_requests_total",
            "HTTP requests processed by ACTA.",
            ("method", "path", "status"),
            registry=registry,
        )
        self._request_latency = Histogram(
            "acta_http_request_latency_seconds",
            "HTTP request latency in seconds.",
            ("method", "path"),
            registry=registry,
        )
        self._pipeline_step_latency = Histogram(
            "acta_pipeline_step_duration_seconds",
            "Duration of orchestrator pipeline steps.",
            ("step",),
            registry=registry,
        )
        self._provider_counter = Counter(
            "acta_provider_calls_total",
            "Provider calls by profile and status.",
            ("provider", "profile", "status"),
            registry=registry,
        )
        self._provider_latency = Histogram(
            "acta_provider_call_duration_seconds",
            "Provider call duration in seconds.",
            ("provider", "profile"),
            registry=registry,
        )
        self._prom_content_type = CONTENT_TYPE_LATEST
        self._prom_generate = lambda: generate_latest(registry)
        self._metrics_ready = True

    def _init_tracing(self) -> None:
        if not self.settings.otel_enabled:
            return
        try:
            from opentelemetry import trace
        except Exception as exc:  # pragma: no cover - optional import
            log.warning("OTel requested but unavailable: %s", exc)
            return
        self._tracer = trace.get_tracer("acta")

    def init_sentry(self) -> None:
        if not self.settings.sentry_dsn:
            return
        try:
            import sentry_sdk
        except Exception as exc:  # pragma: no cover - optional import
            log.warning("Sentry DSN configured but sentry-sdk unavailable: %s", exc)
            return
        sentry_sdk.init(dsn=self.settings.sentry_dsn)
        log.info("Sentry initialized")

    @contextmanager
    def trace_span(self, name: str, **attributes: Any):
        if self._tracer is None:
            yield
            return
        with self._tracer.start_as_current_span(name) as span:
            for key, value in attributes.items():
                if value is not None:
                    span.set_attribute(key, value)
            yield

    def observe_http(self, method: str, path: str, status: int, duration: float) -> None:
        if not self._metrics_ready:
            return
        status_str = str(status)
        self._request_counter.labels(method=method, path=path, status=status_str).inc()
        self._request_latency.labels(method=method, path=path).observe(duration)

    def metrics_payload(self) -> tuple[int, bytes, str]:
        if not self.settings.metrics_enabled:
            return 503, b"", self._prom_content_type
        if not self._metrics_ready or self._prom_generate is None:
            message = f"# prometheus disabled: {self._metrics_error or 'dependency missing'}\n"
            return 503, message.encode("utf-8"), self._prom_content_type
        return 200, self._prom_generate(), self._prom_content_type

    def instrument_orchestrator(self, orchestrator: Any) -> None:
        self._wrap_provider_calls(orchestrator.s.router)
        self._wrap_step_call(orchestrator)
        self._wrap_run_call(orchestrator)

    def _wrap_provider_calls(self, router: Any) -> None:
        original = getattr(router, "complete")
        if getattr(original, "__acta_observed__", False):
            return

        @functools.wraps(original)
        def wrapped_complete(*args: Any, **kwargs: Any):
            profile = kwargs.get("profile", "default")
            started = time.perf_counter()
            provider_name = "unknown"
            try:
                with self.trace_span("provider.complete", profile=profile):
                    response = original(*args, **kwargs)
                provider_name = getattr(response, "provider", provider_name)
                if self._metrics_ready:
                    self._provider_counter.labels(
                        provider=provider_name,
                        profile=profile,
                        status="ok",
                    ).inc()
                    self._provider_latency.labels(provider=provider_name, profile=profile).observe(
                        time.perf_counter() - started
                    )
                return response
            except Exception:
                if self._metrics_ready:
                    self._provider_counter.labels(
                        provider=provider_name,
                        profile=profile,
                        status="error",
                    ).inc()
                raise

        setattr(wrapped_complete, "__acta_observed__", True)
        router.complete = wrapped_complete

    def _wrap_step_call(self, orchestrator: Any) -> None:
        original = getattr(orchestrator, "_step")
        if getattr(original, "__acta_observed__", False):
            return

        @functools.wraps(original)
        def wrapped_step(step: int, run_fn: Any, state: Any, runner: Any = None) -> int:
            if runner is not None:
                step_name = f"{getattr(runner.__self__, 'NAME', 'agent')}.{runner.__name__}"
            else:
                step_name = f"{getattr(run_fn.__self__, 'NAME', 'agent')}.run"
            started = time.perf_counter()
            with self.trace_span("pipeline.step", step=step_name):
                next_step = original(step, run_fn, state, runner=runner)
            if self._metrics_ready:
                self._pipeline_step_latency.labels(step=step_name).observe(time.perf_counter() - started)
            return next_step

        setattr(wrapped_step, "__acta_observed__", True)
        orchestrator._step = wrapped_step

    def _wrap_run_call(self, orchestrator: Any) -> None:
        original = getattr(orchestrator, "run")
        if getattr(original, "__acta_observed__", False):
            return

        @functools.wraps(original)
        def wrapped_run(request: Any):
            request_id = getattr(request, "request_id", None)
            user_id = getattr(request, "user_id", None)
            with self.trace_span("orchestrator.run", request_id=request_id, user_id=user_id):
                return original(request)

        setattr(wrapped_run, "__acta_observed__", True)
        orchestrator.run = wrapped_run
