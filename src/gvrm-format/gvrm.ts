// gvrm.ts
// GUAVA-based 3D Avatar Generator with ONNX Runtime

import * as THREE from 'three';
import * as ort from 'onnxruntime-web';
import { ImageEncoderONNX } from './image-encoder';

export interface GVRMConfig {
  canvasElement: HTMLCanvasElement;
  assetsPath?: string;
}

export class GVRM {
  private canvas: HTMLCanvasElement;
  private assetsPath: string;
  
  // Three.js
  private scene: THREE.Scene | null = null;
  private camera: THREE.PerspectiveCamera | null = null;
  private renderer: THREE.WebGLRenderer | null = null;
  private avatarMesh: THREE.Mesh | null = null;
  
  // ONNX Sessions
  private templateDecoderSession: ort.InferenceSession | null = null;
  private uvPointDecoderSession: ort.InferenceSession | null = null;
  private refinerSession: ort.InferenceSession | null = null;
  
  // Image Encoder
  private imageEncoder: ImageEncoderONNX | null = null;
  
  // Geometry Data
  private vTemplate: Float32Array | null = null;
  private uvCoord: Float32Array | null = null;
  private vertexBaseFeature: Float32Array | null = null;
  private vertexCount: number = 10595;
  
  // State
  private initialized: boolean = false;
  private assetsLoaded: boolean = false;
  
  constructor(config: GVRMConfig) {
    this.canvas = config.canvasElement;
    this.assetsPath = config.assetsPath || '/assets';
  }
  
  /**
   * 基本初期化（Three.jsセットアップ）
   */
  async init(): Promise<void> {
    if (this.initialized) {
      console.warn('[GVRM] Already initialized');
      return;
    }
    
    const startTime = performance.now();
    console.log('[GVRM] Initializing Three.js...');
    
    try {
      this.initThreeJS();
      this.initialized = true;
      const elapsed = performance.now() - startTime;
      console.log(`[GVRM] ✅ Basic initialization complete (${elapsed.toFixed(2)}ms)`);
    } catch (error) {
      console.error('[GVRM] ❌ Initialization failed:', error);
      this.initialized = false;
      throw error;
    }
  }
  
  /**
   * アセット読み込み（詳細ログ付き）
   */
  async loadAssets(plyPath?: string, imagePath?: string): Promise<boolean> {
    // 🔧 init()が呼ばれていない場合は自動的に呼ぶ
    if (!this.initialized) {
      console.log('[GVRM] Auto-initializing (init() was not called)...');
      await this.init();
    }
    
    if (this.assetsLoaded) {
      console.warn('[GVRM] Assets already loaded');
      return true;
    }
    
    const totalStartTime = performance.now();
    console.log('[GVRM] 📦 Starting asset loading...');
    
    try {
      // 1. Image Encoder初期化
      console.log('[GVRM] 🔧 Step 1/3: Loading Image Encoder...');
      const encoderStartTime = performance.now();
      this.imageEncoder = new ImageEncoderONNX();
      await this.imageEncoder.init();
      const encoderElapsed = performance.now() - encoderStartTime;
      console.log(`[GVRM] ✅ Image Encoder loaded (${encoderElapsed.toFixed(0)}ms)`);
      
      // 2. ジオメトリデータ読み込み
      console.log('[GVRM] 🔧 Step 2/3: Loading geometry data...');
      const geometryStartTime = performance.now();
      await this.loadGeometryData();
      const geometryElapsed = performance.now() - geometryStartTime;
      console.log(`[GVRM] ✅ Geometry data loaded (${geometryElapsed.toFixed(0)}ms)`);
      
      // 3. ONNX モデル読み込み
      console.log('[GVRM] 🔧 Step 3/3: Loading ONNX models...');
      const onnxStartTime = performance.now();
      await this.loadONNXModels();
      const onnxElapsed = performance.now() - onnxStartTime;
      console.log(`[GVRM] ✅ ONNX models loaded (${onnxElapsed.toFixed(0)}ms)`);
      
      this.assetsLoaded = true;
      const totalElapsed = performance.now() - totalStartTime;
      
      console.log(`[GVRM] ✅ All assets loaded successfully!`);
      console.log(`[GVRM] ⏱️  Total time: ${(totalElapsed/1000).toFixed(1)}s`);
      
      // 🎯 アセット読み込み後、自動的にデフォルトアバターを生成
      const sourceImage = imagePath || '/assets/source.png';
      console.log(`[GVRM] 🎨 Generating default avatar from: ${sourceImage}`);
      try {
        await this.generateAvatar(sourceImage);
      } catch (genError) {
        console.error('[GVRM] ❌ Failed to generate default avatar:', genError);
        // アバター生成失敗してもloadAssetsは成功として返す
      }
      
      return true;
    } catch (error) {
      console.error('[GVRM] ❌ Asset loading failed:', error);
      this.assetsLoaded = false;
      return false;
    }
  }
  
