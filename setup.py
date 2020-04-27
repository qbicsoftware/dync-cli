from setuptools import setup

setup(
    name='dync',
    version='1.1',
    packages=['dync'],
    license='GPL2+',
    long_description=open('README.md').read(),
    install_requires=['pyzmq', 'pyyaml'],
    entry_points={
        'console_scripts': [
            'dync = dync.client:main',
            'dync-server = dync.server:main'
        ]
    }
)
