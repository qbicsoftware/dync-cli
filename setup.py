from setuptools import setup

setup(
    name='qsync',
    version='0.1dev',
    packages=['qsync'],
    license='GPL2+',
    long_description=open('README.md').read(),
    install_requires=['zmq'],
    entry_points={
        'console_scripts': [
            'qsync = qsync.client:main',
            'qsync-server = qsync.server:main'
        ]
    }
)
