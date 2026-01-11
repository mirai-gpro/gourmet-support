#!/usr/bin/env python3
"""
DINOv2-baseを518×518入力用にONNX変換
出力: dinov2_518.onnx

使用方法:
  python export_dinov2_518.py
"""

import torch
from transformers import AutoModel
import onnx
import onnxruntime as ort
import numpy as np
import os

print("=" * 60)
print("DINOv2 ONNX Export (518×518 input)")
print("=" * 60)

# 1. モデルロード
print("\n1. Loading DINOv2-base model...")
model = AutoModel.from_pretrained("facebook/dinov2-base")
model.eval()
print("   ✅ Model loaded")

# 2. ダミー入力作成
print("\n2. Creating dummy input...")
dummy_input = torch.randn(1, 3, 518, 518, dtype=torch.float32)
print(f"   Dummy input shape: {dummy_input.shape}")

# 3. PyTorchモデルでテスト
print("\n3. Testing with PyTorch...")
with torch.no_grad():
    pytorch_output = model(dummy_input)

hidden_state = pytorch_output.last_hidden_state
print(f"   PyTorch output shape: {hidden_state.shape}")
print(f"   Expected: [1, 1370, 768] (1 CLS + 37×37 patches)")

if hidden_state.shape[1] == 1370:
    print("   ✅ PyTorch test passed")
else:
    print(f"   ❌ Unexpected output shape: {hidden_state.shape}")
    exit(1)

# 4. ONNX変換
print("\n4. Converting to ONNX...")
onnx_path = "dinov2_518.onnx"

torch.onnx.export(
    model,
    dummy_input,
    onnx_path,
    export_params=True,
    opset_version=17,
    do_constant_folding=True,
    input_names=['pixel_values'],
    output_names=['last_hidden_state'],
    dynamic_axes={
        'pixel_values': {0: 'batch'},
        'last_hidden_state': {0: 'batch'}
    },
    verbose=False
)

# ファイルサイズ確認
size_mb = os.path.getsize(onnx_path) / 1024 / 1024
print(f"   ✅ ONNX exported: {onnx_path} ({size_mb:.2f} MB)")

# 5. ONNX検証
print("\n5. Validating ONNX model...")
onnx_model = onnx.load(onnx_path)
onnx.checker.check_model(onnx_model)
print("   ✅ ONNX model is valid")

# 6. ONNX Runtime実行テスト
print("\n6. Testing with ONNX Runtime...")
session = ort.InferenceSession(onnx_path)

# 入力名確認
input_name = session.get_inputs()[0].name
output_name = session.get_outputs()[0].name
print(f"   Input name: {input_name}")
print(f"   Output name: {output_name}")

# 推論実行
onnx_output = session.run(
    [output_name],
    {input_name: dummy_input.numpy()}
)

onnx_hidden_state = onnx_output[0]
print(f"   ONNX Runtime output shape: {onnx_hidden_state.shape}")

# 7. PyTorchとONNXの出力を比較
print("\n7. Comparing PyTorch and ONNX outputs...")
pytorch_numpy = hidden_state.numpy()
max_diff = np.abs(pytorch_numpy - onnx_hidden_state).max()
mean_diff = np.abs(pytorch_numpy - onnx_hidden_state).mean()

print(f"   Max difference: {max_diff:.6f}")
print(f"   Mean difference: {mean_diff:.6f}")

if max_diff < 1e-4:
    print("   ✅ Outputs match (tolerance: 1e-4)")
else:
    print(f"   ⚠️  Difference is larger than expected: {max_diff}")

# 8. 出力形状の詳細確認
print("\n8. Output details:")
print(f"   Total tokens: {onnx_hidden_state.shape[1]}")
print(f"   Feature dimension: {onnx_hidden_state.shape[2]}")
print(f"   CLS token: 1")
print(f"   Patch tokens: {onnx_hidden_state.shape[1] - 1}")
print(f"   Grid size: 37×37")

# 9. 統計情報
print("\n9. Statistics:")
cls_token = onnx_hidden_state[0, 0]
patch_tokens = onnx_hidden_state[0, 1:]

print(f"   CLS token mean: {cls_token.mean():.4f}")
print(f"   CLS token std: {cls_token.std():.4f}")
print(f"   Patch tokens mean: {patch_tokens.mean():.4f}")
print(f"   Patch tokens std: {patch_tokens.std():.4f}")

print("\n" + "=" * 60)
print("ONNX EXPORT COMPLETED SUCCESSFULLY")
print("=" * 60)
print(f"\nGenerated file: {onnx_path} ({size_mb:.2f} MB)")
print("\nNext steps:")
print("  1. cp dinov2_518.onnx public/assets/")
print("  2. Update image-encoder.ts to use this ONNX model")
print("  3. Verify 37×37 patch output in browser")
