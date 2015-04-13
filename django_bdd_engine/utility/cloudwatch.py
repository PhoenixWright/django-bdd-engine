import logging
import datetime
import pytz  # timezone support: datetime.datetime.now(pytz.utc)

# create boto cloudwatch connection for metrics
from django.conf import settings
import boto
from boto.ec2.cloudwatch import CloudWatchConnection

# region to record metrics in
CLOUDWATCH_REGION = u'us-west-2'  # p-town baby

# metrics names
METRIC_ENGINE_HEARTBEAT = u'EngineHeartbeat'
METRIC_ENGINE_NEW_TEST_QUERY_DURATION = u'EngineNewTestQueryDuration'
METRIC_ENGINE_TEST_QUEUE = u'EngineTestQueue'
METRIC_ENGINE_TEST_RUN_DURATION = u'EngineTestRunDuration'
METRIC_ENGINE_EXECUTION_TIME_SANS_TEST = u'EngineExecutionTimeSansTest'
METRIC_ENGINE_TEST_RUN_ERROR = u'EngineTestRunError'

# a region object must be passed to the connection, this is how to get it
for r in boto.ec2.cloudwatch.regions():
    if (r.name == CLOUDWATCH_REGION):
        region = r

log = logging.getLogger(u'django-bdd')

# initialize a connection to cloudwatch for reporting metrics
cloudwatch = CloudWatchConnection(
    aws_access_key_id=settings.AWS_ACCESS_KEY,
    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    region=region
)


def put_metric_data(name, value, timestamp=datetime.datetime.now(pytz.utc)):
    """Utility function for adding a metric to cloudwatch.
    """
    try:
        cloudwatch.put_metric_data(
            settings.CLOUDWATCH_NAMESPACE,
            name,
            value=value,
            timestamp=timestamp
        )
    except Exception as e:
        log.error(u'error reporting metric: {}'.format(unicode(e)))
