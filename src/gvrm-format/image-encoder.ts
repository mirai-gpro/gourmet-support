// image-encoder.ts
// DINOv2 Image Encoder for GUAVA
// 修正版: UV座標ベースのサンプリングと適切な次元削減

import { AutoProcessor, AutoModel, RawImage } from '@huggingface/transformers';

export class ImageEncoder {
  private model: any = null;
  private processor: any = null;
  private initialized = false;
  private uvCoord: Float32Array | null = null;

  /**
   * 初期化：DINOv2モデルをロード
   */
  async init(): Promise<void> {
    if (this.initialized) return;

    console.log('[ImageEncoder] Initializing DINOv2...');

    try {
      // DINOv2-base モデルをロード
      const modelId = 'onnx-community/dinov2-base';

      console.log('[ImageEncoder] Loading processor...');
      this.processor = await AutoProcessor.from_pretrained(modelId);

      console.log('[ImageEncoder] Loading model...');
      this.model = await AutoModel.from_pretrained(modelId, {
        quantized: true
      });

      // UV座標をロード
      await this.loadUVCoordinates('/assets');

      this.initialized = true;
      console.log('[ImageEncoder] ✅ DINOv2 initialized');
    } catch (error) {
      console.error('[ImageEncoder] ❌ Failed to initialize:', error);
      throw new Error(`Image Encoder initialization failed: ${error}`);
    }
  }

  /**
   * UV座標データをロード
   */
  private async loadUVCoordinates(basePath: string): Promise<void> {
    try {
      const response = await fetch(`${basePath}/uv_coord.bin`);
      if (!response.ok) {
        console.warn('[ImageEncoder] uv_coord.bin not found, will use fallback sampling');
        return;
      }
      const buffer = await response.arrayBuffer();
      this.uvCoord = new Float32Array(buffer);
      console.log('[ImageEncoder] ✅ UV coordinates loaded:', {
        length: this.uvCoord.length,
        numVertices: this.uvCoord.length / 2
      });
    } catch (error) {
      console.warn('[ImageEncoder] Failed to load UV coordinates:', error);
    }
  }

  /**
   * 768次元から128次元への適切な次元削減
   * 分散ベースの次元選択を使用
   */
  private reduceDimensions(
    featuresData: Float32Array,
    numPatches: number,
    inputDim: number,
    outputDim: number
  ): Float32Array {
    console.log('[ImageEncoder] Reducing dimensions:', inputDim, '->', outputDim);

    // 各次元の分散を計算
    const variances = new Float32Array(inputDim);
    const means = new Float32Array(inputDim);

    // 平均を計算
    for (let d = 0; d < inputDim; d++) {
      let sum = 0;
      for (let p = 0; p < numPatches; p++) {
        sum += featuresData[p * inputDim + d];
      }
      means[d] = sum / numPatches;
    }

    // 分散を計算
    for (let d = 0; d < inputDim; d++) {
      let variance = 0;
      for (let p = 0; p < numPatches; p++) {
        const diff = featuresData[p * inputDim + d] - means[d];
        variance += diff * diff;
      }
      variances[d] = variance / numPatches;
    }

    // 分散の大きい順にソートしてインデックスを取得
    const indices = Array.from({ length: inputDim }, (_, i) => i);
    indices.sort((a, b) => variances[b] - variances[a]);
    const topIndices = indices.slice(0, outputDim);

    console.log('[ImageEncoder] Top variance dimensions (first 10):',
      topIndices.slice(0, 10).map(i => `${i}(var=${variances[i].toFixed(4)})`));

    // 中心化して選択された次元で出力
    const reduced = new Float32Array(numPatches * outputDim);
    for (let p = 0; p < numPatches; p++) {
      for (let d = 0; d < outputDim; d++) {
        const srcDim = topIndices[d];
        reduced[p * outputDim + d] = featuresData[p * inputDim + srcDim] - means[srcDim];
      }
    }

    return reduced;
  }

