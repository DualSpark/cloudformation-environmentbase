# -*- encoding: utf8 -*-
import glob
import io
import re
from os.path import basename
from os.path import dirname
from os.path import join
from os.path import splitext

from setuptools import find_packages
from setuptools import setup

execfile('src/environmentbase/version.py')

def read(*names, **kwargs):
    return io.open(
        join(dirname(__file__), *names),
        encoding=kwargs.get("encoding", "utf8")
    ).read()

setup(
    name="cfn-environment-base",
    version=__version__,
    license="ISC",
    description="Base environment for Troposphere based CFN project environments",
    long_description="%s" % read("README.md"),
    author="Patrick McClory",
    author_email="patrick@dualspark.com",
    packages=find_packages("src"),
    package_dir={"": "src"},
    py_modules=[splitext(basename(i))[0] for i in glob.glob("src/**/*.py")],
    include_package_data=True,
    zip_safe=False,
    test_suite='nose2.collector.collector',
    tests_require=[
        'nose2',
        'unittest2',
        'mock'
    ],
    classifiers=[
        # complete classifier list: http://pypi.python.org/pypi?%3Aaction=list_classifiers
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: ISC License",
        "Operating System :: Unix",
        "Operating System :: POSIX",
        "Operating System :: Microsoft :: Windows",
        "Programming Language :: Python",
        "Programming Language :: Python :: 2.6",
        "Programming Language :: Python :: 2.7",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.3",
        "Programming Language :: Python :: 3.4",
        "Programming Language :: Python :: Implementation :: CPython",
        "Programming Language :: Python :: Implementation :: PyPy",
        "Topic :: Utilities",
        "Topic :: Software Development :: Code Generators"
    ],
    keywords=[
        # eg: "keyword1", "keyword2", "keyword3",
    ],
    install_requires=[
        "troposphere==1.0.0",
        "boto>=2.38.0",
        "ipcalc==1.1.2",
        "docopt==0.6.2"
    ],
    extras_require={
        # eg: 'rst': ["docutils>=0.11"],
    },
    entry_points={
        "console_scripts": [
            "tests=tests.test_environmentbase:main"
            "environmentutil = environmentutil.environmentutil:main",
            "awsbootstrap = environmentbase.accountbootstrap:main"
        ]
    }

)
