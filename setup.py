# from distutils import sys
import os

from setuptools import setup, find_packages

PACKAGE = "Yuki"
NAME = "Yuki"
DESCRIPTION = "The Data Integration Thought Entity for Chern toolkit"
AUTHOR = "Mingrui Zhao"
AUTHOR_EMAIL = "mingrui.zhao@mail.labz0.org"
URL = "https://github.com/hepChern/Yuki"
VERSION = __import__(PACKAGE).__version__
here = os.path.dirname(__file__)
setup(
    name = NAME,
    version = VERSION,
    description = DESCRIPTION,
    author=AUTHOR,
    author_email=AUTHOR_EMAIL,
    license="Apache License, Version 2.0",
    url = URL,
    classifiers = [
        'Development Status :: 3 - Alpha',

        'Intended Audience :: Science/Research',
        'Topic :: Scientific/Engineering :: Physics',

        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: Implementation :: CPython',

    ],
    zip_safe=False,
    keywords = "Analysis Perservation",
    packages = find_packages(exclude=[]),
    install_requires = [
        "Chern==4", "click", "colored", "python-daemon", "ipython", "PyYAML>=5.1,<6.0", "reana-client", "flask", "celery"
    ],
    include_package_data = True,
    entry_points = {
        'console_scripts': [
            'yuki = Yuki.main:main',
            'yukirunner = Yuki.main:main'
        ]
    }
)

# Python 3.10.12
# Flask 2.2.2
# Werkzeug 2.2.2
# 5.2.7
