import setuptools

with open("README.md", "r") as f:
    long_description = f.read()

with open("requirements.txt", 'r') as f:
    requirements = [line.strip() for line in f if line.strip() and not line.startswith("#")]


setuptools.setup(
    name="photoframe",
    version="0.0.1",
    author="MrAvalanche",
    description="DIY eINK PhotoFrame library",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/mravalanche/photoframe",
    packages=setuptools.find_packages(),
    install_requires=requirements
)
