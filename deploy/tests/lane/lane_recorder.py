"""LiteLLM callback used ONLY by the lane test (test harness, not product).

The test appends `litellm_settings.callbacks: ["lane_recorder.recorder"]` to the
chart-rendered config and mounts this file next to it; LiteLLM looks for the
module beside the config file first (litellm/proxy/types_utils/utils.py,
get_instance_fn).  The chart itself must never carry callbacks.

`async_pre_call_deployment_hook` runs once per attempted deployment (retries
and fallback hops included) after the deployment is chosen and before the
request is sent; `async_post_call_failure_deployment_hook` runs once per
failed attempt.  Each call appends one JSON line to /records/callback.jsonl.
"""
import json
import time

from litellm.integrations.custom_logger import CustomLogger

OUT = '/records/callback.jsonl'


def _write(entry):
    entry['time'] = time.time()
    with open(OUT, 'a') as handle:
        handle.write(json.dumps(entry, default=str) + '\n')


def _describe(kwargs):
    # Confirmed against v1.106.6 by dumping the hook's kwargs: the chosen
    # deployment's settings arrive at the TOP LEVEL (`api_base`, `model_info`,
    # `custom_llm_provider`), and `litellm_params` is empty there.  Both places
    # are read so a version that moves them back still records them.
    params = kwargs.get('litellm_params') or {}
    info = kwargs.get('model_info') or params.get('model_info') or {}
    return {
        'requested_model': kwargs.get('model'),
        'provider': kwargs.get('custom_llm_provider') or params.get('custom_llm_provider'),
        'api_base': kwargs.get('api_base') or params.get('api_base'),
        'deployment_id': info.get('id'),
        'fallback_depth': kwargs.get('fallback_depth'),
    }


class LaneRecorder(CustomLogger):
    async def async_pre_call_deployment_hook(self, kwargs, call_type):
        _write({'event': 'attempt', 'call_type': str(call_type), **_describe(kwargs)})

    async def async_post_call_failure_deployment_hook(self, request_data, exception, call_type, fallback_depth=None):
        _write({'event': 'failure', 'call_type': str(call_type), 'exception': type(exception).__name__,
                'status_code': getattr(exception, 'status_code', None),
                'fallback_depth': fallback_depth, **_describe(dict(request_data))})


recorder = LaneRecorder()
