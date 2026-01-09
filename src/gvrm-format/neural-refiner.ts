// neural-refiner.ts
// Web / iOS / onnxruntime-web(wasm) 安定版 Neural Refiner (修正版)

// ✅ WASM専用インポート（template-decoderと統一）
import * as ort from 'onnxruntime-web/wasm';

export class NeuralRefiner {
  private session: ort.InferenceSession | null = null;
  private initialized = false;

  private readonly MODEL_PATH = '/assets/refiner_websafe_v1.onnx';
  private readonly FM_CHANNELS = 32;
  private readonly FM_SIZE = 256;

  async init(): Promise<void> {
    if (this.initialized) return;

    console.log('[NeuralRefiner] Initializing (WASM direct paths)...');

    try {
      // ✅ iOS安定版設定（template-decoderと統一）
      ort.env.wasm.numThreads = 1;
      ort.env.wasm.simd = true;
      ort.env.wasm.proxy = false;
      
      // ✅ WASMファイルを直接指定（.mjsの読み込みを回避）
      ort.env.wasm.wasmPaths = {
        'ort-wasm-simd-threaded.wasm': 'https://cdn.jsdelivr.net/npm/onnxruntime-web@1.17.3/dist/ort-wasm-simd-threaded.wasm',
        'ort-wasm-simd.wasm': 'https://cdn.jsdelivr.net/npm/onnxruntime-web@1.17.3/dist/ort-wasm-simd.wasm',
        'ort-wasm-threaded.wasm': 'https://cdn.jsdelivr.net/npm/onnxruntime-web@1.17.3/dist/ort-wasm-threaded.wasm',
        'ort-wasm.wasm': 'https://cdn.jsdelivr.net/npm/onnxruntime-web@1.17.3/dist/ort-wasm.wasm'
      };

      console.log('[NeuralRefiner] ONNX Runtime v1.17.3 configured');

      // セッション生成
      this.session = await ort.InferenceSession.create(
        this.MODEL_PATH,
        {
          executionProviders: ['wasm'],
          graphOptimizationLevel: 'all'
        }
      );

      this.initialized = true;
      console.log('[NeuralRefiner] ✅ Model loaded successfully');
    } catch (error) {
      console.error('[NeuralRefiner] ❌ Failed to load model:', error);
      throw new Error(`Neural Refiner initialization failed: ${error}`);
    }
  }

