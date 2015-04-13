from django_bdd_engine.toplevel import *  # database preparation

import os
import tempfile
import time
import datetime
import pytz  # timezone support: datetime.datetime.now(pytz.utc)
import shutil
import logging
import traceback

from django_bdd_engine.utility.cloudwatch import put_metric_data, METRIC_ENGINE_TEST_RUN_DURATION

from django.conf import settings
from django.db import connection

from s3util.s3util import S3Util
from django_bdd.models import NEW, RUNNING, FAILED, PASSED, SKIPPED, TestRun, TestRunStep
from django_bdd.notifications import notify
from mobilebdd.runner import goh_behave
from mobilebdd.listener import Listener  # defines a set of hooks that behave calls


log = logging.getLogger(u'django-bdd')


SCREENSHOT_FOLDER = u'bdd/results/{test_id}/{run_id}/{example_row_num}'
SCREENSHOT_NAME_TEMPLATE = SCREENSHOT_FOLDER + u'/{filename}'


def ensure_connection():
    """This is a hack to make sure that the django db connection wil be available
    before any query or save that has had too much time pass before it.
    """
    # HACK django db connections sometimes time out
    connection.close()


class TestDriver(Listener):
    """Takes a test_run to use for saving results, and defines hooks for
    Behave to call in order to save the results to the db.
    """

    def __init__(self, engine, test_run_id, endpoint, webdriver_processor=None):
        """
        :param test_run_id: the test_run id
        :param endpoint: the endpoint to run the test against
        :param webdriver_processor: a class (or None) implementing hooks like a
            cabability filter, allowing outside users to have the final say on
            capabilities
        """
        super(Listener, self).__init__()
        log.debug(u'running against endpoint "{}"'.format(endpoint))

        self.s3_util = S3Util(settings.AWS_ACCESS_KEY, settings.AWS_SECRET_ACCESS_KEY, s3_bucket=settings.AWS_BUCKET)

        log.debug(u'getting test run {}'.format(test_run_id))
        ensure_connection()
        self.test_run = TestRun.objects.get(pk=test_run_id)
        self.endpoint = endpoint
        self.webdriver_processor = webdriver_processor
        self.example_row_num = 1  # keep track of which permutation row number of the scenario we're on
        self.test_step_num = 1  # keep track of which test step number we're on within a permutation

        # create a feature file and the necessary folders
        log.debug(u'creating temp folders and files')
        self.feature_dir = tempfile.mkdtemp()
        self.result_dir = tempfile.mkdtemp()
        self.feature_file_text = u''
        self.feature_file = tempfile.NamedTemporaryFile(suffix=u'.feature', dir=self.feature_dir)

        # pull the steps out of the test run's test object
        steps = self.test_run.test.steps

        # if the feature has not been explicitly defined, create a Feature: entry at the top of the file
        if 'Feature:' not in steps:
            self.feature_file_text += u'Feature: {}\r\n'.format(self.test_run.test.name)

        # if it's a scenario, there are no examples
        # if it's a scenario outline, Examples: should be present
        # http://jenisys.github.io/behave.example/tutorials/tutorial04.html

        # if the user has not explicitly defined a Scenario: or Scenario Outline section, detect which one should be created
        if 'Scenario:' not in steps and 'Scenario Outline:' not in steps:
            if 'Examples:' in steps:
                log.debug('examples detected in steps, writing scenario outline to feature file')
                scenario_header = u'Scenario Outline: {}\r\n'
            elif self.test_run.example_text:
                log.debug('examples detected in test run example_text, writing scenario outline to feature file')
                scenario_header = u'Scenario Outline: {}\r\n'
            else:
                log.debug('writing normal scenario to feature file')
                scenario_header = u'Scenario: {}\r\n'

            # write the scenario header to the feature file text
            self.feature_file_text += scenario_header.format(self.test_run.test.name)

        self.feature_file_text += steps
        self.feature_file_text += u'\r\n\r\n'  # add some space at the end of the file

        # if example text exists in the test run, write an 'Examples:' section
        # with the text that is present in the db
        if self.test_run.example_text:
            self.feature_file_text += u'Examples:\r\n'
            self.feature_file_text += self.test_run.example_text

        log.debug(u'writing to feature file:\r\n{}'.format(self.feature_file_text))
        self.feature_file.write(self.feature_file_text.encode(u'utf8'))

        log.debug(u'flushing feature file')
        self.feature_file.file.flush()

    def start(self):
        # measure elapsed time in case the test run throws an exception
        start_time = time.time()

        step_dirs = []
        try:
            log.debug(u'calling goh_behave')
            goh_behave(
                feature_dirs=[self.feature_dir],
                step_dirs=step_dirs,
                test_artifact_dir=self.result_dir,
                listeners=[self],
                webdriver_url=self.endpoint,
                webdriver_processor=self.webdriver_processor
            )

            # report the test run duration metric
            log.debug(u'completed running test run {} in {} seconds, reporting metric'.format(self.test_run.id, self.test_run.duration))
            put_metric_data(METRIC_ENGINE_TEST_RUN_DURATION, value=self.test_run.duration)
        except Exception as e:
            # there's a chance that an exception might be thrown. if so, then
            # there's also a chance that the callbacks didnt get reached, so we
            # catch here and report failure
            log.debug(u'goh_behave threw an exception: {}'.format(unicode(e)))
            self.test_run.status = FAILED

            # add the exception text as well as the traceback
            self.test_run.text += u'Exception:\n' + unicode(e) + u'\n\nTraceback:\n' + unicode(traceback.format_exc())

            self.test_run.duration = time.time() - start_time
            ensure_connection()
            self.test_run.save()
        finally:
            # always cleanup
            log.debug(u'cleaning temp directories')
            shutil.rmtree(self.feature_dir)
            shutil.rmtree(self.result_dir)

    """Behave Hooks"""
    def before_feature(self, feature):
        """Inspect the feature. Here, we create pre-create every step that is
        going to be necessary in the db so that we can start recording results.
        """
        log.debug(u'before_feature')

        # set the test to running immediately
        self.test_run.status = RUNNING
        ensure_connection()
        self.test_run.save()

        # the Behave 'Example' row number - the way this works is Behave generates a Scenario for
        # each row in the example tables, regardless of how many tables there are. all of the
        # tables must conform to the same structure, so we just need to have one set of step results
        # per now in these tables. to think of it another way, per scenario generated by walk_scenarios()
        # as we call below. walk_scenarios() will only return 1 result if it's a regular Scenario as
        # opposed to a ScenarioOutline object, which is fine
        example_row_num = 1
        for scenario in feature.walk_scenarios():
            log.debug(u'creating step result entries in db for "Example" row number {}'.format(example_row_num))

            # run through all background steps and create them in the db
            start = 1
            for num, step in enumerate(scenario.background_steps, start=start):
                self.test_run.testrunstep_set.create(
                    num=num,
                    example_row_num=example_row_num,
                    text=u'{} {}'.format(step.keyword, step.name),
                    status=NEW
                )
                start += 1

            # run through all normal steps and create them in the db
            for num, step in enumerate(scenario.steps, start=start):
                self.test_run.testrunstep_set.create(
                    num=num,
                    example_row_num=example_row_num,
                    text=u'{} {}'.format(step.keyword, step.name),
                    status=NEW
                )
            example_row_num += 1

    def before_scenario(self, scenario):
        self.test_step_num = 1  # reset the test step number we're on

    def before_step(self, step):
        log.debug(u'before_step: test_step_num = {}'.format(self.test_step_num))
        # mark the step was running in the db
        try:
            ensure_connection()
            test_step = self.test_run.testrunstep_set.get(
                num=self.test_step_num,
                example_row_num=self.example_row_num,
                text=u'{} {}'.format(step.keyword, step.name)
            )
        except TestRunStep.DoesNotExist:
            # don't care, if not found, then likely this is a substep that
            # doesnt need to be reported in the ui
            pass
        else:
            log.debug(u'setting step {} to running'.format(test_step.id))
            test_step.status = RUNNING
            test_step.timestamp_start = datetime.datetime.now(pytz.utc)
            test_step.save()

    def after_step(self, step):
        log.debug(u'after_step')

        if step.error_message:
            log.debug(u'adding step error message to test run text: {}'.format(step.error_message))
            self.test_run.text += step.error_message
            ensure_connection()
            self.test_run.save()

        screenshot_key = None
        # save a screenshot if the test result step got one
        if step.screenshot_path:
            # put in s3
            filename = os.path.basename(step.screenshot_path)
            screenshot_key = SCREENSHOT_NAME_TEMPLATE.format(
                test_id=self.test_run.test.id,
                run_id=self.test_run.id,
                example_row_num=self.example_row_num,
                filename=filename
            )
            log.debug(u'saving screenshot at "{}" to s3 with key "{}"'.format(filename, screenshot_key))
            with open(step.screenshot_path) as f:
                self.s3_util.save_screenshot(screenshot_key, f)
            log.debug(u'done saving screenshot to s3')

        # update our db stuff
        # try to get the appropriate step
        # we query instead of caching locally because bdd steps might have
        # substeps, which will still trigger this callback and throw off our
        # counting/step mapping.
        step_text = u'{} {}'.format(step.keyword, step.name)
        try:
            # use both num and text, because any of these steps could 'expand' via substeps
            log.debug(u'getting test_step with {}'.format(
                    {u'num': self.test_step_num, u'example_row_num': self.example_row_num, u'text': step_text}
                )
            )
            ensure_connection()
            test_step = self.test_run.testrunstep_set.get(
                num=self.test_step_num,
                example_row_num=self.example_row_num,
                text=step_text
            )
        except TestRunStep.DoesNotExist:
            # do nothing, because it's a step that wasn't in the original test
            # ie. a substep.
            log.warn(u'after_step: test run step "{}" does not exist, could be a real error or a "substep"'.format(step_text))
        else:
            test_step.status = step.status
            test_step.timestamp_end = datetime.datetime.now(pytz.utc)
            test_step.duration = step.duration
            test_step.screenshot_s3_key = screenshot_key
            ensure_connection()
            test_step.save()

            # incr test step cuz we found the 'real' step
            self.test_step_num += 1

        log.debug(u'after_step: test_step_num is now {}'.format(self.test_step_num))

    def after_scenario(self, scenario):
        log.debug(u'after_scenario')

        # increment the 'Example' row number we're on
        self.example_row_num += 1

        log.debug(u'after_scenario: example_row_num is now {}'.format(self.example_row_num))

    def after_feature(self, feature):
        log.debug(u'after_feature - feature status: {}'.format(feature.status))

        # update the test run status based on the feature's final status
        if feature.status == u'skipped':
            self.test_run.status = SKIPPED
        elif feature.status == u'passed':
            self.test_run.status = PASSED
        elif feature.status == u'failed':
            self.test_run.status = FAILED

        self.test_run.duration = feature.duration
        ensure_connection()
        self.test_run.save()

        # try sending an email about the test run
        log.debug(u'sending notification email to test run user: {}'.format(self.test_run.user))
        notify(self.test_run)
        log.debug(u'done sending email')