  /**
   * Feature mapを2Dグリッドとしてアップサンプリング
   */
  private upsampleFeatureMap(
    reducedFeatures: Float32Array,
    numPatches: number,
    featureDim: number,
    targetSize: number
  ): Float32Array {
    // DINOv2のパッチグリッドサイズを計算（通常16x16または14x14）
    const gridSize = Math.round(Math.sqrt(numPatches));
    console.log('[ImageEncoder] Upsampling feature map:', {
      inputGrid: `${gridSize}x${gridSize}`,
      targetSize: `${targetSize}x${targetSize}`
    });

    const output = new Float32Array(targetSize * targetSize * featureDim);
    const scale = gridSize / targetSize;

    for (let y = 0; y < targetSize; y++) {
      for (let x = 0; x < targetSize; x++) {
        // バイリニア補間のための座標計算
        const srcX = x * scale;
        const srcY = y * scale;

        const x0 = Math.floor(srcX);
        const x1 = Math.min(x0 + 1, gridSize - 1);
        const y0 = Math.floor(srcY);
        const y1 = Math.min(y0 + 1, gridSize - 1);

        const wx = srcX - x0;
        const wy = srcY - y0;

        // 4つのパッチインデックス
        const p00 = y0 * gridSize + x0;
        const p10 = y0 * gridSize + x1;
        const p01 = y1 * gridSize + x0;
        const p11 = y1 * gridSize + x1;

        const dstIdx = (y * targetSize + x) * featureDim;

        // 各次元でバイリニア補間
        for (let d = 0; d < featureDim; d++) {
          const v00 = reducedFeatures[p00 * featureDim + d];
          const v10 = reducedFeatures[p10 * featureDim + d];
          const v01 = reducedFeatures[p01 * featureDim + d];
          const v11 = reducedFeatures[p11 * featureDim + d];

          const top = v00 * (1 - wx) + v10 * wx;
          const bottom = v01 * (1 - wx) + v11 * wx;
          output[dstIdx + d] = top * (1 - wy) + bottom * wy;
        }
      }
    }

    return output;
  }

  /**
   * UV座標を使って特徴マップからサンプリング
   */
  private sampleWithUV(
    featureMap: Float32Array,
    mapSize: number,
    featureDim: number,
    targetVertexCount: number
  ): Float32Array {
    const output = new Float32Array(targetVertexCount * featureDim);

    if (!this.uvCoord || this.uvCoord.length < targetVertexCount * 2) {
      console.warn('[ImageEncoder] UV coordinates not available, using grid sampling');
      return this.fallbackGridSampling(featureMap, mapSize, featureDim, targetVertexCount);
    }

    console.log('[ImageEncoder] Sampling with UV coordinates...');

    for (let i = 0; i < targetVertexCount; i++) {
      // UV座標を取得（[0, 1]範囲）
      const u = this.uvCoord[i * 2];
      const v = this.uvCoord[i * 2 + 1];

      // UV座標を特徴マップ座標に変換
      // 注意: Vは通常上下反転が必要
      const mapX = u * (mapSize - 1);
      const mapY = (1 - v) * (mapSize - 1); // V反転

      // バイリニア補間
      const x0 = Math.floor(mapX);
      const x1 = Math.min(x0 + 1, mapSize - 1);
      const y0 = Math.floor(mapY);
      const y1 = Math.min(y0 + 1, mapSize - 1);

      const wx = mapX - x0;
      const wy = mapY - y0;

      const idx00 = (y0 * mapSize + x0) * featureDim;
      const idx10 = (y0 * mapSize + x1) * featureDim;
      const idx01 = (y1 * mapSize + x0) * featureDim;
      const idx11 = (y1 * mapSize + x1) * featureDim;

      const dstIdx = i * featureDim;

      for (let d = 0; d < featureDim; d++) {
        const v00 = featureMap[idx00 + d];
        const v10 = featureMap[idx10 + d];
        const v01 = featureMap[idx01 + d];
        const v11 = featureMap[idx11 + d];

        const top = v00 * (1 - wx) + v10 * wx;
        const bottom = v01 * (1 - wx) + v11 * wx;
        output[dstIdx + d] = top * (1 - wy) + bottom * wy;
      }
    }

    return output;
  }

