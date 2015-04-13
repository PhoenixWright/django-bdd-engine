
class Task(object):
    """
    Generic task with an update function for the engine to call each loop.
    """

    def __init__(self):
        self.done = False

    def update(self):
        """
        To be implemented by tasks.
        """
        pass
