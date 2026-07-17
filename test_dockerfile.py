import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def _copied_python_modules() -> set[str]:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    copied: set[str] = set()
    for line in dockerfile.splitlines():
        if not line.startswith("COPY "):
            continue
        for source in line.removeprefix("COPY ").split()[:-1]:
            if source.endswith(".py"):
                copied.add(Path(source).stem)
    return copied


def _local_imports(module: str) -> set[str]:
    source = (ROOT / f"{module}.py").read_text(encoding="utf-8")
    imports: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".", 1)[0])
    return {name for name in imports if (ROOT / f"{name}.py").is_file()}


def test_dockerfile_copies_transitive_server_runtime_modules() -> None:
    copied = _copied_python_modules()
    pending = ["server"]
    required: set[str] = set()
    while pending:
        module = pending.pop()
        if module in required:
            continue
        required.add(module)
        pending.extend(_local_imports(module) - required)

    assert required <= copied, (
        "Dockerfile does not copy runtime modules: "
        + ", ".join(sorted(required - copied))
    )
