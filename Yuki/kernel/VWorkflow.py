"""
Construction of a workflow with the jobs, especially from the task
"""
class VWorkflow(object):

    def __init__(self, job):
        """ Initialize it with ?
        """
        self.yaml_file = 
        pass

    def construct(self, job):
        self.writeline("version 0.1.0")

    def writeline(self, line):
        pass

    def run(self):
        self.construct()
        self.write_to()
        self.upload()
        self.run()
        self.check_status()
        self.download()
