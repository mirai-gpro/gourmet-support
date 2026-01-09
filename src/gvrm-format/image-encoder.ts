// image-encoder.ts
// DINOv2 Image Encoder for GUAVA

import { AutoProcessor, AutoModel, RawImage } from '@huggingface/transformers';

export class ImageEncoder {
  private model: any = null;
  private processor: any = null;
  private initialized = false;

  /**
   * 初期化：DINOv2モデルをロード
   */
  async init(): Promise<void> {
    if (this.initialized) return;

    console.log('[ImageEncoder] Initializing DINOv2...');

    try {
      // DINOv2-base モデルをロード
      // Note: quantized版を使用して軽量化
      const modelId = 'onnx-community/dinov2-base';

      console.log('[ImageEncoder] Loading processor...');
      this.processor = await AutoProcessor.from_pretrained(modelId);

      console.log('[ImageEncoder] Loading model...');
      this.model = await AutoModel.from_pretrained(modelId, {
        quantized: true // 軽量版を使用
      });

      this.initialized = true;
      console.log('[ImageEncoder] ✅ DINOv2 initialized');
    } catch (error) {
      console.error('[ImageEncoder] ❌ Failed to initialize:', error);
      throw new Error(`Image Encoder initialization failed: ${error}`);
    }
  }

  /**
   * 画像からprojection_featureを抽出
   * 
   * @param imageUrl - source.pngのURL
   * @param targetVertexCount - Template Decoderの頂点数（10595）
   * @returns projection_feature [N, 128]
   */
  async extractFeatures(
    imageUrl: string,
    targetVertexCount: number = 10595
  ): Promise<Float32Array> {
    if (!this.model || !this.processor) {
      throw new Error('[ImageEncoder] Not initialized. Call init() first.');
    }

    console.log('[ImageEncoder] Processing image:', imageUrl);

    try {
      const startTime = performance.now();

      // 1. 画像を読み込み
      const image = await RawImage.fromURL(imageUrl);
      console.log('[ImageEncoder] Image loaded:', {
        width: image.width,
        height: image.height
      });

      // 2. 前処理
      const inputs = await this.processor(image);

      // 3. DINOv2で特徴抽出
      const { last_hidden_state } = await this.model(inputs);

      // 4. 特徴量を取得
      // last_hidden_state: [1, num_patches+1, 768]
      // CLS tokenを除外: [1, num_patches, 768]
      const features = last_hidden_state.slice(null, [1, null], null);

      // 5. 次元削減: 768 → 128
      // 簡易版: 最初の128次元を使用
      // TODO: より高度な方法（PCA、学習済み射影層）
      const featuresData = features.data as Float32Array;
      const numPatches = features.dims[1];
      const featureDim = features.dims[2]; // 768

      console.log('[ImageEncoder] DINOv2 output:', {
        numPatches,
        featureDim,
        targetVertexCount
      });

      // 6. targetVertexCount x 128 の配列を生成
      const projectionFeature = new Float32Array(targetVertexCount * 128);

      // パッチ特徴を頂点にマッピング（最近傍補間）
      for (let i = 0; i < targetVertexCount; i++) {
        // 頂点インデックスをパッチインデックスにマッピング
        const patchIdx = Math.floor((i / targetVertexCount) * numPatches);

        // 768次元から最初の128次元を抽出
        for (let j = 0; j < 128; j++) {
          const srcIdx = patchIdx * featureDim + j;
          const dstIdx = i * 128 + j;
          projectionFeature[dstIdx] = featuresData[srcIdx];
        }
      }

      const elapsed = performance.now() - startTime;
      console.log(`[ImageEncoder] ✅ Feature extraction completed in ${elapsed.toFixed(2)}ms`);

      // 統計情報
      const sample = Array.from(projectionFeature.slice(0, 128));
      console.log('[ImageEncoder] Feature statistics:', {
        min: Math.min(...sample),
        max: Math.max(...sample),
        avg: sample.reduce((a, b) => a + b, 0) / sample.length
      });

      return projectionFeature;

    } catch (error) {
      console.error('[ImageEncoder] ❌ Feature extraction failed:', error);
      throw error;
    }
  }

  /**
   * リソースを解放
   */
  dispose(): void {
    if (this.model) {
      this.model.dispose?.();
      this.model = null;
      this.processor = null;
      this.initialized = false;
      console.log('[ImageEncoder] Disposed');
    }
  }
}