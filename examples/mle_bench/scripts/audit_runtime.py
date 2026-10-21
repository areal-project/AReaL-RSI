#!/usr/bin/env python3
"""Offline functional probes, each isolated in a subprocess (no model downloads)."""
import argparse
from datetime import datetime, timezone
import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path


def torch_gpu():
    import torch
    assert torch.cuda.is_available() and torch.cuda.device_count() == 1
    layer = torch.nn.Linear(16, 4).cuda()
    x = torch.randn(8, 16, device='cuda')
    with torch.autocast('cuda', dtype=torch.bfloat16):
        loss = layer(x).float().square().mean()
    loss.backward()
    assert torch.isfinite(layer.weight.grad).all()
    torch.cuda.synchronize()
    return dict(gpu=torch.cuda.get_device_name(0), capability=torch.cuda.get_device_capability(0),
                torch=torch.__version__, cuda=torch.version.cuda, bf16_backward=True)


def vision_gpu():
    import torch
    import torchvision
    net = torchvision.models.resnet18(weights=None).cuda().train()
    net(torch.randn(2, 3, 64, 64, device='cuda')).square().mean().backward()
    boxes = torch.tensor([[0., 0., 2., 2.], [0., 0., 1., 1.]], device='cuda')
    kept = torchvision.ops.nms(boxes, torch.tensor([.9, .7], device='cuda'), .5)
    assert len(kept) > 0
    return dict(resnet18_backward=True, torchvision_nms_cuda=True)


def timm_gpu():
    import torch
    import timm
    net = timm.create_model('vit_tiny_patch16_224', pretrained=False, img_size=32, num_classes=3).cuda()
    net(torch.randn(2, 3, 32, 32, device='cuda')).square().mean().backward()
    return dict(vit_backward=True)


def segmentation_gpu():
    import torch
    import segmentation_models_pytorch as smp
    net = smp.Unet(encoder_name='resnet18', encoder_weights=None, classes=1).cuda()
    net(torch.randn(2, 3, 64, 64, device='cuda')).square().mean().backward()
    return dict(unet_backward=True)


def nlp_gpu():
    import torch
    from transformers import BertConfig, BertForSequenceClassification
    config = BertConfig(vocab_size=128, hidden_size=32, num_hidden_layers=1,
                        num_attention_heads=2, intermediate_size=64, num_labels=2)
    net = BertForSequenceClassification(config).cuda()
    output = net(input_ids=torch.randint(0, 128, (2, 16), device='cuda'),
                 labels=torch.tensor([0, 1], device='cuda'))
    output.loss.backward()
    assert torch.isfinite(output.loss)
    return dict(bert_backward=True, pretrained_weights_used=False)


def peft_gpu():
    import torch
    from transformers import BertConfig, BertForSequenceClassification
    from peft import LoraConfig, get_peft_model
    config = BertConfig(vocab_size=128, hidden_size=32, num_hidden_layers=1,
                        num_attention_heads=2, intermediate_size=64, num_labels=2)
    net = get_peft_model(BertForSequenceClassification(config),
                        LoraConfig(r=2, target_modules=['query', 'value'])).cuda()
    loss = net(input_ids=torch.randint(0, 128, (2, 8), device='cuda'),
               labels=torch.tensor([0, 1], device='cuda')).loss
    loss.backward()
    assert any(p.grad is not None for n, p in net.named_parameters() if 'lora_' in n)
    return dict(lora_backward=True)


def xgboost_gpu():
    import json
    import numpy as np
    import xgboost as xgb
    x = np.random.default_rng(0).normal(size=(128, 6)).astype('float32')
    y = (x[:, 0] > 0).astype('float32')
    data = xgb.DMatrix(x, label=y)
    model = xgb.train(dict(tree_method='hist', device='cuda', max_depth=2,
                           objective='binary:logistic', nthread=2), data, num_boost_round=3)
    device = json.loads(model.save_config())['learner']['generic_param']['device']
    assert device.startswith('cuda'), f'Unexpected CPU fallback: {device}'
    assert np.isfinite(model.predict(data)).all()
    return dict(device=device, training=True)


