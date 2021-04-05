import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name="locust_influxdb2_listener", # Replace with your own username
    version="0.0.1",
    author="Pablo Calvo",
    author_email="pjcalvov@gmail.com",
    edited_by="Samolevar"
    description="Locust.io 2.X influxdb listener",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/Samolevar/locust-influxdb-listener",
    packages=setuptools.find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    install_requires=[
        'locust>=1.1.1',
        'influxdb-client>=1.15.0'
    ],
    python_requires='>=3.6',
)
