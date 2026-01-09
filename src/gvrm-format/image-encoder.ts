// image-encoder.ts
// DINOv2 Image Encoder for GUAVA
// GUAVA論文 Section 3.2 に基づく正しい実装:
// 1. DINOv2で特徴抽出
// 2. Conv層でアップサンプリング → appearance feature map Fa
// 3. Projection Sampling: 各頂点をスクリーン座標に投影してFaからサンプリング

import { AutoProcessor, AutoModel, RawImage } from '@huggingface/transformers';

export interface CameraParams {
  viewMatrix: Float32Array;   // 4x4 view matrix (column-major)
  projMatrix: Float32Array;   // 4x4 projection matrix (column-major)
  screenWidth: number;
  screenHeight: number;
}

export class ImageEncoder {
  private model: any = null;
  private processor: any = null;
  private initialized = false;
  private convWeights: Float32Array | null = null;

  /**
   * 初期化：DINOv2モデルをロード
   */
  async init(): Promise<void> {
    if (this.initialized) return;

    console.log('[ImageEncoder] Initializing DINOv2...');

    try {
      const modelId = 'onnx-community/dinov2-base';

      console.log('[ImageEncoder] Loading processor...');
      this.processor = await AutoProcessor.from_pretrained(modelId);

      console.log('[ImageEncoder] Loading model...');
      this.model = await AutoModel.from_pretrained(modelId, {
        quantized: true
      });

      // Conv重み（学習済み）をロード試行
      await this.loadConvWeights('/assets');

      this.initialized = true;
      console.log('[ImageEncoder] ✅ DINOv2 initialized');
    } catch (error) {
      console.error('[ImageEncoder] ❌ Failed to initialize:', error);
      throw new Error(`Image Encoder initialization failed: ${error}`);
    }
  }

  /**
   * 学習済みConv重みをロード
   */
  private async loadConvWeights(basePath: string): Promise<void> {
    try {
      const response = await fetch(`${basePath}/encoder_conv.bin`);
      if (!response.ok) {
        console.warn('[ImageEncoder] encoder_conv.bin not found, using bilinear upsampling');
        return;
      }
      const buffer = await response.arrayBuffer();
      this.convWeights = new Float32Array(buffer);
      console.log('[ImageEncoder] ✅ Conv weights loaded:', this.convWeights.length);
    } catch (error) {
      console.warn('[ImageEncoder] Failed to load conv weights:', error);
    }
  }

  /**
   * DINOv2パッチ特徴から appearance feature map Fa を生成
   * GUAVA: DINOv2 → Conv → Fa (入力画像と同解像度)
   */
  private createAppearanceFeatureMap(
    patchFeatures: Float32Array,
    numPatches: number,
    patchDim: number,
    targetWidth: number,
    targetHeight: number,
    outputDim: number
  ): Float32Array {
    // DINOv2-base: 14x14 patches for 224x224 input
    const patchGridSize = Math.round(Math.sqrt(numPatches));

    console.log('[ImageEncoder] Creating appearance feature map:', {
      patchGrid: `${patchGridSize}x${patchGridSize}`,
      patchDim,
      targetSize: `${targetWidth}x${targetHeight}`,
      outputDim
    });

    const featureMap = new Float32Array(targetWidth * targetHeight * outputDim);

    // 学習済みConv重みがある場合は使用、なければバイリニア補間 + 次元マッピング
    for (let y = 0; y < targetHeight; y++) {
      for (let x = 0; x < targetWidth; x++) {
        // パッチ座標へマッピング
        const srcX = (x / targetWidth) * (patchGridSize - 1);
        const srcY = (y / targetHeight) * (patchGridSize - 1);

        const x0 = Math.floor(srcX);
        const x1 = Math.min(x0 + 1, patchGridSize - 1);
        const y0 = Math.floor(srcY);
        const y1 = Math.min(y0 + 1, patchGridSize - 1);

        const wx = srcX - x0;
        const wy = srcY - y0;

        // 4つの隣接パッチインデックス
        const p00 = y0 * patchGridSize + x0;
        const p10 = y0 * patchGridSize + x1;
        const p01 = y1 * patchGridSize + x0;
        const p11 = y1 * patchGridSize + x1;

        const dstIdx = (y * targetWidth + x) * outputDim;

        // 各出力次元について計算
        for (let d = 0; d < outputDim; d++) {
          let value = 0;

          if (this.convWeights) {
            // 学習済み重みを使った変換
            const weightOffset = d * patchDim;
            for (let sd = 0; sd < patchDim; sd++) {
              const w = this.convWeights[weightOffset + sd];
              const v00 = patchFeatures[p00 * patchDim + sd];
              const v10 = patchFeatures[p10 * patchDim + sd];
              const v01 = patchFeatures[p01 * patchDim + sd];
              const v11 = patchFeatures[p11 * patchDim + sd];

              const top = v00 * (1 - wx) + v10 * wx;
              const bottom = v01 * (1 - wx) + v11 * wx;
              value += w * (top * (1 - wy) + bottom * wy);
            }
          } else {
            // 重みがない場合: 次元をストライドでマッピング + バイリニア補間
            // 768次元 → outputDim次元へのシンプルな縮約
            const srcDimStart = Math.floor((d / outputDim) * patchDim);
            const srcDimEnd = Math.min(srcDimStart + Math.ceil(patchDim / outputDim), patchDim);

            let sum = 0;
            let count = 0;

            for (let sd = srcDimStart; sd < srcDimEnd; sd++) {
              const v00 = patchFeatures[p00 * patchDim + sd];
              const v10 = patchFeatures[p10 * patchDim + sd];
              const v01 = patchFeatures[p01 * patchDim + sd];
              const v11 = patchFeatures[p11 * patchDim + sd];

              const top = v00 * (1 - wx) + v10 * wx;
              const bottom = v01 * (1 - wx) + v11 * wx;
              sum += top * (1 - wy) + bottom * wy;
              count++;
            }

            value = count > 0 ? sum / count : 0;
          }

          featureMap[dstIdx + d] = value;
        }
      }
    }

    return featureMap;
  }

