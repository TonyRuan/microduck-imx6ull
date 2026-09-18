"""Export all supported policies and preserve their ONNX reference for Mac parity."""
import argparse
import json
from pathlib import Path
import shutil

from export import export
from policy_bundle import MODEL_NAMES, checked_bundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models', type=Path, default=Path.home()/'.cache/duck-sim/policies/current')
    parser.add_argument('--out', type=Path, default=Path(__file__).parent/'out/policies')
    args = parser.parse_args()
    index = {'format': 1, 'models': {}}
    for name in MODEL_NAMES:
        source = args.models/(name+'.onnx')
        folder = args.out/name
        export(source, folder)
        shutil.copyfile(source, args.out/(name+'.onnx'))
        shutil.copyfile(folder/'weights.bin', args.out/(name+'.duckmlp'))
        index['models'][name] = json.loads((folder/'model.json').read_text())
    (args.out/'index.json').write_text(json.dumps(index, indent=2)+'\n')
    checked_bundle(args.out)
    print('READY:', len(MODEL_NAMES), 'models in', args.out)


if __name__ == '__main__': main()
