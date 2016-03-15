"""Manages the asyncio loop."""

import asyncio
import traceback
import concurrent.futures
import logging

import bpy

log = logging.getLogger(__name__)


def setup_asyncio_executor():
    """Sets up AsyncIO to run on a single thread.

    This ensures that only one Pillar HTTP call is performed at the same time. Other
    calls that could be performed in parallel are queued, and thus we can
    reliably cancel them.
    """

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    loop = asyncio.get_event_loop()
    loop.set_default_executor(executor)
    # loop.set_debug(True)


def kick_async_loop(*args):
    loop = asyncio.get_event_loop()

    if loop.is_closed():
        log.warning('loop closed, stopping')
        stop_async_loop()
        return

    all_tasks = asyncio.Task.all_tasks()
    if not len(all_tasks):
        log.debug('no more scheduled tasks, stopping')
        stop_async_loop()
        return

    if all(task.done() for task in all_tasks):
        log.info('all %i tasks are done, fetching results and stopping.', len(all_tasks))
        for task_idx, task in enumerate(all_tasks):
            # noinspection PyBroadException
            try:
                res = task.result()
                log.debug('   task #%i: result=%r', task_idx, res)
            except asyncio.CancelledError:
                # No problem, we want to stop anyway.
                log.debug('   task #%i: cancelled', task_idx)
            except Exception:
                print('{}: resulted in exception'.format(task))
                traceback.print_exc()
        stop_async_loop()
        return

    # Perform a single async loop step
    def stop_loop(future):
        future.set_result('done')

    future = asyncio.Future()
    loop.call_later(0.005, stop_loop, future)
    loop.run_until_complete(future)


def async_loop_handler() -> callable:
    """Returns the asynchronous loop handler `kick_async_loop`

    Only returns the function if it is installed as scene_update_pre handler, otherwise
    it returns None.
    """

    name = kick_async_loop.__name__
    for handler in bpy.app.handlers.scene_update_pre:
        if getattr(handler, '__name__', '') == name:
            return handler
    return None


def ensure_async_loop():
    if async_loop_handler() is not None:
        return
    bpy.app.handlers.scene_update_pre.append(kick_async_loop)


def stop_async_loop():
    handler = async_loop_handler()
    if handler is None:
        return
    bpy.app.handlers.scene_update_pre.remove(handler)
    log.debug('stopped async loop.')
