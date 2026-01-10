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

export interface SourceCameraConfig {
  position: { x: number; y: number; z: number };
  target: { x: number; y: number; z: number };
  fov: number;
  imageWidth: number;
  imageHeight: number;
}

export class ImageEncoder {
  private model: any = null;
  private processor: any = null;
  private initialized = false;
  // 注: GUAVA論文ではConv層で特徴をアップサンプルするが、
  // 学習済みConv重みがない場合はバイリニア補間で代替

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

      this.initialized = true;
      console.log('[ImageEncoder] ✅ DINOv2 initialized (bilinear upsampling mode)');
    } catch (error) {
      console.error('[ImageEncoder] ❌ Failed to initialize:', error);
      throw new Error(`Image Encoder initialization failed: ${error}`);
    }
  }

  /**
   * DINOv2パッチ特徴から appearance feature map Fa を生成
   * GUAVA: DINOv2 → Conv → Fa (入力画像と同解像度)
   *
   * 注: 学習済みConv重みがないため、バイリニア補間 + 次元削減で代替
   * 768次元 → outputDim次元への変換は、分散の大きい次元を選択
   */
  private createAppearanceFeatureMap(
    patchFeatures: Float32Array,
    numPatches: number,
    patchDim: number,
    targetWidth: number,
    targetHeight: number,
    outputDim: number
  ): Float32Array {
    // DINOv2-base: 16x16 patches for 224x224 input (196 patches)
    const patchGridSize = Math.round(Math.sqrt(numPatches));

    console.log('[ImageEncoder] Creating appearance feature map:', {
      patchGrid: `${patchGridSize}x${patchGridSize}`,
      patchDim,
      targetSize: `${targetWidth}x${targetHeight}`,
      outputDim
    });

    // Step 1: 各次元の分散を計算して、情報量の多い次元を選択
    const variances = new Float32Array(patchDim);
    for (let d = 0; d < patchDim; d++) {
      let sum = 0;
      for (let p = 0; p < numPatches; p++) {
        sum += patchFeatures[p * patchDim + d];
      }
      const mean = sum / numPatches;

      let variance = 0;
      for (let p = 0; p < numPatches; p++) {
        const diff = patchFeatures[p * patchDim + d] - mean;
        variance += diff * diff;
      }
      variances[d] = variance / numPatches;
    }

    // 分散の大きい順にソートして上位outputDim次元を選択
    const dimIndices = Array.from({ length: patchDim }, (_, i) => i);
    dimIndices.sort((a, b) => variances[b] - variances[a]);
    const selectedDims = dimIndices.slice(0, outputDim);

    console.log('[ImageEncoder] Selected top dimensions by variance:',
      selectedDims.slice(0, 5).map(i => `${i}(var=${variances[i].toFixed(4)})`).join(', '));

    const featureMap = new Float32Array(targetWidth * targetHeight * outputDim);

    // Step 2: バイリニア補間でアップサンプル
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

        // 選択された次元のみを使用
        for (let d = 0; d < outputDim; d++) {
          const srcDim = selectedDims[d];

          const v00 = patchFeatures[p00 * patchDim + srcDim];
          const v10 = patchFeatures[p10 * patchDim + srcDim];
          const v01 = patchFeatures[p01 * patchDim + srcDim];
          const v11 = patchFeatures[p11 * patchDim + srcDim];

          const top = v00 * (1 - wx) + v10 * wx;
          const bottom = v01 * (1 - wx) + v11 * wx;
          featureMap[dstIdx + d] = top * (1 - wy) + bottom * wy;
        }
      }
    }

    return featureMap;
  }

  /**
   * 画像を中央から正方形にクロップ
   * DINOv2は正方形入力を期待するため、横長/縦長画像を正方形に変換
   */
  private cropToSquare(image: any): any {
    const { width, height } = image;

    // 既に正方形の場合はそのまま返す
    if (width === height) {
      return image;
    }

    const size = Math.min(width, height);
    const offsetX = Math.floor((width - size) / 2);
    const offsetY = Math.floor((height - size) / 2);

    console.log('[ImageEncoder] Cropping image:', {
      original: `${width}x${height}`,
      cropSize: size,
      offset: `(${offsetX}, ${offsetY})`
    });

    // RawImage.crop() メソッドがある場合は使用
    // ない場合は手動でクロップ
    if (typeof image.crop === 'function') {
      return image.crop([offsetX, offsetY, offsetX + size, offsetY + size]);
    }

    // 手動クロップ（RawImageの内部構造に依存）
    // channels, data, width, height プロパティを持つことを想定
    const channels = image.channels || 3;
    const srcData = image.data;
    const dstData = new Uint8Array(size * size * channels);

    for (let y = 0; y < size; y++) {
      for (let x = 0; x < size; x++) {
        const srcIdx = ((y + offsetY) * width + (x + offsetX)) * channels;
        const dstIdx = (y * size + x) * channels;
        for (let c = 0; c < channels; c++) {
          dstData[dstIdx + c] = srcData[srcIdx + c];
        }
      }
    }

    // RawImageと同じ形式のオブジェクトを作成
    // @huggingface/transformers の RawImage 形式に合わせる
    return {
      data: dstData,
      width: size,
      height: size,
      channels: channels
    };
  }

  /**
   * 3D頂点を2Dスクリーン座標に投影
   * GUAVA Eq.2: P(v^i, RT_s)
   * @returns [screenX, screenY, depth, clipW] - clipW > 0 means vertex is in front of camera
   */
  private projectVertex(
    vx: number, vy: number, vz: number,
    viewMatrix: Float32Array,
    projMatrix: Float32Array,
    screenWidth: number,
    screenHeight: number
  ): [number, number, number, number] {
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
    // Note: clipW can be <= 0 for vertices behind the camera
    const safeW = Math.abs(clipW) > 1e-6 ? clipW : 1e-6;
    const ndcX = clipX / safeW;
    const ndcY = clipY / safeW;
    const depth = clipZ / safeW;

    // NDC → スクリーン座標
    const screenX = (ndcX * 0.5 + 0.5) * screenWidth;
    const screenY = (1.0 - (ndcY * 0.5 + 0.5)) * screenHeight; // Y軸反転

    return [screenX, screenY, depth, clipW];
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

    // デバッグ: カメラ行列の情報
    console.log('[ImageEncoder] View matrix diagonal:',
      camera.viewMatrix[0].toFixed(3), camera.viewMatrix[5].toFixed(3),
      camera.viewMatrix[10].toFixed(3), camera.viewMatrix[15].toFixed(3));
    console.log('[ImageEncoder] View matrix translation:',
      camera.viewMatrix[12].toFixed(3), camera.viewMatrix[13].toFixed(3),
      camera.viewMatrix[14].toFixed(3));
    console.log('[ImageEncoder] Proj matrix [0,0], [1,1]:',
      camera.projMatrix[0].toFixed(3), camera.projMatrix[5].toFixed(3));

    // デバッグ: 頂点座標の範囲を確認
    let minVx = Infinity, maxVx = -Infinity;
    let minVy = Infinity, maxVy = -Infinity;
    let minVz = Infinity, maxVz = -Infinity;
    for (let i = 0; i < vertexCount; i++) {
      const vx = vertices[i * 3];
      const vy = vertices[i * 3 + 1];
      const vz = vertices[i * 3 + 2];
      minVx = Math.min(minVx, vx); maxVx = Math.max(maxVx, vx);
      minVy = Math.min(minVy, vy); maxVy = Math.max(maxVy, vy);
      minVz = Math.min(minVz, vz); maxVz = Math.max(maxVz, vz);
    }
    console.log('[ImageEncoder] Vertex bounds: X=[' + minVx.toFixed(3) + ', ' + maxVx.toFixed(3) +
      '], Y=[' + minVy.toFixed(3) + ', ' + maxVy.toFixed(3) +
      '], Z=[' + minVz.toFixed(3) + ', ' + maxVz.toFixed(3) + ']');

    const projectionFeatures = new Float32Array(vertexCount * featureDim);

    let visibleCount = 0;
    let behindCamera = 0;
    let outsideScreen = 0;

    // デバッグ: スクリーン座標とNDCの範囲を追跡
    let minSx = Infinity, maxSx = -Infinity;
    let minSy = Infinity, maxSy = -Infinity;
    let minDepth = Infinity, maxDepth = -Infinity;
    let minNdcX = Infinity, maxNdcX = -Infinity;
    let minNdcY = Infinity, maxNdcY = -Infinity;

    for (let i = 0; i < vertexCount; i++) {
      const vx = vertices[i * 3];
      const vy = vertices[i * 3 + 1];
      const vz = vertices[i * 3 + 2];

      // 頂点をスクリーン座標に投影
      const [screenX, screenY, depth, clipW] = this.projectVertex(
        vx, vy, vz,
        camera.viewMatrix,
        camera.projMatrix,
        mapWidth,
        mapHeight
      );

      // デバッグ: 座標範囲を追跡
      if (clipW > 0) {
        minSx = Math.min(minSx, screenX); maxSx = Math.max(maxSx, screenX);
        minSy = Math.min(minSy, screenY); maxSy = Math.max(maxSy, screenY);
        minDepth = Math.min(minDepth, depth); maxDepth = Math.max(maxDepth, depth);
        // NDCを逆算
        const ndcX = (screenX / mapWidth - 0.5) * 2;
        const ndcY = ((1 - screenY / mapHeight) - 0.5) * 2;
        minNdcX = Math.min(minNdcX, ndcX); maxNdcX = Math.max(maxNdcX, ndcX);
        minNdcY = Math.min(minNdcY, ndcY); maxNdcY = Math.max(maxNdcY, ndcY);
      }

      // 可視性チェック:
      // 1. clipW > 0: 頂点がカメラの前にある（Three.js/OpenGLでは-Zが前方向、clipW = -viewZ）
      // 2. NDC depth in [-1, 1]: OpenGLの標準NDC範囲
      // 3. スクリーン座標が画面内
      const isInFront = clipW > 0;
      const isInNDCRange = depth >= -1 && depth <= 1;
      const isInScreen = screenX >= 0 && screenX < mapWidth &&
                         screenY >= 0 && screenY < mapHeight;

      const isVisible = isInFront && isInNDCRange && isInScreen;

      if (isVisible) {
        visibleCount++;
      } else {
        if (!isInFront) behindCamera++;
        else if (!isInScreen) outsideScreen++;
      }

      // Feature mapからサンプリング（可視でなくてもサンプリングは行う）
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

    console.log('[ImageEncoder] Screen coord bounds: X=[' + minSx.toFixed(1) + ', ' + maxSx.toFixed(1) +
      '], Y=[' + minSy.toFixed(1) + ', ' + maxSy.toFixed(1) +
      '], depth=[' + minDepth.toFixed(3) + ', ' + maxDepth.toFixed(3) + ']');
    console.log('[ImageEncoder] NDC bounds: X=[' + minNdcX.toFixed(3) + ', ' + maxNdcX.toFixed(3) +
      '], Y=[' + minNdcY.toFixed(3) + ', ' + maxNdcY.toFixed(3) + ']');
    console.log('[ImageEncoder] Visible vertices:', visibleCount, '/', vertexCount);
    if (visibleCount === 0) {
      console.warn('[ImageEncoder] ⚠️ No visible vertices! Check camera parameters.');
      console.warn('[ImageEncoder] Behind camera:', behindCamera, ', Outside screen:', outsideScreen);
    }

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

      // 2. 画像を中央正方形クロップ（アスペクト比問題を解決）
      // DINOv2は224x224正方形入力を期待するため、事前にクロップ
      const croppedImage = this.cropToSquare(image);
      console.log('[ImageEncoder] Cropped to square:', {
        width: croppedImage.width,
        height: croppedImage.height
      });

      // 3. DINOv2前処理 & 特徴抽出
      const inputs = await this.processor(croppedImage);
      const { last_hidden_state } = await this.model(inputs);

      // 4. CLSトークン（ID embedding用）と パッチトークンを分離
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

      // 5. Appearance Feature Map Fa を生成（DINOv2 → Conv → Fa）
      // GUAVA論文は512x512を使用。メモリ効率のため固定サイズを使用
      const FEATURE_MAP_SIZE = 256; // 256x256で十分な解像度
      const featureMap = this.createAppearanceFeatureMap(
        patchData,
        numPatches,
        patchDim,
        FEATURE_MAP_SIZE,
        FEATURE_MAP_SIZE,
        featureDim
      );

      // 6. Projection Sampling: f_p^i = S(F_a, P(v^i, RT_s))
      const projectionFeature = this.projectionSampling(
        featureMap,
        FEATURE_MAP_SIZE,
        FEATURE_MAP_SIZE,
        featureDim,
        vertices,
        vertexCount,
        camera
      );

      // 7. ID Embedding生成（CLSトークンから）
      const idEmbedding = this.createIdEmbedding(clsData, patchDim, 256);

      // 8. 特徴量の正規化
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
   * UV座標を使用して特徴抽出（3D投影の代替）
   * UV座標を直接feature mapにマッピング
   *
   * @param imageUrl ソース画像URL
   * @param uvCoords UV座標 [u,v, u,v, ...] 範囲[0,1]
   * @param vertexCount 頂点数
   * @param featureDim 出力特徴次元
   */
  async extractFeaturesWithUV(
    imageUrl: string,
    uvCoords: Float32Array,
    vertexCount: number,
    featureDim: number = 128
  ): Promise<{ projectionFeature: Float32Array; idEmbedding: Float32Array }> {
    if (!this.model || !this.processor) {
      throw new Error('[ImageEncoder] Not initialized. Call init() first.');
    }

    console.log('[ImageEncoder] Processing image with UV sampling:', imageUrl);

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

      // 4. Appearance Feature Map Fa を生成
      const FEATURE_MAP_SIZE = 256;
      const featureMap = this.createAppearanceFeatureMap(
        patchData,
        numPatches,
        patchDim,
        FEATURE_MAP_SIZE,
        FEATURE_MAP_SIZE,
        featureDim
      );

      // 5. UV座標を使ってFeature mapからサンプリング
      console.log('[ImageEncoder] UV-based sampling:', {
        vertexCount,
        featureDim,
        mapSize: `${FEATURE_MAP_SIZE}x${FEATURE_MAP_SIZE}`
      });

      // UV座標の範囲を確認
      let minU = Infinity, maxU = -Infinity;
      let minV = Infinity, maxV = -Infinity;
      for (let i = 0; i < vertexCount; i++) {
        const u = uvCoords[i * 2];
        const v = uvCoords[i * 2 + 1];
        minU = Math.min(minU, u); maxU = Math.max(maxU, u);
        minV = Math.min(minV, v); maxV = Math.max(maxV, v);
      }
      console.log('[ImageEncoder] UV bounds: U=[' + minU.toFixed(3) + ', ' + maxU.toFixed(3) +
        '], V=[' + minV.toFixed(3) + ', ' + maxV.toFixed(3) + ']');

      const projectionFeature = new Float32Array(vertexCount * featureDim);

      for (let i = 0; i < vertexCount; i++) {
        const u = uvCoords[i * 2];
        const v = uvCoords[i * 2 + 1];

        // UV座標をfeature map座標に変換
        // UV座標は[0,1]範囲、V軸は通常上向き
        const screenX = u * FEATURE_MAP_SIZE;
        const screenY = (1 - v) * FEATURE_MAP_SIZE; // V軸反転

        // Feature mapからサンプリング
        this.sampleFeatureMapAt(
          featureMap,
          FEATURE_MAP_SIZE,
          FEATURE_MAP_SIZE,
          featureDim,
          screenX,
          screenY,
          projectionFeature,
          i * featureDim
        );
      }

      // 6. ID Embedding生成
      const idEmbedding = this.createIdEmbedding(clsData, patchDim, 256);

      // 7. 特徴量の正規化
      this.normalizeFeatures(projectionFeature, vertexCount, featureDim);

      const elapsed = performance.now() - startTime;
      console.log(`[ImageEncoder] ✅ UV-based feature extraction completed in ${elapsed.toFixed(2)}ms`);

      // 統計情報
      const sampleSize = Math.min(1000, projectionFeature.length);
      const sample = Array.from(projectionFeature.slice(0, sampleSize));
      const mean = sample.reduce((a, b) => a + b, 0) / sample.length;
      const std = Math.sqrt(sample.reduce((sum, v) => sum + (v - mean) ** 2, 0) / sample.length);

      console.log('[ImageEncoder] UV projection feature statistics:', {
        min: Math.min(...sample).toFixed(4),
        max: Math.max(...sample).toFixed(4),
        mean: mean.toFixed(4),
        std: std.toFixed(4),
        nonZeroRatio: (sample.filter(v => Math.abs(v) > 0.001).length / sample.length).toFixed(3)
      });

      return { projectionFeature, idEmbedding };

    } catch (error) {
      console.error('[ImageEncoder] ❌ UV feature extraction failed:', error);
      throw error;
    }
  }

  /**
   * ソースカメラ設定をJSONファイルから読み込む
   */
  async loadSourceCameraConfig(jsonUrl: string): Promise<SourceCameraConfig> {
    const response = await fetch(jsonUrl);
    if (!response.ok) {
      throw new Error(`Failed to load camera config: ${response.status}`);
    }
    const config = await response.json();
    console.log('[ImageEncoder] Loaded source camera config:', config);
    return config;
  }

  /**
   * ソースカメラ設定からカメラパラメータを構築
   * GUAVA論文: ソース画像撮影時のカメラパラメータを使用
   */
  buildCameraParamsFromConfig(config: SourceCameraConfig, featureMapSize: number): CameraParams {
    const { position, target, fov, imageWidth, imageHeight } = config;

    // カメラ方向ベクトルを計算
    const dx = target.x - position.x;
    const dy = target.y - position.y;
    const dz = target.z - position.z;
    const len = Math.sqrt(dx * dx + dy * dy + dz * dz);

    // 前方向ベクトル (カメラが見る方向)
    const fx = dx / len;
    const fy = dy / len;
    const fz = dz / len;

    // 上方向ベクトル (仮定: Y軸が上)
    let ux = 0, uy = 1, uz = 0;

    // 右方向ベクトル = forward × up
    let rx = fy * uz - fz * uy;
    let ry = fz * ux - fx * uz;
    let rz = fx * uy - fy * ux;
    const rlen = Math.sqrt(rx * rx + ry * ry + rz * rz);
    rx /= rlen; ry /= rlen; rz /= rlen;

    // 真の上方向ベクトル = right × forward
    ux = ry * fz - rz * fy;
    uy = rz * fx - rx * fz;
    uz = rx * fy - ry * fx;

    // View Matrix (column-major): 世界座標からカメラ座標へ
    const viewMatrix = new Float32Array([
      rx, ux, -fx, 0,
      ry, uy, -fy, 0,
      rz, uz, -fz, 0,
      -(rx * position.x + ry * position.y + rz * position.z),
      -(ux * position.x + uy * position.y + uz * position.z),
      -(-fx * position.x + -fy * position.y + -fz * position.z),
      1
    ]);

    // Projection Matrix: FOVと画像アスペクト比を使用
    // ただし、feature mapは正方形なので、画像のアスペクト比を考慮して調整
    const fovRad = fov * Math.PI / 180;
    const imageAspect = imageWidth / imageHeight;
    const f = 1 / Math.tan(fovRad / 2);
    const near = 0.01;
    const far = 100;

    // 画像アスペクト比を使用（feature mapは正方形だが、投影は元画像のアスペクトで）
    const projMatrix = new Float32Array([
      f / imageAspect, 0, 0, 0,
      0, f, 0, 0,
      0, 0, (far + near) / (near - far), -1,
      0, 0, (2 * far * near) / (near - far), 0
    ]);

    console.log('[ImageEncoder] Built camera params from config:', {
      position: [position.x, position.y, position.z],
      target: [target.x, target.y, target.z],
      fov,
      imageAspect: imageAspect.toFixed(3)
    });

    return {
      viewMatrix,
      projMatrix,
      screenWidth: featureMapSize,
      screenHeight: featureMapSize
    };
  }

  /**
   * ソースカメラ設定を使用した特徴抽出（GUAVA論文準拠）
   * カメラJSONファイルを自動読み込み
   */
  async extractFeaturesWithSourceCamera(
    imageUrl: string,
    cameraJsonUrl: string,
    vertices: Float32Array,
    vertexCount: number,
    featureDim: number = 128
  ): Promise<{ projectionFeature: Float32Array; idEmbedding: Float32Array }> {
    // カメラ設定を読み込み
    const cameraConfig = await this.loadSourceCameraConfig(cameraJsonUrl);

    const FEATURE_MAP_SIZE = 256;
    const camera = this.buildCameraParamsFromConfig(cameraConfig, FEATURE_MAP_SIZE);

    // 既存のextractFeaturesを呼び出し
    return this.extractFeatures(imageUrl, vertices, vertexCount, camera, featureDim);
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
