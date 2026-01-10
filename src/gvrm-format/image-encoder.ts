// image-encoder.ts
// DINOv2 + ONNX Encoder for GUAVA

import { AutoProcessor, AutoModel } from '@huggingface/transformers';
import * as ort from 'onnxruntime-web';

export class ImageEncoderONNX {
  private dinoProcessor: any = null;
  private dinoModel: any = null;
  private encoderSession: ort.InferenceSession | null = null;
  private initialized: boolean = false;

  async init(): Promise<void> {
    if (this.initialized) return;

    const totalStartTime = performance.now();
    console.log('[ImageEncoder] 🚀 Starting initialization...');

    try {
      const modelId = 'Xenova/dinov2-base';
      
      // DINOv2読み込み
      console.log('[ImageEncoder] 📥 Downloading DINOv2 model from HuggingFace...');
      console.log('[ImageEncoder]    This may take 10-30s on first run (cached after)');
      const dinoStartTime = performance.now();
      
      this.dinoProcessor = await AutoProcessor.from_pretrained(modelId);
      this.dinoModel = await AutoModel.from_pretrained(modelId, {
        dtype: {
          embed_tokens: 'fp32',
          vision_model: 'fp32'
        },
        device: 'wasm'
      });
      
      const dinoElapsed = performance.now() - dinoStartTime;
      console.log(`[ImageEncoder] ✅ DINOv2 loaded (${(dinoElapsed/1000).toFixed(1)}s)`);

      // DINO Encoder ONNX読み込み
      console.log('[ImageEncoder] 📥 Loading DINO Encoder ONNX (50.72 MB)...');
      const onnxStartTime = performance.now();
      this.encoderSession = await ort.InferenceSession.create('/assets/dino_encoder.onnx');
      const onnxElapsed = performance.now() - onnxStartTime;
      console.log(`[ImageEncoder] ✅ DINO Encoder ONNX loaded (${(onnxElapsed/1000).toFixed(1)}s)`);

      this.initialized = true;
      const totalElapsed = performance.now() - totalStartTime;
      
      console.log(`[ImageEncoder] ✅ Initialization complete!`);
      console.log(`[ImageEncoder] ⏱️  Total time: ${(totalElapsed/1000).toFixed(1)}s`);
      console.log(`[ImageEncoder] ⏱️  Breakdown:`);
      console.log(`  🤖 DINOv2:         ${(dinoElapsed/1000).toFixed(1)}s (${(dinoElapsed/totalElapsed*100).toFixed(0)}%)`);
      console.log(`  🧠 DINO Encoder:   ${(onnxElapsed/1000).toFixed(1)}s (${(onnxElapsed/totalElapsed*100).toFixed(0)}%)`);
      
    } catch (error) {
      console.error('[ImageEncoder] ❌ Failed to initialize:', error);
      throw error;
    }
  }

  async extractFeatures(
    imageUrl: string,
    vertices: Float32Array,
    vertexCount: number,
    camera: {
      viewMatrix: Float32Array;
      projMatrix: Float32Array;
      screenWidth: number;
      screenHeight: number;
    },
    featureDim: number
  ): Promise<{
    projectionFeature: Float32Array;
    idEmbedding: Float32Array;
  }> {
    if (!this.initialized) {
      throw new Error('[ImageEncoder] Not initialized');
    }

    console.log('[ImageEncoder] 🎨 Extracting features from image...');
    const startTime = performance.now();

    // 画像読み込み
    const img = await this.loadImage(imageUrl);
    
    // DINOv2処理
    const inputs = await this.dinoProcessor(img);
    const outputs = await this.dinoModel(inputs);
    
    // 特徴抽出 [1, 768, 37, 37]
    const dinoFeatures = outputs.last_hidden_state.data as Float32Array;
    
    // ONNX Encoderで変換
    const feeds = {
      'dino_features': new ort.Tensor('float32', dinoFeatures, [1, 768, 37, 37])
    };
    
    const results = await this.encoderSession!.run(feeds);
    const appearanceFeature = results['appearance_feature'].data as Float32Array;
    
    // 頂点ごとの特徴をプロジェクション
    const projectionFeature = this.projectToVertices(
      appearanceFeature,
      vertices,
      vertexCount,
      camera,
      featureDim
    );
    
    // ID embedding（簡易版：平均プーリング）
    const idEmbedding = new Float32Array(256);
    for (let i = 0; i < 256; i++) {
      let sum = 0;
      for (let j = 0; j < 256; j++) {
        sum += appearanceFeature[i * 256 * 256 + j * 256 + 128];
      }
      idEmbedding[i] = sum / 256;
    }
    
    const elapsed = performance.now() - startTime;
    console.log(`[ImageEncoder] ✅ Features extracted (${elapsed.toFixed(0)}ms)`);
    
    return { projectionFeature, idEmbedding };
  }

  private async loadImage(url: string): Promise<HTMLImageElement> {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.crossOrigin = 'anonymous';
      img.onload = () => resolve(img);
      img.onerror = reject;
      img.src = url;
    });
  }

  private projectToVertices(
    featureMap: Float32Array,
    vertices: Float32Array,
    vertexCount: number,
    camera: any,
    featureDim: number
  ): Float32Array {
    const projected = new Float32Array(vertexCount * featureDim);
    
    // 簡易実装：各頂点を画面座標に投影してfeature mapから取得
    for (let i = 0; i < vertexCount; i++) {
      const vx = vertices[i * 3];
      const vy = vertices[i * 3 + 1];
      const vz = vertices[i * 3 + 2];
      
      // 簡易プロジェクション（中心をサンプリング）
      const u = Math.floor(128);
      const v = Math.floor(128);
      
      for (let c = 0; c < featureDim; c++) {
        projected[i * featureDim + c] = featureMap[c * 256 * 256 + v * 256 + u];
      }
    }
    
    return projected;
  }

  dispose(): void {
    this.dinoProcessor = null;
    this.dinoModel = null;
    
    if (this.encoderSession) {
      this.encoderSession.release();
      this.encoderSession = null;
    }
    
    this.initialized = false;
    console.log('[ImageEncoder] Disposed');
  }
}