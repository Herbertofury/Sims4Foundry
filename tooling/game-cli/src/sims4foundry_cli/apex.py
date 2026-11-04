"""Read-only migration inventory, without importing or running Apex sources."""
import ast
from .errors import FoundryError
from .io import plain_path, sha256


def inspect_source(repository):
    root = plain_path(repository)
    script = plain_path(root / "tools" / "apex_cli.py")
    profile = plain_path(root / "tools" / "reusable_profile.py")
    if script.stat().st_size > 1024 * 1024 or profile.stat().st_size > 1024 * 1024:
        raise FoundryError("source_bound", "Apex source exceeds the migration-review bound.")
    try:
        tree = ast.parse(script.read_text(encoding="utf-8-sig"))
    except (SyntaxError, UnicodeError) as exc:
        raise FoundryError("apex_source_invalid", "Cannot parse the selected Apex CLI source.") from exc
    operations = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument" and isinstance(node.func.value, ast.Name)
                and node.func.value.id in ("game", "cas") and node.args
                and isinstance(node.args[0], ast.Constant) and node.args[0].value == "operation"):
            choices = next((kw.value for kw in node.keywords if kw.arg == "choices"), None)
            try:
                names = ast.literal_eval(choices)
            except (ValueError, TypeError):
                continue
            if isinstance(names, (list, tuple)) and all(isinstance(name, str) for name in names):
                operations[node.func.value.id] = list(names)
    return {"ok": True, "source_executed": False, "game_contacted": False,
            "cli_sha256": sha256(script), "profile_sha256": sha256(profile),
            "declared_apex_operations": operations,
            "provenance": "Locally selected source snapshot; current desktop source must be checked separately.",
            "no_apex_equivalence": False,
            "migration_rules": ["Do not reuse an Apex journal for a different profile marker.",
                                "Preserve per-process, script hash, profile token and result verification.",
                                "Apex queue acceptance is not semantic UI completion.",
                                "Do not publish Apex-only actions as no-Apex controls."]}