  /**
   * 新API（本体）
   * @param coarseFM Float32Array [1,32,256,256] 正規化済み
   * @param idEmb    Float32Array [1,256]
   */
  async run(
    coarseFM: Float32Array,
    idEmb: Float32Array
  ): Promise<Float32Array> {

    if (!this.session) {
      throw new Error('NeuralRefiner not initialized');
    }

    // 入力サイズ検証
    const expectedFMSize = this.FM_CHANNELS * this.FM_SIZE * this.FM_SIZE;

    if (coarseFM.length !== expectedFMSize) {
      throw new Error(
        `Invalid coarseFM size: ${coarseFM.length}, expected: ${expectedFMSize}`
      );
    }

    if (idEmb.length !== 256) {
      throw new Error(
        `Invalid idEmb size: ${idEmb.length}, expected: 256`
      );
    }

    // Tensor作成（float32固定）
    const fmTensor = new ort.Tensor(
      'float32',
      coarseFM,
      [1, 32, 256, 256]
    );

    const idTensor = new ort.Tensor(
      'float32',
      idEmb,
      [1, 256]
    );

    // 推論実行
    const outputs = await this.session.run({
      coarse_fm: fmTensor,
      id_emb: idTensor
    });

    const rawOutput = outputs.refined_rgb.data as Float32Array;
    
    console.log('[NeuralRefiner] Raw output info:', {
      length: rawOutput.length,
      dims: outputs.refined_rgb.dims,
      type: outputs.refined_rgb.type
    });

    // ✅ 出力形状を確認
    const dims = outputs.refined_rgb.dims;
    const expectedLength = 512 * 512 * 3; // [1, 3, 512, 512] or [1, 512, 512, 3]
    
    if (rawOutput.length !== expectedLength) {
      console.error('[NeuralRefiner] Unexpected output length!', {
        actual: rawOutput.length,
        expected: expectedLength,
        dims: dims
      });
    }

    // ✅ チャンネル順序を確認（モデルがCHW形式の場合）
    // dims = [1, 3, 512, 512] なら CHW形式
    // dims = [1, 512, 512, 3] なら HWC形式
    
    let out: Float32Array;
    const H = 512;
    const W = 512;
    const C = 3;
    
    if (dims.length === 4 && dims[1] === 3) {
      // CHW形式 → HWC形式に変換
      console.log('[NeuralRefiner] Converting CHW to HWC...');
      out = new Float32Array(H * W * C);
      
      for (let h = 0; h < H; h++) {
        for (let w = 0; w < W; w++) {
          for (let c = 0; c < C; c++) {
            const srcIdx = c * H * W + h * W + w; // CHW
            const dstIdx = h * W * C + w * C + c; // HWC
            out[dstIdx] = rawOutput[srcIdx];
          }
        }
      }
    } else {
      // 既にHWC形式の場合
      out = rawOutput;
    }

    // sanity check
    let min = Infinity;
    let max = -Infinity;
    let nonZero = 0;
    let nanCount = 0;
    let infCount = 0;

    for (let i = 0; i < out.length; i++) {
      const v = out[i];
      if (isNaN(v)) {
        nanCount++;
        continue;
      }
      if (!isFinite(v)) {
        infCount++;
        continue;
      }
      if (v !== 0) nonZero++;
      if (v < min) min = v;
      if (v > max) max = v;
    }

    console.log('[NeuralRefiner] Output stats (raw):', {
      min,
      max,
      nonZeroCount: nonZero,
      nanCount,
      infCount,
      length: out.length,
      sampleValues: Array.from(out.slice(0, 30)).map(v => v.toFixed(4))
    });

    // NaN/Infを0に置換
    if (nanCount > 0 || infCount > 0) {
      console.warn('[NeuralRefiner] Cleaning invalid values...');
      for (let i = 0; i < out.length; i++) {
        if (!isFinite(out[i])) {
          out[i] = 0;
        }
      }
    }

    // ✅ 正規化方法を改善
    // モデル出力が [-1, 1] や他の範囲の可能性があるため
    const range = max - min;
    
    if (range < 0.001) {
      // 全て同じ値の場合（モデルが正常に動作していない）
      console.error('[NeuralRefiner] ❌ Output range too small! Model may have failed.');
      for (let i = 0; i < out.length; i++) {
        out[i] = 0.5; // グレーで埋める
      }
    } else if (min >= 0 && max <= 1) {
      // 既に [0, 1] の範囲内
      console.log('[NeuralRefiner] Already in [0, 1] range, no normalization needed');
    } else if (min >= -1 && max <= 1) {
      // [-1, 1] → [0, 1] に変換
      console.log('[NeuralRefiner] Converting from [-1, 1] to [0, 1]...');
      for (let i = 0; i < out.length; i++) {
        out[i] = (out[i] + 1) / 2;
      }
    } else {
      // 一般的なmin-max正規化
      console.log('[NeuralRefiner] Applying min-max normalization...');
      for (let i = 0; i < out.length; i++) {
        out[i] = (out[i] - min) / range;
      }
    }

    console.log('[NeuralRefiner] ✅ Normalization complete');

    return out;
  }

  /**
   * ★ 後方互換API
   * gvrm.tsが呼んでいるprocess()を保持
   */
  async process(
    coarseFM: Float32Array,
    idEmb: Float32Array
  ): Promise<Float32Array> {
    return this.run(coarseFM, idEmb);
  }

  dispose(): void {
    if (this.session) {
      this.session.release();
      this.session = null;
      this.initialized = false;
      console.log('[NeuralRefiner] Session disposed');
    }
  }
}