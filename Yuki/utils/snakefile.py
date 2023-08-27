import os

class SnakeFile(object):
    """ Helper class to write a snakefile
    """

    def __init__(self, file_path):
        """ Initialize the class use a path
        create a file if it is not initially exists
        """
        self.file_path = file_path
        self.contents = ""

    def addline(self, string, index):
        self.contents += (" "*index*4 + string + "\n")

    def write(self):
        f = open(self.file_path, "w")
        f.write(self.contents)
        f.close()