def catboost_gpu():
    import numpy as np
    from catboost import CatBoostClassifier
    x = np.random.default_rng(0).normal(size=(128, 6)).astype('float32')
    model = CatBoostClassifier(iterations=3, depth=2, task_type='GPU', devices='0',
                               verbose=False, allow_writing_files=False)
    model.fit(x, (x[:, 0] > 0).astype(int))
    assert np.isfinite(model.predict_proba(x)).all()
    return dict(training=True, backend='GPU')


def tabular_cpu():
    import numpy as np
    import lightgbm as lgb
    from sklearn.ensemble import RandomForestClassifier
    x = np.random.default_rng(0).normal(size=(128, 6))
    y = (x[:, 0] > 0).astype(int)
    forest = RandomForestClassifier(n_estimators=3, n_jobs=2, random_state=0).fit(x, y)
    model = lgb.LGBMClassifier(n_estimators=3, n_jobs=2, verbosity=-1).fit(x, y)
    assert len(forest.predict(x)) == len(model.predict(x)) == 128
    return dict(sklearn_training=True, lightgbm_cpu_training=True)


def tensorflow_cpu():
    import tensorflow as tf
    tf.config.set_visible_devices([], 'GPU')
    x = tf.Variable(tf.ones([2, 4]))
    with tf.GradientTape() as tape:
        loss = tf.reduce_sum(tf.square(x))
    assert tape.gradient(loss, x) is not None
    return dict(version=tf.__version__, backward=True)


def tensorflow_gpu():
    import tensorflow as tf
    gpus = tf.config.list_physical_devices('GPU')
    assert gpus, 'TensorFlow detects no usable GPU'
    for gpu in gpus:
        tf.config.experimental.set_memory_growth(gpu, True)
    tf.config.set_soft_device_placement(False)
    with tf.device('/GPU:0'):
        x = tf.Variable(tf.random.normal([2, 16, 16, 3]))
        kernel = tf.Variable(tf.random.normal([3, 3, 3, 4]))
        with tf.GradientTape() as tape:
            loss = tf.reduce_mean(tf.square(tf.nn.conv2d(x, kernel, 1, 'SAME')))
        gradients = tape.gradient(loss, [x, kernel])
        assert all(gradient is not None for gradient in gradients)
        assert 'GPU' in loss.device
        value = float(loss.numpy())
    return dict(version=tf.__version__, conv2d_backward=True, loss=value)


def audio():
    import numpy as np
    import torch
    import torchaudio
    import librosa
    import soundfile as sf
    import tempfile
    wave = np.sin(2 * np.pi * 440 * np.arange(8000) / 16000).astype('float32')
    mel = librosa.feature.melspectrogram(y=wave, sr=16000, n_fft=512)
    assert np.isfinite(mel).all()
    tensor = torch.tensor(wave, device='cuda')
    spectrum = torchaudio.transforms.MelSpectrogram(sample_rate=16000, n_fft=512).cuda()(tensor)
    assert torch.isfinite(spectrum).all()
    with tempfile.TemporaryDirectory() as directory:
        filename = Path(directory) / 'audio.wav'
        sf.write(filename, wave, 16000)
        restored, rate = torchaudio.load(str(filename))
        assert rate == 16000 and restored.shape[-1] == len(wave)
    return dict(librosa_mel=True, torchaudio_cuda_fft=True, wav_decode=True)


