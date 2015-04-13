from setuptools import setup, find_packages

args = dict(
    name='Django-bdd-engine',
    version='1.0',
    packages=find_packages(exclude=("test",)),
)

setup(**args)
