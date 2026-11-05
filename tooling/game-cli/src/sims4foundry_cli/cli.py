"""Structured CLI. All semantic execution is explicitly synthetic in 0.1."""
import argparse
from dataclasses import asdict
import json
import sys
from . import __version__
from . import neutral_testmods, neutral_profile, neutral_launch, neutral_ea_consent, neutral_launch_recovery, neutral_records
from . import neutral_testmod_recovery
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
    independent = commands.add_parser("independent-session", help="Create or inspect cooperative records; live input remains disabled")
    independent.add_argument("action", choices=("authority-init", "initialize", "fixture-initialize", "status", "input-status", "input-once"))
    independent.add_argument("--authority", required=True)
    independent.add_argument("--session")
    independent.add_argument("--identity", help="External Foundry identity JSON; accepted as supplied, never inspected live")
    independent.add_argument("--request-id")
    live_owner = commands.add_parser("experimental-live-owner", help="Foreground single-owner stdin service; not game-qualified")
    live_owner.add_argument("--authority", required=True)
    live_owner.add_argument("--session", required=True)
    live_owner.add_argument("--witness-id", required=True)
    live_owner.add_argument("--ffmpeg", required=True)
    live_owner.add_argument("--ffmpeg-sha256", required=True)
    live_owner.add_argument("--allow-experimental", action="store_true")
    global_authority = commands.add_parser("global-authority", help="Initialize or inspect experimental cooperative lifetime records")
    global_authority.add_argument("action", choices=("initialize", "status", "inspect"))
    global_authority.add_argument("--identity", help="Original external identity JSON for reservation inspection")
    fixture_owner = commands.add_parser("fixture-owner", help="Synthetic retained owner only; never controls the game")
    fixture_owner.add_argument("action", choices=("identity", "serve", "serve-persistent", "client", "receipt", "launch", "interactive"))
    fixture_owner.add_argument("--authority")
    fixture_owner.add_argument("--session")
    fixture_owner.add_argument("--endpoint")
    fixture_owner.add_argument("--request", help="Bounded fixture request JSON; secret arrives only through stdin")
    fixture_owner.add_argument("--count", type=int)
    fixture_owner.add_argument("--owner-id")
    fixture_owner.add_argument("--operation-id")
    fixture_owner.add_argument("--requests", help="Bounded JSON list of synthetic operation templates")
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
    probe_report = commands.add_parser("startup-probe-report", help="Inspect explicit startup self-report files; never qualify or contact a game")
    probe_report.add_argument("--report", required=True)
    probe_report.add_argument("--manifest", required=True)
    probe_report.add_argument("--run-id", required=True)
    probe_report.add_argument("--expected-pid", required=True, type=int)
    probe_report.add_argument("--archive")
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
    game_launch = commands.add_parser("game-launch", help="Launch EA once using an active prepared test profile")
    game_launch.add_argument("--game-root", required=True)
    game_launch.add_argument("--ea-log", required=True, action="append")
    game_launch.add_argument("--executable-sha256", required=True)
    game_launch.add_argument("--ea-launcher", required=True)
    game_launch.add_argument("--launcher-sha256", required=True)
    game_launch.add_argument("--profile-session", required=True)
    game_launch.add_argument("--session", required=True)
    game_launch.add_argument("--seconds", type=float, default=30.0)
    ea_context = commands.add_parser("game-launch-ea-context", help="Read a fresh EA request for an existing handoff")
    ea_context.add_argument("--session", required=True)
    ea_context.add_argument("--output")
    consent = commands.add_parser("game-launch-ea-consent", help="Acknowledge one exact correlated EA launch prompt")
    consent.add_argument("--session", required=True)
    consent.add_argument("--ea-image", required=True)
    consent.add_argument("--ea-sha256", required=True)
    consent.add_argument("--seconds", type=float, default=20.0)
    game_observe = commands.add_parser("game-launch-observe", help="Observe an existing launch without dispatching again")
    game_observe.add_argument("--session", required=True)
    game_observe.add_argument("--seconds", type=float, default=30.0)
    game_observe.add_argument("--output")
    resolve = commands.add_parser("game-launch-reconcile-not-dispatched", help="Resolve only a proven never-dispatched launch; never cancel EA work")
    resolve.add_argument("--profile-session", required=True)
    resolve.add_argument("--session", required=True)
    profile = commands.add_parser("test-profile", help="Prepare and restore an explicit test profile",
        description=("Receipt inspection observes point-in-time identity; its lock may be created or initialized. "
                     "It does not move a profile, verify save integrity, or authorize launch."))
    profile.add_argument("action", choices=("activate", "status", "restore", "recover", "recover-activation",
                                            "inspect-restoration-receipt"))
    profile.add_argument("--session", required=True)
    profile.add_argument("--profile")
    testmods = commands.add_parser("test-mods", help="Install, inspect, or retire a selected test-mod set")
    testmods.add_argument("action", choices=("install", "retire", "recover-retirement", "status"))
    testmods.add_argument("--session", required=True)
    testmods.add_argument("--profile")
    testmods.add_argument("--profile-session", help="Exact activation session required for a Foundry-marked profile")
    testmods.add_argument("--manifest")
    migration = commands.add_parser("apex-inventory", help="Parse existing Apex CLI without importing it or contacting the game")
    migration.add_argument("--repository", required=True)
    return parser