  /**
   * フォールバック: グリッドベースのサンプリング
   */
  private fallbackGridSampling(
    featureMap: Float32Array,
    mapSize: number,
    featureDim: number,
    targetVertexCount: number
  ): Float32Array {
    console.log('[ImageEncoder] Using fallback grid sampling');
    const output = new Float32Array(targetVertexCount * featureDim);

    // テンプレートメッシュの頂点を均等に分布していると仮定
    // 頭部・上半身の典型的なUV展開パターンを考慮
    for (let i = 0; i < targetVertexCount; i++) {
      // 頂点インデックスを2D座標にマッピング（近似）
      const ratio = i / targetVertexCount;

      // 上半身メッシュの典型的な配置を考慮
      // 頭部は上部、胴体は下部
      const mapX = (ratio * 0.8 + 0.1) * (mapSize - 1); // 中央80%を使用
      const mapY = (ratio * 0.7 + 0.15) * (mapSize - 1);

      const x0 = Math.floor(mapX);
      const x1 = Math.min(x0 + 1, mapSize - 1);
      const y0 = Math.floor(mapY);
      const y1 = Math.min(y0 + 1, mapSize - 1);

      const wx = mapX - x0;
      const wy = mapY - y0;

      const idx00 = (y0 * mapSize + x0) * featureDim;
      const idx10 = (y0 * mapSize + x1) * featureDim;
      const idx01 = (y1 * mapSize + x0) * featureDim;
      const idx11 = (y1 * mapSize + x1) * featureDim;

      const dstIdx = i * featureDim;

      for (let d = 0; d < featureDim; d++) {
        const v00 = featureMap[idx00 + d] || 0;
        const v10 = featureMap[idx10 + d] || 0;
        const v01 = featureMap[idx01 + d] || 0;
        const v11 = featureMap[idx11 + d] || 0;

        const top = v00 * (1 - wx) + v10 * wx;
        const bottom = v01 * (1 - wx) + v11 * wx;
        output[dstIdx + d] = top * (1 - wy) + bottom * wy;
      }
    }

    return output;
  }

  /**
   * 画像からprojection_featureを抽出
   * GUAVA論文に基づく正しい実装:
   * 1. DINOv2で特徴抽出
   * 2. 分散ベースの次元削減（768→128）
   * 3. 特徴マップをアップサンプリング
   * 4. UV座標を使って各頂点の特徴をサンプリング
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

      // CLS tokenを除外
      const features = last_hidden_state.slice(null, [1, null], null);
      const featuresData = features.data as Float32Array;
      const numPatches = features.dims[1];
      const featureDim = features.dims[2]; // 768

      console.log('[ImageEncoder] DINOv2 output:', {
        numPatches,
        featureDim,
        patchGrid: `${Math.sqrt(numPatches)}x${Math.sqrt(numPatches)}`
      });

      // 4. 次元削減: 768 → 128（分散ベース）
      const reducedFeatures = this.reduceDimensions(
        featuresData,
        numPatches,
        featureDim,
        128
      );

      // 5. 特徴マップをアップサンプリング（画像に近い解像度へ）
      const targetMapSize = 64; // 64x64の特徴マップ
      const featureMap = this.upsampleFeatureMap(
        reducedFeatures,
        numPatches,
        128,
        targetMapSize
      );

      // 6. UV座標を使って各頂点の特徴をサンプリング
      const projectionFeature = this.sampleWithUV(
        featureMap,
        targetMapSize,
        128,
        targetVertexCount
      );

      // 7. 特徴量の正規化（標準化）
      this.normalizeFeatures(projectionFeature, targetVertexCount, 128);

      const elapsed = performance.now() - startTime;
      console.log(`[ImageEncoder] ✅ Feature extraction completed in ${elapsed.toFixed(2)}ms`);

      // 統計情報
      const sampleSize = Math.min(1000, projectionFeature.length);
      const sample = Array.from(projectionFeature.slice(0, sampleSize));
      const mean = sample.reduce((a, b) => a + b, 0) / sample.length;
      const std = Math.sqrt(sample.reduce((sum, v) => sum + (v - mean) ** 2, 0) / sample.length);

      console.log('[ImageEncoder] Feature statistics:', {
        min: Math.min(...sample).toFixed(4),
        max: Math.max(...sample).toFixed(4),
        mean: mean.toFixed(4),
        std: std.toFixed(4),
        nonZeroRatio: (sample.filter(v => Math.abs(v) > 0.001).length / sample.length).toFixed(3)
      });

      return projectionFeature;

    } catch (error) {
      console.error('[ImageEncoder] ❌ Feature extraction failed:', error);
      throw error;
    }
  }

  /**
   * 特徴量を正規化（各次元を標準化）
   */
  private normalizeFeatures(
    features: Float32Array,
    numVertices: number,
    featureDim: number
  ): void {
    // 各次元の平均と標準偏差を計算
    for (let d = 0; d < featureDim; d++) {
      let sum = 0;
      for (let v = 0; v < numVertices; v++) {
        sum += features[v * featureDim + d];
      }
      const mean = sum / numVertices;

      let variance = 0;
      for (let v = 0; v < numVertices; v++) {
        const diff = features[v * featureDim + d] - mean;
        variance += diff * diff;
      }
      const std = Math.sqrt(variance / numVertices) + 1e-8;

      // 標準化
      for (let v = 0; v < numVertices; v++) {
        features[v * featureDim + d] = (features[v * featureDim + d] - mean) / std;
      }
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
      this.uvCoord = null;
      this.initialized = false;
      console.log('[ImageEncoder] Disposed');
    }
  }
}