  /**
   * Three.js初期化
   */
  private initThreeJS(): void {
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x1a1a1a);
    
    this.camera = new THREE.PerspectiveCamera(
      45,
      this.canvas.width / this.canvas.height,
      0.01,
      100
    );
    this.camera.position.set(0, 1.4, 2.5);
    this.camera.lookAt(0, 1.4, 0);
    
    this.renderer = new THREE.WebGLRenderer({
      canvas: this.canvas,
      antialias: true,
      alpha: false
    });
    this.renderer.setSize(this.canvas.width, this.canvas.height);
    this.renderer.setPixelRatio(window.devicePixelRatio);
    
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.6);
    this.scene.add(ambientLight);
    
    const directionalLight = new THREE.DirectionalLight(0xffffff, 0.8);
    directionalLight.position.set(1, 2, 1);
    this.scene.add(directionalLight);
    
    console.log('[GVRM] Three.js initialized');
  }
  
  /**
   * ジオメトリデータ読み込み
   */
  private async loadGeometryData(): Promise<void> {
    const loadBinary = async (filename: string): Promise<Float32Array> => {
      const startTime = performance.now();
      const response = await fetch(`${this.assetsPath}/${filename}`);
      if (!response.ok) {
        throw new Error(`Failed to load ${filename}: ${response.status}`);
      }
      const buffer = await response.arrayBuffer();
      const elapsed = performance.now() - startTime;
      const sizeMB = (buffer.byteLength / 1024 / 1024).toFixed(2);
      console.log(`[GVRM]   ✓ ${filename} (${sizeMB} MB, ${elapsed.toFixed(0)}ms)`);
      return new Float32Array(buffer);
    };
    
    [this.vTemplate, this.uvCoord, this.vertexBaseFeature] = await Promise.all([
      loadBinary('v_template.bin'),
      loadBinary('uv_coord.bin'),
      loadBinary('vertex_base_feature.bin')
    ]);
    
    console.log('[GVRM] Geometry loaded:', {
      vertices: this.vTemplate.length / 3,
      uvCoords: this.uvCoord.length / 2,
      baseFeatures: this.vertexBaseFeature.length / 128
    });
  }
  
  /**
   * ONNXモデル読み込み
   */
  private async loadONNXModels(): Promise<void> {
    const loadONNX = async (filename: string): Promise<ort.InferenceSession> => {
      const path = `${this.assetsPath}/${filename}`;
      const startTime = performance.now();
      console.log(`[GVRM]   Loading ${filename}...`);
      const session = await ort.InferenceSession.create(path);
      const elapsed = performance.now() - startTime;
      console.log(`[GVRM]   ✓ ${filename} (${elapsed.toFixed(0)}ms)`);
      return session;
    };
    
    [
      this.templateDecoderSession,
      this.uvPointDecoderSession,
      this.refinerSession
    ] = await Promise.all([
      loadONNX('template_decoder.onnx'),
      loadONNX('uv_point_decoder.onnx'),
      loadONNX('refiner_websafe_v1.onnx')
    ]);
  }
  
  /**
   * ソース画像から3Dアバター生成
   */
  async generateAvatar(imageUrl: string): Promise<void> {
    if (!this.initialized) {
      throw new Error('[GVRM] Not initialized. Call init() first.');
    }
    
    if (!this.assetsLoaded) {
      throw new Error('[GVRM] Assets not loaded. Call loadAssets() first.');
    }
    
    console.log('[GVRM] Generating avatar from:', imageUrl);
    
    try {
      const startTime = performance.now();
      
      console.log('[GVRM] Step 1: Feature extraction...');
      const { projectionFeature, idEmbedding } = await this.extractFeatures(imageUrl);
      
      console.log('[GVRM] Step 2: Template decoding...');
      const templateResult = await this.runTemplateDecoder(
        projectionFeature,
        idEmbedding
      );
      
      console.log('[GVRM] Step 4: Refining...');
      const refinedImage = await this.runRefiner(
        templateResult.latent32ch,
        idEmbedding
      );
      
      console.log('[GVRM] Step 5: Creating 3D mesh...');
      this.createAvatarMesh(templateResult, refinedImage);
      
      const elapsed = performance.now() - startTime;
      console.log(`[GVRM] ✅ Avatar generation completed in ${elapsed.toFixed(2)}ms`);
      
      this.startRenderLoop();
      
    } catch (error) {
      console.error('[GVRM] ❌ Avatar generation failed:', error);
      throw error;
    }
  }
  
  private async extractFeatures(imageUrl: string): Promise<{
    projectionFeature: Float32Array;
    idEmbedding: Float32Array;
  }> {
    if (!this.imageEncoder) {
      throw new Error('[GVRM] Image Encoder not initialized');
    }
    
    const camera = {
      viewMatrix: this.buildViewMatrix(),
      projMatrix: this.buildProjectionMatrix(),
      screenWidth: 256,
      screenHeight: 256
    };
    
    return await this.imageEncoder.extractFeatures(
      imageUrl,
      this.vTemplate!,
      this.vertexCount,
      camera,
      128
    );
  }
  
  private buildViewMatrix(): Float32Array {
    const position = new THREE.Vector3(0, 0, 2.5);
    const target = new THREE.Vector3(0, 0, 0);
    const up = new THREE.Vector3(0, 1, 0);
    
    const camera = new THREE.PerspectiveCamera();
    camera.position.copy(position);
    camera.lookAt(target);
    camera.up.copy(up);
    camera.updateMatrixWorld();
    
    const viewMatrix = new THREE.Matrix4();
    viewMatrix.copy(camera.matrixWorldInverse);
    
    return new Float32Array(viewMatrix.elements);
  }
  
  private buildProjectionMatrix(): Float32Array {
    const fov = 45 * Math.PI / 180;
    const aspect = 1.0;
    const near = 0.01;
    const far = 100;
    
    const camera = new THREE.PerspectiveCamera(
      fov * 180 / Math.PI,
      aspect,
      near,
      far
    );
    camera.updateProjectionMatrix();
    
    return new Float32Array(camera.projectionMatrix.elements);
  }
  
  private async runTemplateDecoder(
    projectionFeature: Float32Array,
    idEmbedding: Float32Array
  ): Promise<{
    latent32ch: Float32Array;
    opacity: Float32Array;
    scale: Float32Array;
    rotation: Float32Array;
  }> {
    if (!this.templateDecoderSession) {
      throw new Error('[GVRM] Template Decoder not loaded');
    }
    
    const N = this.vertexCount;
    const combined = new Float32Array(N * 512);
    
    for (let i = 0; i < N; i++) {
      for (let j = 0; j < 128; j++) {
        combined[i * 512 + j] = projectionFeature[i * 128 + j];
      }
      for (let j = 0; j < 128; j++) {
        combined[i * 512 + 128 + j] = this.vertexBaseFeature![i * 128 + j];
      }
      for (let j = 0; j < 256; j++) {
        combined[i * 512 + 256 + j] = idEmbedding[j];
      }
    }
    
    const feeds = {
      'combined_features': new ort.Tensor('float32', combined, [N, 512])
    };
    
    const results = await this.templateDecoderSession.run(feeds);
    
    return {
      latent32ch: results['latent_32ch'].data as Float32Array,
      opacity: results['opacity'].data as Float32Array,
      scale: results['scale'].data as Float32Array,
      rotation: results['rotation'].data as Float32Array
    };
  }
  
  private async runRefiner(
    coarseFM: Float32Array,
    idEmbedding: Float32Array
  ): Promise<Float32Array> {
    if (!this.refinerSession) {
      throw new Error('[GVRM] Refiner not loaded');
    }
    
    const feeds = {
      'coarse_fm': new ort.Tensor('float32', coarseFM, [1, 32, 256, 256]),
      'id_emb': new ort.Tensor('float32', idEmbedding, [1, 256])
    };
    
    const results = await this.refinerSession.run(feeds);
    const refinedRGB = results['refined_rgb'].data as Float32Array;
    
    return refinedRGB;
  }
  
  private createAvatarMesh(
    templateResult: any,
    refinedImage: Float32Array
  ): void {
    if (!this.scene) return;
    
    if (this.avatarMesh) {
      this.scene.remove(this.avatarMesh);
      this.avatarMesh.geometry.dispose();
      (this.avatarMesh.material as THREE.Material).dispose();
    }
    
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(this.vTemplate!, 3));
    geometry.setAttribute('uv', new THREE.BufferAttribute(this.uvCoord!, 2));
    
    const textureData = new Uint8Array(256 * 256 * 4);
    for (let i = 0; i < 256 * 256; i++) {
      textureData[i * 4 + 0] = Math.floor(refinedImage[i] * 255);
      textureData[i * 4 + 1] = Math.floor(refinedImage[256 * 256 + i] * 255);
      textureData[i * 4 + 2] = Math.floor(refinedImage[256 * 256 * 2 + i] * 255);
      textureData[i * 4 + 3] = 255;
    }
    
    const texture = new THREE.DataTexture(textureData, 256, 256, THREE.RGBAFormat);
    texture.needsUpdate = true;
    
    const material = new THREE.MeshStandardMaterial({
      map: texture,
      side: THREE.DoubleSide
    });
    
    this.avatarMesh = new THREE.Mesh(geometry, material);
    this.scene.add(this.avatarMesh);
    
    console.log('[GVRM] Avatar mesh created');
  }
  
  private startRenderLoop(): void {
    const animate = () => {
      requestAnimationFrame(animate);
      
      if (this.avatarMesh) {
        this.avatarMesh.rotation.y += 0.005;
      }
      
      if (this.renderer && this.scene && this.camera) {
        this.renderer.render(this.scene, this.camera);
      }
    };
    
    animate();
  }
  
  updateLipSync(level: number | Float32Array | null): void {
    // 将来的にリップシンク実装
  }
  
  dispose(): void {
    if (this.imageEncoder) {
      this.imageEncoder.dispose();
    }
    
    if (this.templateDecoderSession) {
      this.templateDecoderSession.release();
    }
    
    if (this.uvPointDecoderSession) {
      this.uvPointDecoderSession.release();
    }
    
    if (this.refinerSession) {
      this.refinerSession.release();
    }
    
    if (this.renderer) {
      this.renderer.dispose();
    }
    
    if (this.scene) {
      this.scene.clear();
    }
    
    this.initialized = false;
    this.assetsLoaded = false;
    console.log('[GVRM] Disposed');
  }
}