  /**
   * 3D頂点を2Dスクリーン座標に投影
   * GUAVA Eq.2: P(v^i, RT_s)
   */
  private projectVertex(
    vx: number, vy: number, vz: number,
    viewMatrix: Float32Array,
    projMatrix: Float32Array,
    screenWidth: number,
    screenHeight: number
  ): [number, number, number] {
    // View transform (column-major)
    const viewX = viewMatrix[0] * vx + viewMatrix[4] * vy + viewMatrix[8] * vz + viewMatrix[12];
    const viewY = viewMatrix[1] * vx + viewMatrix[5] * vy + viewMatrix[9] * vz + viewMatrix[13];
    const viewZ = viewMatrix[2] * vx + viewMatrix[6] * vy + viewMatrix[10] * vz + viewMatrix[14];
    const viewW = viewMatrix[3] * vx + viewMatrix[7] * vy + viewMatrix[11] * vz + viewMatrix[15];

    // Projection transform
    const clipX = projMatrix[0] * viewX + projMatrix[4] * viewY + projMatrix[8] * viewZ + projMatrix[12] * viewW;
    const clipY = projMatrix[1] * viewX + projMatrix[5] * viewY + projMatrix[9] * viewZ + projMatrix[13] * viewW;
    const clipZ = projMatrix[2] * viewX + projMatrix[6] * viewY + projMatrix[10] * viewZ + projMatrix[14] * viewW;
    const clipW = projMatrix[3] * viewX + projMatrix[7] * viewY + projMatrix[11] * viewZ + projMatrix[15] * viewW;

    // Perspective division → NDC
    const ndcX = clipX / clipW;
    const ndcY = clipY / clipW;
    const depth = clipZ / clipW;

    // NDC → スクリーン座標
    const screenX = (ndcX * 0.5 + 0.5) * screenWidth;
    const screenY = (1.0 - (ndcY * 0.5 + 0.5)) * screenHeight; // Y軸反転

    return [screenX, screenY, depth];
  }

  /**
   * Feature mapから2D位置でバイリニアサンプリング
   * GUAVA Eq.2: S(F_a, screen_pos)
   */
  private sampleFeatureMapAt(
    featureMap: Float32Array,
    mapWidth: number,
    mapHeight: number,
    featureDim: number,
    screenX: number,
    screenY: number,
    output: Float32Array,
    outputOffset: number
  ): void {
    // スクリーン座標をfeature map座標に変換
    const x = Math.max(0, Math.min(screenX, mapWidth - 1));
    const y = Math.max(0, Math.min(screenY, mapHeight - 1));

    const x0 = Math.floor(x);
    const x1 = Math.min(x0 + 1, mapWidth - 1);
    const y0 = Math.floor(y);
    const y1 = Math.min(y0 + 1, mapHeight - 1);

    const wx = x - x0;
    const wy = y - y0;

    const idx00 = (y0 * mapWidth + x0) * featureDim;
    const idx10 = (y0 * mapWidth + x1) * featureDim;
    const idx01 = (y1 * mapWidth + x0) * featureDim;
    const idx11 = (y1 * mapWidth + x1) * featureDim;

    for (let d = 0; d < featureDim; d++) {
      const v00 = featureMap[idx00 + d] || 0;
      const v10 = featureMap[idx10 + d] || 0;
      const v01 = featureMap[idx01 + d] || 0;
      const v11 = featureMap[idx11 + d] || 0;

      const top = v00 * (1 - wx) + v10 * wx;
      const bottom = v01 * (1 - wx) + v11 * wx;
      output[outputOffset + d] = top * (1 - wy) + bottom * wy;
    }
  }

