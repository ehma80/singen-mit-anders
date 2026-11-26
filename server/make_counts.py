"""Spoken count-in words ("eins" … "acht") for the entry counter, shared by all songs: <site>/count/<n>.mp3"""
import os, sys, wave, io, numpy as np
from piper import PiperVoice, SynthesisConfig
import app

WORDS = ['eins', 'zwei', 'drei', 'vier', 'fünf', 'sechs', 'sieben', 'acht']


def make(site, voice='de_DE-thorsten-high'):
    d = os.path.join(site, 'count'); os.makedirs(d, exist_ok=True)
    base = os.path.join(os.environ.get('CHOR_VOICES', '/opt/voices'), f'vits-piper-{voice}', voice)
    v = PiperVoice.load(base + '.onnx', config_path=base + '.onnx.json')
    for i, w in enumerate(WORDS, 1):
        buf = io.BytesIO()
        with wave.open(buf, 'wb') as wf:
            v.synthesize_wav(w + '!', wf, syn_config=SynthesisConfig(length_scale=0.72))
        buf.seek(0)
        with wave.open(buf) as wf:
            sr = wf.getframerate(); y = np.frombuffer(wf.readframes(wf.getnframes()), np.int16).astype(np.float32) / 32768
        # trim silence so the word starts right on the beat
        idx = np.where(np.abs(y) > 0.02)[0]
        y = y[max(0, idx[0] - int(0.005 * sr)): idx[-1] + int(0.03 * sr)]
        app._encode(y, os.path.join(d, f'{i}.mp3'), sr)


if __name__ == '__main__':
    make(sys.argv[1])
