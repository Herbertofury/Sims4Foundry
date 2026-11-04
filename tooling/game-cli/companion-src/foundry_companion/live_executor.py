"""Inert, source-only owner executor for the distinct schema-3 Live read.

Only trusted injected synchronous readers can run. No game import, listener,
bootstrap, implicit actor selection, mutation or qualification is supplied here.
"""

from .live_observation import LiveQueueObserver, _LiveRowLimitExceeded
from .native import NativeRefusal
from .queue import live_request_key, live_request_from_key


def _result(observation=None, reason=None):
    return {'schema_version': 1, 'outcome': 'unverified', 'game_qualified': False,
            'read_status': 'observed' if reason is None else 'unavailable',
            'observation': observation, 'reason': reason}


class LiveQueueExecutor(object):
    """Default-denied one-owner read attempt, never independent game evidence."""
    def __init__(self, context_reader, sim_info_reader, instance_reader,
                 admission=None, session_id=None, process_epoch=None):
        self._observer = LiveQueueObserver(context_reader, sim_info_reader, instance_reader,
                                           admission, session_id, process_epoch)

    def execute(self, request):
        # Capture every primitive before any hook; frozen dataclasses alone do
        # not prevent object.__setattr__ or mutation of nested retained aliases.
        baseline = live_request_key(request)
        original = live_request_from_key(baseline)
        try:
            observation = self._observer.observe_request(request)
            result = _result(observation)
        except _LiveRowLimitExceeded:
            result = _result(reason='row_limit_exceeded')
        if live_request_key(request) != baseline:
            raise NativeRefusal('live_execution_request_changed')
        # Schema/binding errors and unexpected exceptions are not budget failures.
        # The queue records them as unknown, never a known non-dispatch refusal.
        from .live_wire import ResultBudgetExceeded, preview_result_receipt
        try:
            preview_result_receipt(result, original)
        except ResultBudgetExceeded:
            result = _result(reason='result_budget_exceeded')
            preview_result_receipt(result, original)
        return result
