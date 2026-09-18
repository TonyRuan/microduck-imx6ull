"""Export only the exact feed-forward graph supported by policy.c, with ORT fixtures."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import onnx
import onnxruntime as ort
from onnx import numpy_helper, helper


def export(model_path, out):
    model = onnx.load(model_path)
    onnx.checker.check_model(model)
    graph = model.graph
    assert len(graph.input) == len(graph.output) == 1
    for tensor, name, shape in [(graph.input[0], "obs", [1, 61]), (graph.output[0], "actions", [1, 14])]:
        assert tensor.name == name
        assert tensor.type.tensor_type.elem_type == onnx.TensorProto.FLOAT
        assert [d.dim_value for d in tensor.type.tensor_type.shape.dim] == shape
    weights = {t.name: numpy_helper.to_array(t) for t in graph.initializer}
    expected_ops = ["Sub", "Div", "Gemm", "Elu", "Gemm", "Elu", "Gemm", "Elu", "Gemm"]
    assert [n.op_type for n in graph.node] == expected_ops
    packed, previous, layer = [], "obs", 0
    dims = [61, 512, 256, 128, 14]
    for node in graph.node:
        assert node.domain in ("", "ai.onnx") and len(node.output) == 1
        assert node.input[0] == previous
        attrs = {a.name: helper.get_attribute_value(a) for a in node.attribute}
        if node.op_type in ("Sub", "Div"):
            assert not attrs and len(node.input) == 2
            arr = weights[node.input[1]]
            assert arr.shape == (1, 61)
            if node.op_type == "Div":
                assert np.all(arr > 0)
            packed.append(arr)
        elif node.op_type == "Gemm":
            assert len(node.input) == 3
            assert not (attrs.keys() - {"alpha", "beta", "transA", "transB"})
            assert attrs.get("alpha", 1) == attrs.get("beta", 1) == 1
            assert attrs.get("transA", 0) == 0 and attrs.get("transB", 0) == 1
            w, b = [weights[name] for name in node.input[1:]]
            assert w.shape == (dims[layer+1], dims[layer]) and b.shape == (dims[layer+1],)
            packed.extend([w, b]); layer += 1
        else:
            assert len(node.input) == 1 and not (attrs.keys() - {"alpha"})
            assert attrs.get("alpha", 1) == 1
        previous = node.output[0]
    assert previous == "actions"
    assert all(a.dtype == np.float32 and np.isfinite(a).all() for a in packed)
    out.mkdir(parents=True, exist_ok=True)
    payload = b"DUCKMLP1" + b"".join(a.astype("<f4").tobytes() for a in packed)
    (out / "weights.bin").write_bytes(payload)

    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    session = ort.InferenceSession(str(model_path), options, providers=["CPUExecutionProvider"])
    rng = np.random.default_rng(20260919)
    fixtures = []
    def reference(obs):
        action = session.run(["actions"], {"obs": obs[None, :]})[0][0]
        assert np.isfinite(action).all()
        fixtures.append(np.concatenate([obs, action]))
        return action
    for scale in [0.0, 0.1, 1.0, 5.0]:
        for _ in range(1024):
            reference((rng.standard_normal(61) * scale).astype(np.float32))
    # Synthetic feedback sequence exercises previous-action input, but is not a
    # physics simulation or evidence of stability on a moving robot.
    previous = np.zeros(14, dtype=np.float32)
    for i in range(1024):
        obs = np.zeros(61, dtype=np.float32)
        obs[5] = -1
        obs[34:48] = previous
        obs[48] = .15 * np.sin(i * .013)
        obs[50] = .2 * np.cos(i * .009)
        previous = reference(obs)
    np.asarray(fixtures, dtype="<f4").tofile(out / "fixtures.bin")
    metadata = {
        "model": str(model_path), "model_sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(),
        "weights_sha256": hashlib.sha256(payload).hexdigest(),
        "onnxruntime_reference_version": ort.__version__, "numpy_version": np.__version__,
        "onnx_version": onnx.__version__, "parameters": sum(a.size for a in packed),
        "layer_widths": dims, "dense_macs": sum(a*b for a,b in zip(dims,dims[1:])),
        "validation_cases": len(fixtures), "validation_seed": 20260919,
        "tolerance": "abs_error <= 1e-4 + 1e-4 * abs(reference)",
        "scope": "FP32 graph specialization; no quantization, pruning, or training",
    }
    (out / "model.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("model", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    export(args.model, args.out)
