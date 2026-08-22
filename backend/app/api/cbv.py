"""Minimal class-based views for FastAPI.

Vendored on purpose: ``fastapi-utils`` is not a dependency here and its CBV helper
does not track current FastAPI/Pydantic-v2 internals. Usage::

    router = SlashInferringRouter()

    @cbv(router)
    class Conversations:
        db: AsyncSession = Depends(get_db_session)

        @router.get("/{conversation_uuid}")
        async def detail(self, conversation_uuid: str) -> ConversationDetailResponse: ...

The route decorators run while the class body executes, so the routes already sit on
the router by the time ``cbv`` sees the class; ``cbv`` then rewrites them.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any, get_type_hints

from fastapi import APIRouter, Depends, params
from fastapi.routing import APIRoute

# APIRoute attributes whose names match its constructor keywords one to one.
_ROUTE_KWARGS = tuple(
    name
    for name in inspect.signature(APIRoute.__init__).parameters
    if name not in {"self", "path", "endpoint"}
)
_KEYWORD_ONLY = inspect.Parameter.KEYWORD_ONLY


class SlashInferringRouter(APIRouter):
    """Router where a collection registered as ``""`` also answers on ``"/"``."""

    def add_api_route(
        self, path: str, endpoint: Callable[..., Any], **kwargs: Any
    ) -> None:
        """Register a route, mirroring a bare collection path on both spellings.

        Args:
            path: Route path as written by the caller.
            endpoint: The endpoint callable.
            **kwargs: Every other option FastAPI accepts for a route.
        """
        if path in ("", "/"):
            super().add_api_route("", endpoint, **kwargs)
            # The mirrored spelling stays out of the schema to keep operation ids unique.
            kwargs["include_in_schema"] = False
            path = "/"
        super().add_api_route(path, endpoint, **kwargs)


def cbv(router: APIRouter) -> Callable[[type], type]:
    """Bind a class's route-decorated methods to instances of that class.

    Class attributes whose value is a ``Depends(...)`` become constructor-injected
    instance attributes, and every already-registered route on ``router`` that points
    at a method of the class gets its ``self`` parameter resolved by FastAPI.

    Args:
        router: The router the class's methods were registered on.

    Returns:
        A class decorator.
    """

    def decorator(cls: type) -> type:
        _build_init(cls)
        _rewrite_routes(router, cls)
        return cls

    return decorator


def _class_dependencies(cls: type) -> dict[str, tuple[Any, params.Depends]]:
    """Collect the class attributes that hold a ``Depends`` marker, with their types."""
    declared = {n: v for n, v in vars(cls).items() if isinstance(v, params.Depends)}
    if not declared:
        return {}
    try:
        hints = get_type_hints(cls)
    except Exception:  # Unresolvable forward reference: fall back to an open type.
        hints = {}
    return {name: (hints.get(name, Any), dep) for name, dep in declared.items()}


def _build_init(cls: type) -> None:
    """Give the class an ``__init__`` whose parameters are its declared dependencies."""
    dependencies = _class_dependencies(cls)
    names = tuple(dependencies)
    parameters = [
        inspect.Parameter(name, _KEYWORD_ONLY, default=dep, annotation=hint)
        for name, (hint, dep) in dependencies.items()
    ]

    def __init__(self: Any, **kwargs: Any) -> None:
        for name in names:
            setattr(self, name, kwargs[name])

    cls.__init__ = __init__  # type: ignore[method-assign]
    # FastAPI reads inspect.signature(cls); annotations must be real objects here
    # because a class carries no __globals__ for resolving string annotations.
    cls.__signature__ = inspect.Signature(parameters)  # type: ignore[attr-defined]


def _inject_self(cls: type, endpoint: Callable[..., Any]) -> None:
    """Rewrite ``self`` into a dependency on ``cls``, keeping every other parameter."""
    if getattr(endpoint, "__cbv_bound__", False):
        return
    signature = inspect.signature(endpoint)
    parameters = list(signature.parameters.values())
    bound_self = parameters[0].replace(
        kind=_KEYWORD_ONLY, default=Depends(cls), annotation=cls
    )
    # Everything becomes keyword-only: FastAPI calls endpoints with **kwargs, and it
    # keeps a defaulted `self` in front of non-defaulted path params legal.
    rest = [param.replace(kind=_KEYWORD_ONLY) for param in parameters[1:]]
    new_signature = signature.replace(parameters=[bound_self, *rest])
    endpoint.__signature__ = new_signature  # type: ignore[attr-defined]
    endpoint.__cbv_bound__ = True  # type: ignore[attr-defined]


def _rebuilt(route: APIRoute) -> APIRoute:
    """Re-create a route so FastAPI re-reads the endpoint's rewritten signature."""
    kwargs = {
        name: getattr(route, name) for name in _ROUTE_KWARGS if hasattr(route, name)
    }
    return APIRoute(route.path, route.endpoint, **kwargs)


def _rewrite_routes(router: APIRouter, cls: type) -> None:
    """Replace every route on the router that belongs to this class."""
    owned = f"{cls.__name__}."
    for index, route in enumerate(router.routes):
        if not isinstance(route, APIRoute):
            continue
        if not getattr(route.endpoint, "__qualname__", "").startswith(owned):
            continue
        _inject_self(cls, route.endpoint)
        router.routes[index] = _rebuilt(route)
