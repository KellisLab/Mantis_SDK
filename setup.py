from setuptools import setup, find_packages

setup(
    name="Mantis_SDK",
    version="1.0.0",
    description="SDK for interacting with the Mantis API",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    author="Sufian A",
    author_email="",
    url="https://github.com/KellisLab/Mantis_SDK",
    packages=find_packages(),
    install_requires=["requests"],
    python_requires=">=3.7",
)
