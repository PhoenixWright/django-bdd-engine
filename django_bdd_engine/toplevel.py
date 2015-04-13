import logging
import logging.handlers
import argparse

# django backend
from django.conf import settings as django_settings
from django_root import settings as settings_file


log = logging.getLogger(u'django-bdd')

DEBUG = True

try:
    from amazon_django_bdd_engine.utility.toplevel import DEBUG as AMAZON_DEBUG
    DEBUG = DEBUG or AMAZON_DEBUG
except:
    pass  # do nothing


# also check if there's a stage command line arg (used by ARMED)
parser = argparse.ArgumentParser(description=u'process command line args')
parser.add_argument(u'--stage', dest=u'stage', action=u'store', default=u'beta',
    help=u'set the django-bdd stage to run against - defaults to beta', required=False)
args, unknown = parser.parse_known_args()

# if we're set to debug, then let the stage arg triumph over the check for the apollo data
if DEBUG:
    if args.stage == u'prod':
        DEBUG = False


class Stage:
    PROD = u'prod'
    BETA = u'beta'

    def __init__(self, stage):
        self.stage = stage

        if stage == Stage.BETA:
            # configure the db
            django_settings.configure(settings_file)

        elif stage == Stage.PROD:
            # configure the db, point to the prod one
            settings_file.set_databases(DEBUG)
            django_settings.configure(settings_file)

# stage setup
if DEBUG:
    log.info(u'running in beta')
    STAGE = Stage(Stage.BETA)
else:
    log.info(u'running in prod')
    STAGE = Stage(Stage.PROD)
