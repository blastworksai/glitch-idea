"""Literal packaged handler/route registry.

Optional step modules export HANDLER: Service.TrustedStepHandler. Optional
providers export ROUTES: tuple[TrustedRoute,...]. Callbacks receive
(binding, RequestInfo, payload); payload is bounded JSON, None for reads, or
BoundedBody for upload-bytes. They return a JSON dict or bridge.Response.
Callbacks are trusted packaged code, authenticated and serialized per binding;
network reads/waits must never hold a Store lock. No HTTP value selects imports.
"""
from dataclasses import dataclass
import importlib

from idea_service import TrustedStepHandler
from idea_workflow import STEP_ORDER

_STEP_MODULE_NAMES = {
    'method': 'idea_steps.method', 'discovery': 'idea_steps.discovery',
    'exploration': 'idea_steps.exploration', 'visualize': 'idea_steps.visualize',
    'assess': 'idea_steps.assess',
}
# Follows the derived workflow order (one setting decides Methods vs Discovery).
STEP_MODULES = tuple((step, _STEP_MODULE_NAMES[step]) for step in STEP_ORDER if step in _STEP_MODULE_NAMES)
PROVIDER_MODULES = ('idea_proposals', 'idea_assets', 'idea_assessment', 'idea_handoff')
# route_id -> method, exact path or one opaque ID prefix, body kind
ROUTE_SPECS = {
    'propose': ('POST', '/api/v1/propose', 'json'),
    'visual-disposition': ('POST', '/api/v1/visual-disposition', 'json'),
    'visual-set/accept': ('POST', '/api/v1/visual-set/accept', 'json'),
    'handoff': ('POST', '/api/v1/handoff', 'json'),
    'ideas': ('GET', '/api/v1/ideas', None),
    'selection': ('POST', '/api/v1/selection', 'json'),
    'uploads': ('POST', '/api/v1/uploads', 'json'),
    'upload-bytes': ('PUT', '/api/v1/uploads/{id}/bytes', 'bytes'),
    'attachment': ('GET', '/api/v1/attachments/{id}', None),
    'idea-markdown': ('GET', '/api/v1/ideas/{id}/markdown', None),
    'settings': ('GET', '/api/v1/settings', None),
    'settings-save': ('POST', '/api/v1/settings/save', 'json'),
    'settings-test': ('POST', '/api/v1/settings/test', 'json'),
}


class RegistryError(RuntimeError):
    """A present packaged module is broken; this is not optional absence."""


@dataclass(frozen=True)
class TrustedRoute:
    route_id: str
    handler: object


def _optional(name, importer):
    try:
        return importer(name)
    except ModuleNotFoundError as exc:
        if exc.name == name:
            return None
        raise RegistryError('Packaged module dependency is unavailable: ' + name) from exc
    except Exception as exc:
        raise RegistryError('Cannot load packaged module: ' + name) from exc


def load_registry(importer=importlib.import_module):
    """Return detached (handlers, routes); missing future modules are allowed."""
    handlers, routes = {}, {}
    def add_routes(module, name, required):
        exported = getattr(module, 'ROUTES', () if not required else None)
        if type(exported) is not tuple:
            raise RegistryError('Invalid ROUTES export: ' + name)
        for route in exported:
            if not (isinstance(route, TrustedRoute) and route.route_id in ROUTE_SPECS
                    and callable(route.handler) and route.route_id not in routes):
                raise RegistryError('Invalid or duplicate packaged route: ' + name)
            routes[route.route_id] = route

    for step, name in STEP_MODULES:
        module = _optional(name, importer)
        if module is None:
            continue
        handler = getattr(module, 'HANDLER', None)
        if not (isinstance(handler, TrustedStepHandler) and callable(handler.validate)
                and (handler.apply is None or callable(handler.apply))
                and type(handler.extra_dependencies) is tuple
                and all(name in STEP_ORDER[:STEP_ORDER.index(step)] for name in handler.extra_dependencies)):
            raise RegistryError('Invalid HANDLER export: ' + name)
        handlers[step] = handler
        add_routes(module, name, False)
    for name in PROVIDER_MODULES:
        module = _optional(name, importer)
        if module is None:
            continue
        add_routes(module, name, True)
    return handlers, routes
