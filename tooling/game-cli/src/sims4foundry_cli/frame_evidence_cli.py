"""Inspect or compare existing request frame evidence; never sends input."""
import argparse
import json

from . import input_frame_evidence, input_journal
from .errors import FoundryError


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('inspect','compare'):
        command = commands.add_parser(name)
        command.add_argument('--journal', required=True)
        command.add_argument('--request-id', required=True)
        command.add_argument('--evidence', required=True)
        if name == 'compare':
            command.add_argument('--frame-sha256', required=True)
            command.add_argument('--evidence-intent-sha256', required=True)
            command.add_argument('--reference', required=True)
            command.add_argument('--reference-sha256', required=True)
            command.add_argument('--region', required=True, nargs=4, type=int, metavar=('X','Y','W','H'))
            command.add_argument('--max-channel-difference', type=int, default=0)
            command.add_argument('--max-mismatched-pixels', type=int, default=0)
    args = parser.parse_args(argv)
    try:
        journal = input_journal.InputJournal(args.journal)
        if args.command == 'inspect':
            result = input_frame_evidence.inspect(journal,args.request_id,args.evidence)
        else:
            result = input_frame_evidence.compare(
                journal,args.request_id,args.evidence,args.frame_sha256,args.evidence_intent_sha256,
                args.reference,args.reference_sha256,args.region,
                max_channel_difference=args.max_channel_difference,
                max_mismatched_pixels=args.max_mismatched_pixels)
    except FoundryError as error:
        result = error.as_dict()
    except (OSError, ValueError, TypeError, KeyError) as error:
        result = {'ok':False, 'code':'local_failure', 'error_type':type(error).__name__}
    print(json.dumps(result,ensure_ascii=True,allow_nan=False,indent=2))
    if result.get('ok') is False:
        return 2
    if args.command == 'inspect':
        return 0 if result.get('state') == 'matching_files_observed' else 1
    return 0 if result.get('ok') and result.get('matched') else 1


if __name__ == '__main__':
    raise SystemExit(main())
