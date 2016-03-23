"""Manages the asyncio loop."""

import asyncio
import traceback
import concurrent.futures
import logging

import bpy

log = logging.getLogger(__name__)

# Keeps track of whether a loop-kicking operator is already running.
_loop_kicking_operator_running = False


def setup_asyncio_executor():
    """Sets up AsyncIO to run on a single thread.

    This ensures that only one Pillar HTTP call is performed at the same time. Other
    calls that could be performed in parallel are queued, and thus we can
    reliably cancel them.
    """

    executor = concurrent.futures.ThreadPoolExecutor()
    loop = asyncio.get_event_loop()
    loop.set_default_executor(executor)
    # loop.set_debug(True)


def kick_async_loop(*args) -> bool:
    """Performs a single iteration of the asyncio event loop.

    :return: whether the asyncio loop should stop after this kick.
    """

    loop = asyncio.get_event_loop()

    # Even when we want to stop, we always need to do one more
    # 'kick' to handle task-done callbacks.
    stop_after_this_kick = False

    if loop.is_closed():
        log.warning('loop closed, stopping immediately.')
        return True

    all_tasks = asyncio.Task.all_tasks()
    if not len(all_tasks):
        log.debug('no more scheduled tasks, stopping after this kick.')
        stop_after_this_kick = True

    elif all(task.done() for task in all_tasks):
        log.debug('all %i tasks are done, fetching results and stopping after this kick.',
                  len(all_tasks))
        stop_after_this_kick = True

        for task_idx, task in enumerate(all_tasks):
            if not task.done():
                continue

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

    loop.stop()
    loop.run_forever()

    return stop_after_this_kick


def ensure_async_loop():
    log.debug('Starting asyncio loop')
    result = bpy.ops.asyncio.loop()
    log.debug('Result of starting modal operator is %r', result)


class AsyncLoopModalOperator(bpy.types.Operator):
    bl_idname = 'asyncio.loop'
    bl_label = 'Runs the asyncio main loop'

    timer = None
    log = logging.getLogger(__name__ + '.AsyncLoopModalOperator')

    def execute(self, context):
        return self.invoke(context, None)

    def invoke(self, context, event):
        global _loop_kicking_operator_running

        if _loop_kicking_operator_running:
            self.log.debug('Another loop-kicking operator is already running.')
            return {'PASS_THROUGH'}

        context.window_manager.modal_handler_add(self)
        _loop_kicking_operator_running = True

        wm = context.window_manager
        self.timer = wm.event_timer_add(0.00001, context.window)

        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        global _loop_kicking_operator_running

        if event.type != 'TIMER':
            return {'PASS_THROUGH'}

        # self.log.debug('KICKING LOOP')
        stop_after_this_kick = kick_async_loop()
        if stop_after_this_kick:
            context.window_manager.event_timer_remove(self.timer)
            _loop_kicking_operator_running = False

            self.log.debug('Stopped asyncio loop kicking')
            return {'FINISHED'}

        return {'RUNNING_MODAL'}


def register():
    bpy.utils.register_class(AsyncLoopModalOperator)


def unregister():
    bpy.utils.unregister_class(AsyncLoopModalOperator)
