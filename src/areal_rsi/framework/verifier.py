"""Loading and safe execution of user-provided Verifier implementations."""

from __future__ import annotations

import asyncio
import importlib
from collections.abc import Mapping
from dataclasses import replace
from typing import Any, cast

from areal_rsi.api.base import (
    JsonObject,
    VerificationError,
    VerificationRequest,
    VerificationResult,
    VerificationStatus,
    VerifierSpec,
)
from areal_rsi.api.modules import Verifier, VerifierFactory


class PythonVerifierFactory:
    """Load a Python Verifier from a ``module:attribute`` entrypoint.

    The attribute may be a Verifier instance, a zero-argument Verifier class,
    or a zero-argument factory. Runtime configuration stays in
    ``VerificationRequest.verifier.config`` so every invocation is fully
    described by its request.
    """

    def create(self, spec: VerifierSpec) -> Verifier:
        if spec.provider != "python":
            raise ValueError(f"PythonVerifierFactory cannot load provider {spec.provider!r}.")
        if spec.entrypoint is None:
            raise ValueError("Python Verifier requires an entrypoint.")

        module_name, separator, attribute_name = spec.entrypoint.partition(":")
        if not separator or not module_name or not attribute_name:
            raise ValueError("Verifier entrypoint must use 'module:attribute' format.")

        module = importlib.import_module(module_name)
        try:
            target: Any = getattr(module, attribute_name)
        except AttributeError as exc:
            raise ValueError(f"Verifier entrypoint {spec.entrypoint!r} does not exist.") from exc

        if isinstance(target, type):
            verifier = target()
        elif callable(getattr(target, "verify", None)):
            verifier = target
        elif callable(target):
            verifier = target()
        else:
            verifier = target

        if not callable(getattr(verifier, "verify", None)):
            raise TypeError(
                f"Verifier entrypoint {spec.entrypoint!r} must provide async verify(request)."
            )
        return cast(Verifier, verifier)


class VerifierLoader:
    """Resolve Verifier implementations through an extensible provider registry."""

    def __init__(self, factories: Mapping[str, VerifierFactory] | None = None):
        self._factories: dict[str, VerifierFactory] = {
            "python": PythonVerifierFactory(),
            **dict(factories or {}),
        }

    def register(self, provider: str, factory: VerifierFactory) -> None:
        if not provider.strip():
            raise ValueError("Verifier provider must not be empty.")
        self._factories[provider] = factory

    def create(self, spec: VerifierSpec) -> Verifier:
        try:
            factory = self._factories[spec.provider]
        except KeyError as exc:
            raise ValueError(
                f"No VerifierFactory registered for provider {spec.provider!r}."
            ) from exc
        return factory.create(spec)


class VerifierExecutor:
    """Apply framework timeout, error, and identity semantics to a Verifier."""

    async def verify(
        self,
        verifier: Verifier,
        request: VerificationRequest,
    ) -> VerificationResult:
        try:
            call = verifier.verify(request)
            if request.verifier.timeout_seconds is None:
                result = await call
            else:
                result = await asyncio.wait_for(
                    call,
                    timeout=request.verifier.timeout_seconds,
                )
            return self._validate_and_identify(result, request)
        except TimeoutError:
            return self._failure(
                request,
                status=VerificationStatus.TIMEOUT,
                code="verifier_timeout",
                message=(
                    "Verifier exceeded its configured timeout of "
                    f"{request.verifier.timeout_seconds} seconds."
                ),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return self._failure(
                request,
                status=VerificationStatus.INFRA_ERROR,
                code="verifier_execution_failed",
                message=str(exc) or type(exc).__name__,
                details={"exception_type": type(exc).__name__},
            )

    @staticmethod
    def _validate_and_identify(
        result: VerificationResult,
        request: VerificationRequest,
    ) -> VerificationResult:
        if not isinstance(result, VerificationResult):
            raise TypeError("Verifier.verify must return VerificationResult.")
        if result.task_id != request.task.task_id:
            raise ValueError("VerificationResult.task_id does not match the request.")
        trajectory_id = request.result.trajectory.trajectory_id
        if result.trajectory_id != trajectory_id:
            raise ValueError("VerificationResult.trajectory_id does not match the request.")
        if result.verifier_id not in (None, request.verifier.verifier_id):
            raise ValueError("VerificationResult.verifier_id does not match the request.")
        if result.verifier_digest not in (None, request.verifier.digest):
            raise ValueError("VerificationResult.verifier_digest does not match the request.")
        return replace(
            result,
            verifier_id=request.verifier.verifier_id,
            verifier_digest=request.verifier.digest,
        )

    @staticmethod
    def _failure(
        request: VerificationRequest,
        *,
        status: VerificationStatus,
        code: str,
        message: str,
        details: JsonObject | None = None,
    ) -> VerificationResult:
        return VerificationResult(
            task_id=request.task.task_id,
            trajectory_id=request.result.trajectory.trajectory_id,
            passed=None,
            score=None,
            metrics={},
            feedback="",
            status=status,
            verifier_id=request.verifier.verifier_id,
            verifier_digest=request.verifier.digest,
            error=VerificationError(
                code=code,
                message=message,
                details=details or {},
            ),
        )