def formats():
    import tempfile
    import numpy as np
    import pandas as pd
    import imagecodecs
    import h5py
    import scipy.io
    from PIL import Image
    from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
    from pydicom.uid import ExplicitVRLittleEndian, generate_uid
    import pydicom
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        data = pd.DataFrame({'a': [1., 2.], 'b': ['x', 'y']})
        for engine in ['pyarrow', 'fastparquet']:
            path = root / f'{engine}.parquet'
            data.to_parquet(path, engine=engine)
            assert data.equals(pd.read_parquet(path, engine=engine))
        pixels = np.arange(256, dtype='uint8').reshape(16, 16)
        Image.fromarray(pixels).save(root / 'image.png')
        assert np.array_equal(np.array(Image.open(root / 'image.png')), pixels)
        assert np.array_equal(imagecodecs.jpeg2k_decode(imagecodecs.jpeg2k_encode(pixels)), pixels)
        with h5py.File(root / 'sample.h5', 'w') as file:
            file['pixels'] = pixels
        with h5py.File(root / 'sample.h5') as file:
            assert np.array_equal(file['pixels'][:], pixels)
        scipy.io.savemat(root / 'sample.mat', {'pixels': pixels})
        assert np.array_equal(scipy.io.loadmat(root / 'sample.mat')['pixels'], pixels)
        meta = FileMetaDataset()
        meta.TransferSyntaxUID = ExplicitVRLittleEndian
        meta.MediaStorageSOPClassUID = generate_uid()
        meta.MediaStorageSOPInstanceUID = generate_uid()
        ds = FileDataset(str(root / 'sample.dcm'), {}, file_meta=meta, preamble=b'\0' * 128)
        ds.is_little_endian = True
        ds.is_implicit_VR = False
        ds.Rows, ds.Columns = pixels.shape
        ds.SamplesPerPixel, ds.PhotometricInterpretation = 1, 'MONOCHROME2'
        ds.BitsAllocated, ds.BitsStored, ds.HighBit, ds.PixelRepresentation = 8, 8, 7, 0
        ds.PixelData = pixels.tobytes()
        ds.save_as(root / 'sample.dcm', write_like_original=False)
        assert np.array_equal(pydicom.dcmread(root / 'sample.dcm').pixel_array, pixels)
    return dict(parquet_pyarrow=True, parquet_fastparquet=True, png=True, jpeg2000=True,
                hdf5=True, matlab=True, dicom_uncompressed=True)


CASES = {fn.__name__: fn for fn in [torch_gpu, vision_gpu, timm_gpu, segmentation_gpu,
    nlp_gpu, peft_gpu, xgboost_gpu, catboost_gpu, tabular_cpu, tensorflow_cpu,
    tensorflow_gpu, audio, formats]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--case', choices=CASES)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.case:
        print(json.dumps(CASES[args.case](), default=str))
        return
    output = args.output or Path('/workspace/runtime-audit.json')
    log_dir = output.parent / 'runtime-logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
           'HF_DATASETS_OFFLINE': '1', 'NO_ALBUMENTATIONS_UPDATE': '1',
           'TF_FORCE_GPU_ALLOW_GROWTH': 'true', 'TF_NUM_INTRAOP_THREADS': '2',
           'TF_NUM_INTEROP_THREADS': '1', 'OMP_NUM_THREADS': '2'}
    results = {}
    for name in CASES:
        start = time.monotonic()
        try:
            process = subprocess.run([sys.executable, __file__, '--case', name], env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, timeout=120)
            (log_dir / f'{name}.log').write_text(process.stdout)
            results[name] = dict(status='passed' if process.returncode == 0 else 'failed',
                                 exit_code=process.returncode, seconds=round(time.monotonic()-start, 2),
                                 log=f'runtime-logs/{name}.log')
            if process.returncode == 0:
                results[name]['metrics'] = json.loads(process.stdout.strip().splitlines()[-1])
        except Exception as error:
            (log_dir / f'{name}.log').write_text(traceback.format_exc())
            results[name] = dict(status='error', error=str(error), seconds=round(time.monotonic()-start, 2))
        print(name, results[name]['status'], flush=True)
        output.write_text(json.dumps(dict(created_at=datetime.now(timezone.utc).isoformat(),
                                         slurm_job=os.environ.get('SLURM_JOB_ID'),
                                         scope='Synthetic framework probes; no task data or pretrained weights.',
                                         cases=results), indent=2) + '\n')
    raise SystemExit(int(any(result['status'] != 'passed' for result in results.values())))


if __name__ == '__main__':
    main()
