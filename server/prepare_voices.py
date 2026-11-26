"""Download the open German Piper voices and prepare them for singing (expose/replace the duration node)."""
import os, sys, subprocess, glob, onnx
VOICE_DIR = os.environ.get('CHOR_VOICES', '/opt/voices')
NAMES = ['de_DE-kerstin-low', 'de_DE-ramona-low', 'de_DE-eva_k-x_low', 'de_DE-thorsten-high', 'de_DE-karlsson-low', 'de_DE-pavoque-low']
URL = 'https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/vits-piper-{}.tar.bz2'
os.makedirs(VOICE_DIR, exist_ok=True)
for n in NAMES:
    d = os.path.join(VOICE_DIR, 'vits-piper-' + n)
    if not os.path.isdir(d):
        subprocess.run(f'curl -sL {URL.format(n)} | tar xj -C {VOICE_DIR}', shell=True, check=True)
    f = os.path.join(d, n + '.onnx')
    if not os.path.exists(f.replace('.onnx', '.sing.onnx')):
        a = onnx.load(f)
        a.graph.output.append(onnx.helper.make_tensor_value_info('/Ceil_output_0', onnx.TensorProto.FLOAT, None))
        onnx.save(a, f.replace('.onnx', '.dur.onnx'))
        b = onnx.load(f)
        b.graph.node.remove([x for x in b.graph.node if x.op_type == 'Ceil'][0])
        b.graph.input.append(onnx.helper.make_tensor_value_info('/Ceil_output_0', onnx.TensorProto.FLOAT, [1, 1, None]))
        onnx.save(b, f.replace('.onnx', '.sing.onnx'))
    print('ok', n)
