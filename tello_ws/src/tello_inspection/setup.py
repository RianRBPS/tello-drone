from setuptools import setup

package_name = 'tello_inspection'

setup(
    name=package_name,
    version='0.1.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='riris',
    maintainer_email='rianrbps@gmail.com',
    description='Tello indoor inspection: capture, mosaic, defect detection',
    license='MIT',
    entry_points={
        'console_scripts': [
            'tello_inspection = tello_inspection.node:main',
        ],
    },
)
