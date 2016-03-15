"""Manages the asyncio loop."""

import asyncio
import traceback
import logging

import bpy

log = logging.getLogger(__name__)


def kick_async_loop(*args):
    loop = asyncio.get_event_loop()

    if loop.is_closed():
        log.warning('loop closed, stopping')
        stop_async_loop()
        return

    all_tasks = asyncio.Task.all_tasks()
    if not all_tasks:
        log.debug('no more scheduled tasks, stopping')
        stop_async_loop()
        return

    if all(task.done() for task in all_tasks):
        log.info('all tasks are done, fetching results and stopping.'.format(__name__))
        for task in all_tasks:
            # noinspection PyBroadException
            try:
                task.result()
            except asyncio.CancelledError:
                # No problem, we want to stop anyway.
                pass
            except Exception:
                print('{}: resulted in exception'.format(task))
                traceback.print_exc()
        stop_async_loop()
        return

    # Perform a single async loop step
    async def do_nothing():
        pass

    loop.run_until_complete(do_nothing())


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
