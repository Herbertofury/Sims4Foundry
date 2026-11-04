"""Structured CLI. All semantic execution is explicitly synthetic in 0.1."""
import argparse
from dataclasses import asdict
import json
import sys
from . import __version__
from . import apex, mods, windows, workflows, launch, discovery, package_resources, bytecode_headers, resource_diff
from .catalog import CATALOG, capabilities
from .engine import Session, create_session
from .companion_journal import Journal
from .errors import FoundryError
from .io import external_path, loads, new_json, read_json


def parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    cap = commands.add_parser("capabilities", help="Distinguish simulated contracts from real qualified controls")
    cap.add_argument("--backend", choices=("windows", "simulation"), default="windows")
    cap.add_argument("--source-details", action="store_true", help="Include bounded source-review lineage, never live qualification")
    session = commands.add_parser("session")
    session.add_argument("action", choices=("create", "status", "observe"))
    session.add_argument("--directory", required=True)
    session.add_argument("--backend", choices=("simulation", "windows"), default="simulation")
    companion = commands.add_parser("companion", help="Read existing local companion history only; no network or execution")
    companion.add_argument("action", choices=("journal-status",))
    companion.add_argument("--directory", required=True)
    companion.add_argument("--request-id", help="Read one existing immutable request; never creates missing history")
    companion_build = commands.add_parser("companion-build", help="Build a neutral Python 3.7 script archive outside the game; does not install or activate it")
    companion_build.add_argument("--source", required=True, help="Explicit Foundry companion-src directory")
    companion_build.add_argument("--output", required=True, help="New external .ts4script path")
    companion_build.add_argument("--python37", required=True, help="Explicit trusted Python 3.7 compiler executable")
    companion_build.add_argument("--compiler-sha256", required=True, help="Expected lowercase SHA-256 of that executable")
    companion_build.add_argument("--manifest-output", help="Optional new external manifest path")
    probe_build = commands.add_parser("startup-probe-build", help="Build a separate credential-free loader diagnostic; never install or run it")
    probe_build.add_argument("--source", required=True, help="Explicit Foundry diagnostic-src directory")
    probe_build.add_argument("--output", required=True, help="New external .ts4script path")
    probe_build.add_argument("--python37", required=True)
    probe_build.add_argument("--compiler-sha256", required=True)
    probe_build.add_argument("--run-id", required=True, help="32 lowercase hex characters for this proposed diagnostic run")
    probe_build.add_argument("--probe-output", required=True, help="Explicit new Windows external .json evidence filename for a later authorized run")
    probe_build.add_argument("--seconds", type=float, default=30.0)
    probe_build.add_argument("--manifest-output")
    for domain in ("game", "cas", "live", "build"):
        semantic = commands.add_parser(domain, help="Plan a typed operation (does not execute it)")
        semantic.add_argument("action", choices=[key.split(".", 1)[1] for key in CATALOG if key.startswith(domain + ".")])
        semantic.add_argument("--session", required=True)
        semantic.add_argument("--arguments", default="{}", help="Exact typed JSON object; see capabilities")
    execute = commands.add_parser("execute", help="Execute one returned simulation plan; never retry an uncertain action")
    execute.add_argument("--session", required=True)
    execute.add_argument("--plan-id", required=True)
    execute.add_argument("--plan-sha256", required=True)
    reconcile = commands.add_parser("reconcile", help="Observe an earlier operation without dispatching again")
    reconcile.add_argument("--session", required=True)
    reconcile.add_argument("--operation-id", required=True)
    flow = commands.add_parser("workflow")
    flow.add_argument("action", choices=("validate", "run"))
    flow.add_argument("--file", required=True)
    flow.add_argument("--session")
    launch_plan = commands.add_parser("launch-plan", help="Read-only EA metadata preparation; does not launch")
    launch_plan.add_argument("--game-root", required=True)
    launch_plan.add_argument("--ea-log", required=True, action="append", help="One or two logs in chronological order")
    launch_plan.add_argument("--executable-sha256", required=True)
    launch_plan.add_argument("--output")
    discover = commands.add_parser("discover", help="Passive bounded process discovery; never launch admission")
    discover.add_argument("--executable", required=True)
    discover.add_argument("--executable-sha256", required=True)
    discover.add_argument("--output")
    inspect = commands.add_parser("inspect", help="Passive, exact Windows process/window identity only")
    inspect.add_argument("--pid", required=True, type=int)
    inspect.add_argument("--executable", required=True)
    inspect.add_argument("--executable-sha256", required=True)
    inspect.add_argument("--hwnd", type=int)
    inspect.add_argument("--output")
    observe_exit = commands.add_parser("observe-exit", help="Watch an exact retained process lifetime end; never sends quit or verifies saving")
    observe_exit.add_argument("--identity", required=True)
    observe_exit.add_argument("--seconds", type=float, default=30.0)
    observe_exit.add_argument("--output")
    capture = commands.add_parser("capture", help="Pinned FFmpeg WGC capture only; no input")
    capture.add_argument("--identity", required=True)
    capture.add_argument("--ffmpeg", required=True)
    capture.add_argument("--ffmpeg-sha256", required=True)
    capture.add_argument("--destination", required=True)
    mod = commands.add_parser("mods")
    sub = mod.add_subparsers(dest="action", required=True)
    inv = sub.add_parser("inventory")
    inv.add_argument("--directory", required=True)
    inv.add_argument("--output")
    check = sub.add_parser("check")
    check.add_argument("--file", required=True)
    bytecode = sub.add_parser("bytecode", help="Report bounded PYC header bytes; never a compatibility verdict")
    bytecode.add_argument("--file", required=True)
    bytecode.add_argument("--target-magic", help="Exactly eight ASCII hexadecimal characters in file-byte order")
    bytecode.add_argument("--output")
    resources = sub.add_parser("resources", help="Read a bounded DBPF 2.1 resource index; no body extraction")
    resources.add_argument("--file", required=True)
    resources.add_argument("--output")
    overlaps = sub.add_parser("overlaps", help="Report shared resource keys without guessing load order or compatibility")
    overlaps.add_argument("--file", action="append", required=True)
    overlaps.add_argument("--output")
    resource_compare = sub.add_parser("resource-diff", help="Compare exact resource keys and opaque stored bytes; not update safety")
    resource_compare.add_argument("--before", required=True)
    resource_compare.add_argument("--after", required=True)
    resource_compare.add_argument("--output")
    compare = sub.add_parser("compare")
    compare.add_argument("--before", required=True)
    compare.add_argument("--after", required=True)
    stage = sub.add_parser("stage")
    stage.add_argument("--manifest", required=True)
    stage.add_argument("--destination", required=True)
    stage.add_argument("--execute", action="store_true", help="Copy into a new external staging directory, never a profile")
    migration = commands.add_parser("apex-inventory", help="Parse existing Apex CLI without importing it or contacting the game")
    migration.add_argument("--repository", required=True)
    return parser


