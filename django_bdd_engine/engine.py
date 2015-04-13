import logging
import time
import datetime
import pytz  # timezone support: datetime.datetime.now(pytz.utc)

from django_bdd_engine.toplevel import *  # initialize stage and django backend

from django_bdd_engine.testdriver import TestDriver

# import db models
from django.db import connection
from django_bdd.models import TestRun, NEW, ERROR

# metrics keys
from django_bdd_engine.utility.cloudwatch import (
    put_metric_data,
    METRIC_ENGINE_HEARTBEAT,
    METRIC_ENGINE_NEW_TEST_QUERY_DURATION,
    METRIC_ENGINE_TEST_QUEUE,
    METRIC_ENGINE_EXECUTION_TIME_SANS_TEST,
    METRIC_ENGINE_TEST_RUN_ERROR
)

from mobilebdd.environment import get_runtime_requirements_from_steps


NAPPY_TIME = 15  # length of time to sleep (seconds)

log = logging.getLogger(u'django-bdd')
email_log = logging.getLogger(u'email_log')


class DjangoBDDEngine(object):

    def __init__(self, endpoint=None, runners=None):
        """
        :param runners: A list of potential runner classes for tests.
        :type runners: list
        """
        # if there are not runners, there must be an endpoint to run against
        if not runners:
            assert endpoint, u'DjangoBDDEngine was not given an endpoint to run tests against'
        self.endpoint = endpoint
        self.runners = runners
        self.tasks = []  # list of tasks to execute

    def add_task(self, task):
        """
        Adds a task to the list.
        """
        self.tasks.append(task)

    def run(self):
        """
        Run an infinite loop pinging the database for new tests.
        """
        log.debug(u'engine is now running')

        while True:
            engine_loop_start = datetime.datetime.now(pytz.utc)

            # run all tasks that need to be performed
            log.debug(u'{} tasks need to be run'.format(len(self.tasks)))
            tasks_to_remove = []
            for task in self.tasks:
                log.debug(u'running task: {}'.format(task))

                try:
                    task.update()
                    if task.done:
                        tasks_to_remove.append(task)
                except Exception as e:
                    tasks_to_remove.append(task)
                    error_message = u'exception when running task.update: {}'.format(unicode(e))
                    log.error(error_message)
                    email_log.error(error_message)

            # and then remove any tasks that are done
            for task in tasks_to_remove:
                self.tasks.remove(task)

            # ping db to see if there are any new tests
            log.debug(u'pinging database for new tests')

            # report the engine heartbeat metric
            put_metric_data(METRIC_ENGINE_HEARTBEAT, value=1)

            # query db, order by least -> most recent requested test runs for justice
            try:
                start_time = datetime.datetime.now(pytz.utc)
                new_test_runs = TestRun.objects.filter(status=NEW).order_by(u'pk')
                end_time = datetime.datetime.now(pytz.utc)
                total_seconds = (end_time - start_time).total_seconds()

                # report the query time metric
                log.debug(u'queried test runs in {} seconds, reporting metric'.format(total_seconds))
                put_metric_data(METRIC_ENGINE_NEW_TEST_QUERY_DURATION, value=total_seconds)
            except:
                log.error(u'new test run query failed')
                log.debug(u'HACK closing connection to make django re-open it again')
                log.error(u'engine runtime metric is being dropped')  # TODO: make this continue play nice with metrics
                connection.close()
                continue

            # report the test queue length metric
            queue_length = len(new_test_runs) - 1
            queue_length = queue_length if queue_length >= 0 else 0
            log.debug(u'found {} {} test runs, queue_length is {}, reporting metric'.format(len(new_test_runs), NEW, queue_length))
            put_metric_data(METRIC_ENGINE_TEST_QUEUE, value=queue_length)

            # if there are any runs, grab the first and run it
            test_runner_start = datetime.datetime.now(pytz.utc)
            if new_test_runs:
                # run the first test
                test_run = new_test_runs[0]
                test_run_id = test_run.id

                # wrap these calls, we really don't want the engine to go down
                try:
                    test_runtime_requirements = get_runtime_requirements_from_steps(test_run.test.steps)

                    test_runner = None
                    for runner in self.runners:
                        if runner.is_compatible(test_runtime_requirements):
                            log.debug(u'creating {} runner for test run {}'.format(runner, test_run_id))
                            test_runner = runner(engine=self, test_run_id=test_run_id)
                    if not test_runner:
                        log.debug(u'creating TestDriver for test run {}'.format(test_run_id))
                        test_runner = TestDriver(engine=self, test_run_id=test_run_id, endpoint=self.endpoint)

                    log.debug(u'running test run {}'.format(test_run_id))
                    try:
                        test_runner.start()
                    except Exception as e:
                        connection.close()  # HACK: make sure the db connection works
                        test_run = TestRun.objects.get(pk=test_run_id)
                        test_run.text += unicode(e)
                        test_run.status = ERROR
                        test_run.save()
                except Exception as e:
                    log.error(u'error running test run {}, reporting metric, exception: {}'.format(test_run_id, unicode(e)))
                    put_metric_data(METRIC_ENGINE_TEST_RUN_ERROR, value=1)
            test_runner_end = datetime.datetime.now(pytz.utc)
            engine_loop_end = datetime.datetime.now(pytz.utc)
            engine_execution_time_sans_test = (engine_loop_end - engine_loop_start).total_seconds() - (test_runner_end - test_runner_start).total_seconds()
            log.debug(u'engine loop complete in {} seconds, reporting metric'.format(engine_execution_time_sans_test))
            put_metric_data(METRIC_ENGINE_EXECUTION_TIME_SANS_TEST, value=engine_execution_time_sans_test)

            # only sleep if there weren't any new test runs
            if not new_test_runs:
                log.debug(u'sleeping for {} seconds before pinging db again'.format(NAPPY_TIME))
                time.sleep(NAPPY_TIME)


if __name__ == u'__main__':
    log.debug(u'engine.py is starting up')
    engine = DjangoBDDEngine(u'http://localhost:4723')
    engine.run()

    # should never happen
    log.error(u'DjangoBDDEngine.run() has returned')
