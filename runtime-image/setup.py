from setuptools import setup, find_packages

setup(
    name="llm_port_ray_runtime",
    version="0.2.0",
    license="Apache-2.0",
    license_files=["LICENSE", "NOTICE"],
    packages=find_packages(),
    entry_points={
        "console_scripts": [
            "llm-port-ray-runtime=llm_port_ray_runtime.cli:main",
        ],
    },
    install_requires=[
        "pydantic",
    ],
)

