import re
from jsonschema import Draft202012Validator

from .schema import Tool

REF_RE = re.compile(r"^\$(s\d+)\.(.+)$")


def is_ref(v) -> bool:
    return isinstance(v, str) and bool(REF_RE.match(v))


def validate_args(tool: Tool, args: dict, static: bool = False) -> list[str]:
    """JSON-Schema check. static=True (plan time) tolerates args that are refs to earlier steps."""
    refs = {k for k, v in args.items() if is_ref(v)} if static else set()
    schema = {**tool.input_schema,
              "required": [r for r in tool.input_schema["required"] if r not in refs]}
    concrete = {k: v for k, v in args.items() if k not in refs}
    errs = Draft202012Validator(schema).iter_errors(concrete)
    return [f"{'.'.join(map(str, e.path)) or '(args)'}: {e.message}"
            for e in sorted(errs, key=lambda e: list(map(str, e.path)))]
