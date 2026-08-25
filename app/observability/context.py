from contextvars import ContextVar


_context = ContextVar("gateforge_request_context", default={})


def set_context(**values):
    current = dict(_context.get())
    current.update({key: value for key, value in values.items() if value is not None})
    _context.set(current)
    return current


def get_context():
    return dict(_context.get())


def clear_context():
    _context.set({})