  /**
   * Projection Sampling: 各頂点についてスクリーン座標に投影し、Faからサンプリング
   * GUAVA Eq.2: f_p^i = S(F_a, P(v^i, RT_s))
   */
  private projectionSampling(
    featureMap: Float32Array,
    mapWidth: number,
    mapHeight: number,
    featureDim: number,
    vertices: Float32Array,
    vertexCount: number,
    camera: CameraParams
  ): Float32Array {
    console.log('[ImageEncoder] Projection sampling:', {
      vertexCount,
      featureDim,
      mapSize: `${mapWidth}x${mapHeight}`
    });

    const projectionFeatures = new Float32Array(vertexCount * featureDim);

    let visibleCount = 0;

    for (let i = 0; i < vertexCount; i++) {
      const vx = vertices[i * 3];
      const vy = vertices[i * 3 + 1];
      const vz = vertices[i * 3 + 2];

      // 頂点をスクリーン座標に投影
      const [screenX, screenY, depth] = this.projectVertex(
        vx, vy, vz,
        camera.viewMatrix,
        camera.projMatrix,
        mapWidth,
        mapHeight
      );

      // 可視性チェック（画面内かつ前面）
      const isVisible =
        screenX >= 0 && screenX < mapWidth &&
        screenY >= 0 && screenY < mapHeight &&
        depth > 0 && depth < 1;

      if (isVisible) {
        visibleCount++;
      }

      // Feature mapからサンプリング
      this.sampleFeatureMapAt(
        featureMap,
        mapWidth,
        mapHeight,
        featureDim,
        screenX,
        screenY,
        projectionFeatures,
        i * featureDim
      );
    }

    console.log('[ImageEncoder] Visible vertices:', visibleCount, '/', vertexCount);

    return projectionFeatures;
  }

  /**
   * ID embeddingを生成（CLSトークンから）
   */
  private createIdEmbedding(clsToken: Float32Array, patchDim: number, outputDim: number): Float32Array {
    const idEmbedding = new Float32Array(outputDim);

    // 768次元 → outputDim次元への線形マッピング
    // 本来は学習済み重みで変換するが、ここでは均等サンプリング
    for (let i = 0; i < outputDim; i++) {
      const srcIdx = Math.floor((i / outputDim) * patchDim);
      idEmbedding[i] = clsToken[srcIdx] || 0;
    }

    return idEmbedding;
  }