def run(args):
    if args.command == "global-authority":
        from . import global_lifetime_authority
        if args.action in ("initialize", "status"):
            if args.identity is not None:
                raise FoundryError("invalid_arguments", "This action does not accept an identity.")
            if args.action == "status": return global_lifetime_authority.status()
            return {"ok":True, **global_lifetime_authority.initialize()}
        if args.identity is None:
            raise FoundryError("invalid_arguments", "Original identity JSON is required for reservation inspection.")
        return {"ok":True, **global_lifetime_authority.inspect(neutral_records.read_json(external_path(args.identity)))}
    if args.command == "experimental-live-owner":
        if not args.allow_experimental:
            raise FoundryError("live_owner_unqualified", "Explicit experimental opt-in is required; this is not game-qualified.")
        from . import independent_live_service
        return independent_live_service.serve(args.authority,args.session,args.witness_id,args.ffmpeg,args.ffmpeg_sha256)
    if args.command == "fixture-owner":
        from . import fixture_launcher
        if args.action == "identity":
            if any(value is not None for value in (args.authority, args.session, args.endpoint, args.request, args.count,
                                                   args.owner_id, args.operation_id, args.requests)):
                raise FoundryError("invalid_arguments", "Fixture identity accepts no options.")
            return {"ok": True, "identity": fixture_launcher.FIXTURE_IDENTITY,
                    "game_qualified": False, "live_dispatch_available": False}
        if args.action in ("serve-persistent", "interactive"):
            if (not args.authority or not args.session or args.endpoint is not None or args.request is not None or
                    args.count is not None or args.owner_id is not None or args.operation_id is not None or args.requests is not None):
                raise FoundryError("invalid_arguments", "Persistent fixture mode requires its authority and session only.")
            from . import fixture_interactive
            if args.action == "serve-persistent":
                return fixture_interactive.serve(args.authority, args.session)
            return fixture_interactive.launch(args.authority, args.session)
        if args.action == "serve":
            if (not args.authority or not args.session or args.endpoint is not None or args.request is not None or
                    args.owner_id is not None or args.operation_id is not None or args.requests is not None):
                raise FoundryError("invalid_arguments", "Fixture serve requires its original authority and session only.")
            return fixture_launcher.serve(args.authority, args.session, count=1 if args.count is None else args.count)
        if args.action == "receipt":
            if (not args.authority or not args.session or not args.owner_id or not args.operation_id or
                    args.endpoint is not None or args.request is not None or args.count is not None or args.requests is not None):
                raise FoundryError("invalid_arguments", "Receipt inspection requires original session, owner and operation IDs.")
            return fixture_launcher.inspect_receipt(args.authority, args.session, args.owner_id, args.operation_id)
        if args.action == "launch":
            if (not args.authority or not args.session or args.requests is None or args.endpoint is not None or
                    args.request is not None or args.count is not None or args.owner_id is not None or args.operation_id is not None):
                raise FoundryError("invalid_arguments", "Fixture launcher requires authority, session and request list only.")
            return fixture_launcher.launch(args.authority, args.session, loads(args.requests))
        if (args.authority is not None or args.session is not None or args.count is not None or not args.endpoint or
                args.request is None or args.owner_id is not None or args.operation_id is not None or args.requests is not None):
            raise FoundryError("invalid_arguments", "Fixture client requires only endpoint and exact request JSON.")
        return fixture_launcher.client(args.endpoint, loads(args.request))
    if args.command == "independent-session":
        from . import independent_session
        if args.action == "input-once":
            return independent_session.submit_input_once()
        if args.action == "authority-init":
            if args.session is not None or args.identity is not None or args.request_id is not None:
                raise FoundryError("invalid_arguments", "Authority initialization accepts only its new directory.")
            return independent_session.initialize_authority(args.authority)
        if args.session is None:
            raise FoundryError("invalid_arguments", "An explicit independent session directory is required.")
        if args.action == "fixture-initialize":
            if args.identity is not None or args.request_id is not None:
                raise FoundryError("invalid_arguments", "Fixture initialization uses only the built-in synthetic identity.")
            from .fixture_launcher import FIXTURE_IDENTITY
            return independent_session.initialize_session(args.authority, args.session, FIXTURE_IDENTITY)
        if args.action == "initialize":
            if args.identity is None or args.request_id is not None:
                raise FoundryError("invalid_arguments", "Initialization requires only one external identity record.")
            return independent_session.initialize_session(args.authority, args.session,
                                                         neutral_records.read_json(external_path(args.identity)))
        if args.identity is not None or (args.action == "status" and args.request_id is not None):
            raise FoundryError("invalid_arguments", "Status does not accept a replacement identity or request ID.")
        if args.action == "input-status" and args.request_id is None:
            raise FoundryError("invalid_arguments", "Input status requires the original request ID.")
        return independent_session.status(args.authority, args.session, args.request_id)
    if args.command == "startup-probe-report":
        from .startup_probe_report import inspect_startup_probe
        return inspect_startup_probe(args.report, args.manifest,
                                     expected_run_id=args.run_id, expected_pid=args.expected_pid,
                                     archive_path=args.archive)
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
    if args.command == "game-launch":
        return neutral_launch.execute(args.game_root, args.ea_log, args.executable_sha256,
                                      args.ea_launcher, args.launcher_sha256,
                                      args.profile_session, args.session, args.seconds)
    if args.command == "game-launch-ea-context":
        return neutral_launch.ea_context(args.session, args.output)
    if args.command == "game-launch-ea-consent":
        return neutral_ea_consent.acknowledge(args.session, args.ea_image, args.ea_sha256,
                                              timeout_seconds=args.seconds)
    if args.command == "game-launch-observe":
        return neutral_launch.observe(args.session, args.seconds, args.output)
    if args.command == "game-launch-reconcile-not-dispatched":
        return neutral_launch_recovery.reconcile_not_dispatched(args.profile_session, args.session)
    if args.command == "test-profile":
        if args.action == "activate":
            if not args.profile:
                raise FoundryError("missing_arguments", "Activation needs an explicit existing profile.")
            return neutral_profile.activate(args.profile, args.session)
        if args.action == "status":
            return neutral_profile.status(args.session)
        if args.action == "restore":
            return neutral_profile.restore(args.session)
        if args.action == "recover":
            return neutral_profile.recover(args.session)
        if args.action == "inspect-restoration-receipt":
            if args.profile is not None:
                raise FoundryError("invalid_arguments", "Receipt inspection uses only the exact existing session.")
            return neutral_profile.reconcile_restored_receipt(args.session)
        return neutral_profile.recover_activation(args.session)
    if args.command == "test-mods":
        if args.action == "status":
            return neutral_testmods.status(args.session)
        if args.action == "retire":
            return neutral_testmods.retire(args.session)
        if args.action == "recover-retirement":
            if args.profile_session is not None:
                raise FoundryError("invalid_arguments", "Recovery uses the stored activation binding, not a supplied override.")
            return neutral_testmod_recovery.recover_retirement(args.session)
        if not args.manifest or not args.profile:
            raise FoundryError("missing_arguments", "Install needs an explicit profile and inventory manifest.")
        return neutral_testmods.install(neutral_records.read_json(args.manifest), args.profile, args.session,
                                        profile_session=args.profile_session)
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
