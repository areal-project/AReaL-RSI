#!/usr/bin/env python3
"""Record TensorFlow build requirements and loader failures on the allocated GPU."""
import ctypes
import importlib.metadata
import json
import os
from pathlib import Path

os.environ['TF_CPP_MIN_LOG_LEVEL'] = '0'
import tensorflow as tf

report = dict(build=dict(tf.sysconfig.get_build_info()),
              devices=[str(x) for x in tf.config.list_physical_devices()],
              gpu_devices=[str(x) for x in tf.config.list_physical_devices('GPU')],
              libraries={}, installed_nvidia={})
for name in ['libcuda.so.1', 'libcudart.so.12', 'libcublas.so.12', 'libcublasLt.so.12',
             'libcufft.so.11', 'libcurand.so.10', 'libcusolver.so.11', 'libcusparse.so.12',
             'libcudnn.so.8', 'libcudnn.so.9']:
    try:
        ctypes.CDLL(name)
        report['libraries'][name] = 'loaded'
    except OSError as error:
        report['libraries'][name] = str(error)
for dist in importlib.metadata.distributions():
    name = dist.metadata.get('Name', '')
    if name.startswith('nvidia-'):
        report['installed_nvidia'][name] = dist.version
Path('/workspace/tensorflow-diagnostic.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2), flush=True)
