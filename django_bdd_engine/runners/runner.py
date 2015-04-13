

class TestRunner(object):

    def __init__(self, engine, **kwargs):
        # the engine has a list of miscellaneous tasks to run, and add_task(task) can be used to add one
        self.engine = engine

    @staticmethod
    def is_compatible(test_runtime_requirements):
        """
        Every test runner must implement this function, which inspects runtime requirements and returns whether or not
        it is capable of running the test.

        Valid things to look for:
        * device_type (phone or tablet)
        * os_type
        * os_version
        * app_uri
        * app_package
        * app_activity

        :param test_runtime_requirements: A collection of requirements for a test.
        :type test_runtime_requirements: dict
        :return: A yes or no on whether the test runner can handle the requirements.
        :rtype: bool
        """
        return False

    def start(self):
        """
        Called by the Engine when the test should start.
        """
        pass