def run(args):
    if args.command == "capabilities":
        return capabilities(args.backend, args.source_details)
    if args.command == "companion-build":
        from .companion_build import build_companion
        return build_companion(args.source, args.output, args.python37,
                               args.compiler_sha256, args.manifest_output)
    if args.command == "startup-probe-build":
        from .startup_probe_build import build_startup_probe
        return build_startup_probe(args.source, args.output, args.python37, args.compiler_sha256,
                                  run_id=args.run_id, probe_output=args.probe_output,
                                  wait_seconds=args.seconds, manifest_output=args.manifest_output)
    if args.command == "session":
        if args.action == "create":
            return create_session(args.directory, args.backend)
        return getattr(Session(args.directory), args.action)()
    if args.command == "companion":
        journal = Journal(args.directory)
        rows = journal.status(args.request_id) if args.request_id is not None else journal.status()
        if args.request_id is not None:
            rows = (rows,)
        return {"ok": True, "scope": "local_companion_journal", "read_only": True,
                "network_accessed": False, "game_accessed": False,
                "independent_evidence": False, "live_qualified": False,
                "entries": [asdict(row) for row in rows]}
    if args.command in ("game", "cas", "live", "build"):
        return Session(args.session).plan(args.command + "." + args.action, loads(args.arguments))
    if args.command == "execute":
        return Session(args.session).execute(args.plan_id, args.plan_sha256)
    if args.command == "reconcile":
        return Session(args.session).reconcile(args.operation_id)
    if args.command == "workflow":
        value = read_json(args.file)
        if args.action == "validate":
            return workflows.validate(value)
        if not args.session:
            raise FoundryError("session_required", "Workflow run needs an explicit synthetic session directory.")
        return workflows.run(value, args.session)
    if args.command == "launch-plan":
        result = launch.prepare(args.game_root, args.ea_log, args.executable_sha256)
        if args.output:
            new_json(external_path(args.output), result)
        return result
    if args.command == "inspect":
        result = windows.inspect(args.pid, args.executable, args.executable_sha256, args.hwnd)
        if args.output:
            new_json(external_path(args.output), result)
        return {"ok": True, "identity": result}
    if args.command == "observe-exit":
        from .process_exit import observe_exit
        target = external_path(args.output) if args.output else None
        if target is not None and target.exists():
            raise FoundryError("output_exists", "Exit observation needs a new evidence path.")
        result = observe_exit(read_json(args.identity), args.seconds)
        if target is not None:
            new_json(target, result)
        return result
    if args.command == "discover":
        result = discovery.discover(args.executable, args.executable_sha256)
        if args.output:
            new_json(external_path(args.output), result)
        return result
    if args.command == "capture":
        return windows.capture(read_json(args.identity), args.ffmpeg, args.ffmpeg_sha256, args.destination)
    if args.command == "mods":
        if args.action == "resource-diff":
            result = resource_diff.compare_resources(args.before, args.after)
            if args.output:
                new_json(external_path(args.output), result)
            return result
        if args.action == "bytecode":
            result = bytecode_headers.inspect_headers(args.file, args.target_magic)
            if args.output:
                new_json(external_path(args.output), result)
            return result
        if args.action in ("resources", "overlaps"):
            result = (package_resources.inspect_resources(args.file) if args.action == "resources"
                      else package_resources.audit_overlaps(args.file))
            if args.output:
                new_json(external_path(args.output), result)
            return result
        if args.action == "check":
            return {"ok": True, "artifact": mods.inspect_artifact(args.file), "code_executed": False}
        if args.action == "inventory":
            result = mods.inventory(args.directory)
            if args.output:
                new_json(external_path(args.output), result)
            return {"ok": True, "manifest": result}
        if args.action == "compare":
            return mods.compare(read_json(args.before), read_json(args.after))
        return mods.stage(read_json(args.manifest), args.destination, args.execute)
    if args.command == "apex-inventory":
        return apex.inspect_source(args.repository)
    raise FoundryError("unsupported_command", "Unsupported command.")


def main(argv=None):
    try:
        result = run(parser().parse_args(argv))
    except FoundryError as exc:
        result = exc.as_dict()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # Filesystem/configuration failures stay nonzero and machine-readable.
        result = {"ok": False, "code": "local_failure", "message": str(exc), "error_type": type(exc).__name__}
    print(json.dumps(result, ensure_ascii=True, allow_nan=False, indent=2))
    if result.get("ok"):
        return 0
    if result.get("outcome") == "unresolved" or result.get("code") in ("dispatch_uncertain", "pending_operation"):
        return 3
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