  /**
   * 画像から特徴抽出し、各頂点のprojection featureを計算
   * GUAVA論文 Section 3.2 のメイン処理
   *
   * @param imageUrl ソース画像URL
   * @param vertices テンプレートメッシュ頂点（ポーズ空間）[x,y,z, x,y,z, ...]
   * @param vertexCount 頂点数
   * @param camera カメラパラメータ（view/proj行列）
   * @param featureDim 出力特徴次元（デフォルト128）
   */
  async extractFeatures(
    imageUrl: string,
    vertices: Float32Array,
    vertexCount: number,
    camera: CameraParams,
    featureDim: number = 128
  ): Promise<{ projectionFeature: Float32Array; idEmbedding: Float32Array }> {
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

      // 2. DINOv2前処理 & 特徴抽出
      const inputs = await this.processor(image);
      const { last_hidden_state } = await this.model(inputs);

      // 3. CLSトークン（ID embedding用）と パッチトークンを分離
      // last_hidden_state shape: [1, 1+num_patches, 768]
      const clsToken = last_hidden_state.slice(null, [0, 1], null);
      const patchTokens = last_hidden_state.slice(null, [1, null], null);

      const clsData = clsToken.data as Float32Array;
      const patchData = patchTokens.data as Float32Array;
      const numPatches = patchTokens.dims[1];
      const patchDim = patchTokens.dims[2]; // 768

      console.log('[ImageEncoder] DINOv2 output:', {
        numPatches,
        patchDim,
        patchGrid: `${Math.sqrt(numPatches)}x${Math.sqrt(numPatches)}`
      });

      // 4. Appearance Feature Map Fa を生成（DINOv2 → Conv → Fa）
      const featureMap = this.createAppearanceFeatureMap(
        patchData,
        numPatches,
        patchDim,
        image.width,
        image.height,
        featureDim
      );

      // 5. Projection Sampling: f_p^i = S(F_a, P(v^i, RT_s))
      const projectionFeature = this.projectionSampling(
        featureMap,
        image.width,
        image.height,
        featureDim,
        vertices,
        vertexCount,
        camera
      );

      // 6. ID Embedding生成（CLSトークンから）
      const idEmbedding = this.createIdEmbedding(clsData, patchDim, 256);

      // 7. 特徴量の正規化
      this.normalizeFeatures(projectionFeature, vertexCount, featureDim);

      const elapsed = performance.now() - startTime;
      console.log(`[ImageEncoder] ✅ Feature extraction completed in ${elapsed.toFixed(2)}ms`);

      // 統計情報
      const sampleSize = Math.min(1000, projectionFeature.length);
      const sample = Array.from(projectionFeature.slice(0, sampleSize));
      const mean = sample.reduce((a, b) => a + b, 0) / sample.length;
      const std = Math.sqrt(sample.reduce((sum, v) => sum + (v - mean) ** 2, 0) / sample.length);

      console.log('[ImageEncoder] Projection feature statistics:', {
        min: Math.min(...sample).toFixed(4),
        max: Math.max(...sample).toFixed(4),
        mean: mean.toFixed(4),
        std: std.toFixed(4),
        nonZeroRatio: (sample.filter(v => Math.abs(v) > 0.001).length / sample.length).toFixed(3)
      });

      return { projectionFeature, idEmbedding };

    } catch (error) {
      console.error('[ImageEncoder] ❌ Feature extraction failed:', error);
      throw error;
    }
  }

  /**
   * 後方互換性のための簡易版（カメラパラメータなし）
   * 頂点座標から自動的にカメラを設定
   */
  async extractFeaturesSimple(
    imageUrl: string,
    targetVertexCount: number = 10595
  ): Promise<Float32Array> {
    // デフォルトのテンプレート頂点を生成（上半身メッシュの典型的な配置）
    const vertices = new Float32Array(targetVertexCount * 3);
    for (let i = 0; i < targetVertexCount; i++) {
      // 頂点を円柱状に配置（上半身の近似）
      const ratio = i / targetVertexCount;
      const angle = ratio * Math.PI * 10; // スパイラル配置
      const height = 1.0 + ratio * 0.8;   // 1.0 ~ 1.8 の高さ範囲
      const radius = 0.15 + 0.1 * Math.sin(ratio * Math.PI * 3);

      vertices[i * 3] = Math.cos(angle) * radius;
      vertices[i * 3 + 1] = height;
      vertices[i * 3 + 2] = Math.sin(angle) * radius;
    }

    // デフォルトカメラ設定（gvrm.tsと同じ）
    const camera = this.createDefaultCamera(512, 512);

    const { projectionFeature } = await this.extractFeatures(
      imageUrl,
      vertices,
      targetVertexCount,
      camera,
      128
    );

    return projectionFeature;
  }

  /**
   * デフォルトカメラパラメータを生成
   */
  createDefaultCamera(width: number, height: number): CameraParams {
    // View Matrix: position (0, 1.4, 0.8), looking at (0, 1.4, 0)
    const viewMatrix = new Float32Array([
      1, 0, 0, 0,
      0, 1, 0, 0,
      0, 0, 1, 0,
      0, -1.4, -0.8, 1
    ]);

    // Projection Matrix: FOV 45°, aspect 1:1
    const fov = 45 * Math.PI / 180;
    const aspect = width / height;
    const near = 0.01;
    const far = 100;
    const f = 1 / Math.tan(fov / 2);

    const projMatrix = new Float32Array([
      f / aspect, 0, 0, 0,
      0, f, 0, 0,
      0, 0, (far + near) / (near - far), -1,
      0, 0, (2 * far * near) / (near - far), 0
    ]);

    return {
      viewMatrix,
      projMatrix,
      screenWidth: width,
      screenHeight: height
    };
  }

  /**
   * Three.jsのカメラからCameraParamsを作成
   */
  createCameraFromThree(
    camera: { matrixWorldInverse: { elements: number[] }, projectionMatrix: { elements: number[] } },
    width: number,
    height: number
  ): CameraParams {
    return {
      viewMatrix: new Float32Array(camera.matrixWorldInverse.elements),
      projMatrix: new Float32Array(camera.projectionMatrix.elements),
      screenWidth: width,
      screenHeight: height
    };
  }

  /**
   * 特徴量を正規化（標準化）
   */
  private normalizeFeatures(
    features: Float32Array,
    numVertices: number,
    featureDim: number
  ): void {
    // 各次元の平均と標準偏差を計算して標準化
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
      this.convWeights = null;
      this.initialized = false;
      console.log('[ImageEncoder] Disposed');
    }
  }
}
